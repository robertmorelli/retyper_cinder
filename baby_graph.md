# Baby Graph

A recipe for a small, declarative family of type binders.

The binder has four pieces:

1. two type cells for each relevant syntax node,
2. a small graph of typed dependencies,
3. union-find for facts that are genuinely identical,
4. a fixpoint solver over types supplied by the language.

AST-specific rules construct the graph. They do not perform type checking while
walking the tree.

## Philosophy

**Every binding in the AST must flow from an annotation supplied by a
programmer.** The binder's job is not to rediscover types independently at each
syntax node; it constructs the smallest graph that carries those annotations to
every place they determine a type or type context. Library signatures and
language intrinsics can be treated as permanent annotations, while annotations
in the program are ordinary removable or replaceable sources.

## 1. Give each expression two cells

Every expression or declaration may own two independent cells:

```text
TYPE(node)          the type produced by the node
TYPE_CONTEXT(node)  the type demanded of the node by its surroundings
```

These must remain separate. For example, an expression may produce `int` while
being used in a context that accepts `object`, or produce a machine integer
while being assigned into a dynamically typed slot.

A type error is normally discovered at the boundary between these cells:

```text
TYPE(node)  must satisfy  TYPE_CONTEXT(node)
```

The language defines `satisfies(actual, expected)`. The graph only determines
where `actual` and `expected` came from.

## 2. Use one minimal edge form

An edge carries a type from a source cell to a destination cell through a
continuation:

```text
Edge(source, destination, continuation)
```

A continuation is a tiny immutable linked list with three shapes:

```text
SAME()            finish and copy the current type to the destination
ARG(index, cont)  select a type argument, then run cont
PROP(id, cont)    look up a numeric property, then run cont
```

`SAME()` is the terminal sentinel at the end of every continuation. It means
that no projections remain and the current type is the edge's result.

Examples:

```text
TYPE(value) --SAME()--> TYPE(target)
TYPE(object) --PROP(12, SAME())--> TYPE(attribute)
TYPE(optional_t) --ARG(1, SAME())--> TYPE(unwrapped_value)
```

Compound lookup is represented by nesting the linked continuation:

```text
TYPE(receiver)
    --PROP(method_id,
           ARG(callable_args,
               ARG(-1, SAME())))-->
TYPE(call_result)
```

The exact numeric conventions belong to the language. One useful convention is
that callable types contain an argument tuple, with nonnegative indices naming
parameters and `-1` naming the return type:

```text
CALLABLE[ARGS[t0, t1, ..., return_type]]
```

Property names should be interned to stable numeric IDs. That is an
implementation detail, but it makes edges compact and property lookup cheap.

Keep continuations declarative rather than storing arbitrary functions on
edges. A continuation can be hashed, memoized, serialized, compared,
invalidated, and printed in an error trace.

## 3. Use union-find only for real identity

Union-find merges cells that must denote the same unknown type:

```text
equate(left, right)
```

After merging, all edges refer to the representative of the equivalence class.
This is useful for aliases, shared generic variables, mutually constrained
signatures, and other language rules that establish equality rather than
one-way dependency.

Do not union every `SAME()` edge. `SAME()` flow may be directional: erasing or
changing a producer can affect a consumer without making the consumer an equal
source in the reverse direction. Use union-find for equality and graph edges
for flow.

If explanations matter, retain a separate reason for every union. Path
compression should not erase the evidence needed to explain why two cells were
made equal.

## 4. Treat annotations as the roots

All type flow begins at an annotation source record:

```text
Source(cell, type, origin)
```

Most are explicit annotations written by the program or its libraries. Facts
owned by the language can use the same representation as permanent synthetic
annotations, so literals, builtin signatures, constructor types, generic
parameters, and permanent dynamic contexts do not require a second mechanism.

An annotation can source both a declaration's type and the context imposed on
its initializer:

```text
annotation T -> TYPE(declaration)
annotation T -> TYPE_CONTEXT(initializer)
```

Removing an annotation disables its source records. It does not inject an
`Unknown` value throughout the graph; the solver should first discover whether
another surviving source still determines the affected cells.

## 5. Make syntax rules emit constraints

The AST walk should be a table of declarative rules. A rule creates cells,
sources, unions, and edges, but never recursively implements type semantics.

Representative rules follow.

### Highlight: inference is only an edge

Type inference needs no separate inference engine. If a value's type ultimately
flows from an annotation, inferring a binding from that value is just another
edge in the same graph:

```text
annotation -> ... -> TYPE(value) --SAME()--> TYPE(inferred_binding)
```

The binding acquires a type when the annotation reaches it during settlement.
Removing or changing the annotation naturally recomputes the inferred result.

### Highlight: narrowing and promotion can also be edges

A guard can create a branch-local **shadow annotation** and link it exactly like
an annotation written by the programmer. For `v is not None`, the condition is
the origin of a synthetic non-optional annotation, which flows only to the
program-point cells dominated by the successful guard:

```text
v is not None
    -> shadow annotation: NonNone[type of v]
    -> TYPE(v in guarded region)
```

Promotion uses the same construction: create a shadow annotation carrying the
promoted type and connect it to the reads for which that fact is valid. This
keeps narrowing in the ordinary graph while program-point cells prevent the
shadow annotation from leaking beyond its branch or lifetime.

### Assignment

For an inferred assignment:

```text
x = value

TYPE(value) --SAME()--> TYPE(x)
```

For a declared assignment:

```text
x: T = value

T -> TYPE(x)
T -> TYPE_CONTEXT(value)
```

### Name use

```text
TYPE(binding) --SAME()--> TYPE(read)
```

A flow-sensitive language should use the reaching binding at that program
point, not one global cell for the variable.

### Property access

```text
object.field

TYPE(object) --PROP(field_id, SAME())--> TYPE(object.field)
```

A property may resolve to data or to a callable type. Binding a method to its
receiver is part of the language's `PROP` semantics.

### Function call

For each positional argument `i`:

```text
TYPE(callee) --ARG(args_slot, ARG(i, SAME()))--> TYPE_CONTEXT(argument_i)
```

For the result:

```text
TYPE(callee) --ARG(args_slot, ARG(-1, SAME()))--> TYPE(call)
```

Keyword arguments, variadics, and defaults can use the same representation once
the call-shape rule maps them to logical parameter indices.

### Return

```text
TYPE(function) --ARG(args_slot, ARG(-1, SAME()))--> TYPE_CONTEXT(returned_expression)
```

### Containers and generics

```text
TYPE(container) --ARG(element_index, SAME())--> TYPE(element_read)
TYPE(container) --ARG(element_index, SAME())--> TYPE_CONTEXT(element_write)
```

The same operation handles `Option[T]`, tuples, mappings, iterators, and other
parameterized types when their layouts are known.

### Multiple producers

When several edges feed one cell, the language chooses how their values combine.
The usual operation is a least common supertype or union:

```text
merge(incoming projected types) -> destination type
```

Equality, merging, and contextual compatibility are separate operations. They
should not be hidden inside additional AST visitors.

## 6. Resolve the graph to a fixpoint

The solver repeatedly performs four operations:

1. read currently available source values,
2. evaluate edge continuations whose input types are known,
3. merge all contributions to destination cells,
4. schedule dependents whose inputs changed.

It stops when no cell changes. Strongly connected components may be solved as
units, while ordinary acyclic regions need only one forward evaluation.

Types should form a finite-height lattice, or the implementation should enforce
another convergence rule. At minimum, distinguish:

```text
UNRESOLVED  no answer yet
UNKNOWN     a valid gradual/dynamic answer
ERROR       an answer exists but violates a constraint
```

`UNRESOLVED` must not be confused with the language's dynamic type. Keeping
`ERROR` as a propagating value allows the binder to continue after local
failures and report several useful diagnostics from one pass.

## 7. Integrate type-directed rewriting into iteration

Some syntax cannot be lowered until operand types become available. Operators,
indexing protocols, iteration protocols, and similar conveniences should first
emit delayed requests, then be expanded into their callable/property graph once
the required receiver types are known.

For example, a binary operator can eventually become property lookup plus a
normal call:

```text
left + right
    -> TYPE(left) --PROP(add_id, SAME())--> TYPE(callee)
    -> normal call constraints for right and the result
```

The expansion participates in the same work queue and fixpoint. It is not a
separate binder pass that commits permanently before types are known.

## 8. Represent flow with program-point cells

One cell per variable is sufficient only for flow-insensitive languages. For
narrowing and branch-sensitive assignment, construct cells per definition or
program point:

```text
TYPE(x at entry)
TYPE(x in true branch)
TYPE(x after join)
```

Branches feed join cells through normal edges. A narrowing rule introduces a
new source or projection for the narrowed program-point cell without mutating
the earlier one.

SSA is a convenient construction, not a requirement. The essential property is
that two reads which may have different types do not share one cell merely
because they have the same spelling.

## 9. Keep an escape hatch small

A language may expose a `worker_for` callback for operations that cannot be
expressed as projection continuations, such as complex overload selection or
literal-dependent rules. Workers should observe cells and emit values or new
declarative constraints through the normal work queue; keeping them pure,
memoizable, and dependency-tracked prevents the binder from degenerating into
an ad hoc callback interpreter.

## 10. Derive diagnostics from provenance

Every source, edge, union, merge, and failed projection should retain an origin:

```text
syntax location
rule identifier
previous provenance
```

When `TYPE(node)` does not satisfy `TYPE_CONTEXT(node)`, walk the two provenance
chains backward. This naturally produces a blame trace such as:

```text
argument has Widget
  because local x received constructor Widget(...)
parameter expects Path
  because call resolved receiver.open to Callable[[Path], File]
```

Prefer the shortest or highest-confidence path when many explanations exist.
Provenance is also useful for debugging rules and visualizing the graph.

## 11. Make incrementality structural

Give syntax nodes, cells, sources, and rules stable IDs. When a file changes:

1. rebuild constraints only for the changed syntax region,
2. remove source and edge contributions owned by invalidated rules,
3. invalidate their dependent cells,
4. resume the existing work queue.

Interned property IDs, immutable continuations, compact union-find
representatives, and memoized continuation evaluation make this architecture
suitable for interactive use.

## Minimal public interface

A binder in this family can expose a small API:

```python
graph = build_constraints(tree, language_rules)
result = graph.solve()

result.type_of(node)
result.context_of(node)
result.errors()
result.explain(node)
```

The implementation may optimize aggressively underneath this interface, but
its conceptual pipeline remains:

```text
syntax rules
    -> sources + unions + projection edges
    -> fixpoint
    -> types, contexts, errors, and explanations
```

## Design test

A feature belongs in the declarative core when it can be described as one of:

- introducing a type source,
- equating two cells,
- projecting a type with `SAME()`, `PROP(id, cont)`, or `ARG(index, cont)`,
- merging several incoming types,
- checking a produced type against a demanded context.

If most language features fit those operations, the binder remains a baby
graph: small enough to understand, but powerful enough to carry the majority of
modern type dependencies.

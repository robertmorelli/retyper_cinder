# Moving the remaining ad-hoc logic into the graph

This is a cleanup plan for the current detyper. It is not a proposal for a new
standalone type system. The goal is to make the graph the one place that says
where a post-erasure type or context comes from, and then delete parallel sets,
scans, and repair passes.

## Rule of thumb

A fact belongs in the graph when all three are true:

1. it has a specific producer,
2. it affects a specific type or context slot, and
3. erasing a source can turn it on or off.

Keep language operations such as `element_type`, operator lookup, and type join
as transfer functions. An edge names the inputs to a rule; it does not need to
contain the rule itself.

Use one edge record throughout:

```python
Edge(src, dst, role, detail=None)
```

`src` and `dst` are vertices such as `(node, TYPE)` and `(node, CTX)`. A small
number of fixed source vertices are useful for facts that do not originate at
an AST expression, for example `DYNAMIC_CONTEXT` and an imported signature.
The same edge list should drive typedness, type transfer, and explanation.

## Easy conversions

### 1. `dyn_ctx` becomes ordinary source edges

Today `GraphBuilder.expect_dynamic()` writes nodes into a side set and
`_settle_ctxs()` checks that set before considering graph demanders.

Instead, add one permanent source vertex and an edge:

```text
DYNAMIC_CONTEXT --CONTEXT--> (value, CTX)
```

The source carries `dynamic` and is never erasable. `List`, `Tuple`, `Slice`,
`Starred`, and `FormattedValue` keep calling `expect_dynamic`; that helper now
adds an edge rather than filling a set. Delete `dyn_ctx` and its precedence rule
in `_settle_ctxs()`.

### 2. `elem_ctx` becomes a labelled demand edge

Today `elem_demand(container, value)` stores `value -> container` in a separate
dictionary. The context solver then calls `element_type` specially.

Represent it directly:

```text
(container, TYPE) --ELEM_CONTEXT--> (value, CTX)
```

The context transfer for `ELEM_CONTEXT` projects the element type. This handles
`append` and can also represent other element-consuming operations. Delete
`elem_ctx`.

### 3. External call contexts become signature sources

`external_contexts()` scans every call and preserves the old recorded context
for arguments whose callee cannot be resolved locally. That is a hidden source.

During graph construction, create a fixed source for each recorded external
argument context:

```text
ExternalContext(call, argument_index, recorded_type)
    --CONTEXT--> (argument, CTX)
```

Keyword arguments use their keyword name as the detail. These sources survive
annotation erasure. Delete the later whole-tree `external_contexts()` scan.
This does not require modelling external signatures; it only makes the fact the
current code already trusts explicit.

### 4. A declaration's own context becomes a source record

`settle()` currently receives `declarations` separately and seeds `(node, CTX)`
when the declaration survives.

Give an annotation source both outputs:

```text
AnnotationSource(annotation)
    --TYPE_SOURCE--> (declaration, TYPE)
AnnotationSource(annotation)
    --CONTEXT_SOURCE--> (declaration, CTX)
```

Erasure removes that one source and both consequences disappear. This removes
the special `declarations` seed expression without incorrectly preserving the
context through an edge from the declaration's type.

### 5. Erased declaration repair becomes the assignment boundary

`settle_tables()` builds `erased_decls`, lets the value type flow, and then boxes
a primitive declaration result. `_settle_ctxs()` separately makes the erased
slot dynamic.

The graph already knows both facts. The annotation source controls the target's
context; the RHS still controls the inferred local type. Keep those as separate
edges:

```text
(value, TYPE) --VALUE--> (target, TYPE)
(target, TYPE or annotation source) --CONTEXT--> (value, CTX)
```

When the annotation source is erased, the assignment boundary has dynamic
context. Record the required conversion on that boundary; the stored type is
the conversion result. This replaces the `erased_decls` repair with the same
box decision used for any primitive entering a dynamic position.

Do not make the target's type dynamic. The useful fact learned from the current
implementation is that `x: Any = int64(...)` has a dynamic slot but a narrowed,
boxed value at later reads.

### 6. Definition and declaration reads use explicit edges only

`visit_Name()` currently prefers `resolved_from`, then falls back to
`reverse_outflow`. `known_type_source()` also recognizes locally defined class
and function names by spelling.

Build all three forms once:

```text
(reaching store, TYPE) --BRANCH--> (read, TYPE)
(declaration, TYPE)    --VALUE-->  (read, TYPE)
(definition, TYPE)     --VALUE-->  (name load, TYPE)
```

`resolved_from` remains the authority for local reads. `reverse_outflow` is only
an input while building missing declaration edges. Class and function loads
point to their actual definition node rather than becoming sources because the
name appears in a `defined` set. The solver then needs no name-specific fallback.

### 7. Guard narrowing becomes an edge emitted during construction

`narrowed_reads()` performs a second tree scan and turns recognized reads into
sources. Keep its intentionally small syntax vocabulary, but emit the result
while constructing the graph:

```text
GuardFact(if_test, narrowed_type) --NARROWED--> (read, TYPE)
```

The guard fact is permanent because erasing annotations does not erase the
source-level guard. This change is about ownership, not adding more narrowing:
keep exactly the existing `isinstance`, `is not None`, `and`, and exiting-guard
shapes.

### 8. Natural literal types become fallback sources

`settled_type()` checks whether a literal's context survived and then calls
`natural_type()` as a repair. Make both candidate inputs visible:

```text
NaturalLiteral(node) --FALLBACK--> (literal, TYPE)
(literal, CTX)       --CONTEXTUAL_LITERAL--> (literal, TYPE)
```

The literal transfer chooses the contextual interpretation while a context is
present and otherwise uses the natural source. `natural_type()` remains a small
lookup, but it runs as the literal's transfer rule rather than as a post-solve
exception.

This is especially useful for `2: int64 -> int` and `[]: CheckedList[T] ->
list`, because the type change appears in the same dependency explanation as
every other change.

### 9. Coercions use resolved metadata, not repeated name tests

`COERCIONS` is consulted in source discovery, graph construction, transfer, and
`PatchAdder`. Classify a coercion call once during graph construction and attach
its kind to the call vertex:

```text
(operand, TYPE) --COERCE(box)--> (call, TYPE)
```

Later code asks the graph for the call's `COERCE` input. This removes repeated
AST/name matching and makes redundant author-written coercions an ordinary
transfer result: if input and output types are equal, the conversion is
identity.

Keep the current set of recognized coercions. This cleanup does not need a more
general call model.

### 10. Context fallback becomes an explicit self-demand

`_settle_ctxs()` currently distinguishes “a demander died” from “there was no
demander,” and in the second case returns the expression's own type.

Add the fallback edge at construction time:

```text
(node, TYPE) --SELF_CONTEXT--> (node, CTX)
```

Real context edges outrank `SELF_CONTEXT`; the fallback is selected only when
there is no live real demander. This preserves the important rule that an
unconstrained expression satisfies itself without keeping the special final
branch in `ctx_of()`.

### 11. Conversion choice belongs to a type-to-context boundary

`pick_patch` receives a node, its settled type, and its settled context. That is
already an edge calculation expressed as a function call.

Record one conversion result per value-use boundary:

```text
Conversion(actual_type, expected_type, needs_exact)
    = identity | box | construct | cast
```

`PatchAdder` should only materialize this result. `TowerSimplifier` should use
the same result after rebinding rather than reimplementing exceptions. Optional
narrowing and primitive-to-dynamic boxing remain rules in the one conversion
function.

This is the most valuable footprint reduction: graph settlement decides the
pair, one conversion function decides the operation, and both patching stages
only apply it.

## Things to leave as transfer functions

Do not try to turn these into piles of extra edges:

- `type_transfer.join`
- `type_transfer.element_type`
- builtin operator lookup in `dunder_table`
- `boxed_instance`
- natural-type lookup for a literal
- conversion selection from one actual/expected pair

They are small operations over edge inputs, not hidden dependency discovery.

## Suggested order

Each step should delete its old side channel in the same change.

1. Convert `dyn_ctx` and `elem_ctx` to labelled context edges.
2. Convert declaration and external context seeds to source vertices.
3. Add definition-to-load edges and remove name source heuristics.
4. Move natural literal selection into transfer.
5. Unify assignment conversion and erased-declaration boxing.
6. Make one conversion result serve both patch stages.
7. Move the existing guard recognizer into graph construction.

Stop if a conversion adds more machinery than it deletes. The target is fewer
representations of the same fact, not a more abstract graph library.

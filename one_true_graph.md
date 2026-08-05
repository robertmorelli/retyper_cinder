# One true graph

The detyper currently has several overlapping descriptions of binding:

- `roots` and annotation roots,
- `components`,
- `inflow`, `outflow`, and `reverse_outflow`,
- benchmark root groups,
- the typedness graph `g`,
- role-labelled `deps`,
- `dyn_ctx` and `elem_ctx`,
- `resolved_from`,
- source, declaration, and erased-node sets.

Most of these describe the same relation at different stages. The goal is one
`BindingGraph` built once from the bound AST and then queried for erasure,
settlement, patching, grouping, and tests.

This document only merges the structures the detyper already uses. It does not
propose a new standalone binder constraint system.

## The graph shape

A semantic vertex is:

```python
Vertex(node, slot)
```

where `slot` is `TYPE` or `CTX`. A few fixed source vertices represent facts
that have no producing expression, such as a permanent dynamic context or a
recorded external argument context.

An edge is:

```python
Edge(src, dst, role, detail=None)
```

The existing roles remain useful:

- `VALUE`, `BRANCH`
- `ELEM`, `RETURN`, `RECEIVER`, `FIELD`
- `BINOP`, `CMPOP`, `UNARY`
- `COERCE`, `ELTS`
- ordinary, element, dynamic, and fallback context roles

One edge list is indexed in both directions:

```python
graph.outgoing[vertex]
graph.incoming[vertex]
```

Typedness ignores most role details. Type and context transfer use them. No
separate `g` and `deps` need to be kept in sync.

## Source records

A source record says where a value enters and whether it can be erased:

```python
Source(vertex, value, erasure_root=None, kind=...)
```

Examples:

- an argument annotation,
- an annotated assignment,
- a function return annotation,
- `self` or `cls`,
- a literal's natural type,
- a constructor or builtin result,
- a fixed dynamic context,
- a recorded external-call context.

Settling a mask means enabling all sources except those whose `erasure_root` is
selected. Nothing else receives special knowledge of the mask.

An annotation source may feed both a declaration's type and its own context.
Erasing that one source therefore removes both facts without an
`erased_decls` repair list.

## Binding elements are graph-owned erasure groups

A binding element is not a second graph. It is a named set of erasable sources:

```python
ErasureUnit(id, sources, ast_nodes)
```

`ast_nodes` is retained because `AnnoRemover` needs to rewrite the corresponding
syntax. The semantic effect comes only from disabling `sources`.

The old `components` relation becomes union-find during graph construction:
connected co-erasure items receive the same unit id. After construction, store
only the resulting units; do not retain a node-to-every-other-node component
closure.

The existing meanings remain:

- **annotation unit:** the smallest selectable annotation/component unit,
- **benchmark unit:** a union of annotation units joined by the existing
  function grouping rule.

A mask always selects unit ids. `bench=True` merely chooses the benchmark-unit
view. This removes the current ambiguity between roots, annotation roots,
components, and benchmark roots.

## Reaching definitions become ordinary type edges

`resolved_from` is valuable binder instrumentation, but it need not remain a
runtime side table after graph construction.

For each read:

```text
(store_1, TYPE) --BRANCH--> (read, TYPE)
(store_2, TYPE) --BRANCH--> (read, TYPE)
```

A single reaching definition can use `VALUE`; using `BRANCH` for both is also
fine if transfer treats a one-arm join as identity. Loop-carried definitions
and branch merges require no additional solver path once these edges exist.

`reverse_outflow` supplies declaration edges only where there are no recorded
reaching definitions. It can then be discarded by the detyper.

## Inflow and outflow become edge indexes

The old tables have three uses:

1. finding what a declaration affects,
2. finding what constrains a declaration,
3. grouping items for co-erasure.

The first two are `outgoing` and `incoming` indexes over the one edge list. The
third is erasure-unit construction metadata. There is no need to expose all
three old tables after `BindingGraph.build()`.

`component_reflow` should become part of graph construction:

- loop-variable/iterator relations emit their semantic edges,
- member-access chains emit receiver, field, call, or element edges,
- co-removal relations union erasure units.

It should not mutate a parallel family of flow dictionaries.

## Settlement

A settlement is immutable output for one mask:

```python
Settlement:
    types: dict[AST, Value]
    contexts: dict[AST, Value]
    typed: set[Vertex]
    conversions: dict[Use, Conversion]
```

The operation is conceptually:

```python
settlement = graph.settle(disabled_units)
```

It performs:

1. enable surviving sources,
2. run the descending typedness fixpoint,
3. run role-labelled type transfer,
4. run context transfer,
5. choose conversions at type-to-context boundaries.

The existing greatest-fixpoint behavior must remain: a loop-carried cycle with
no dynamic input stays typed. Context vertices retain their existing
any-live-demander behavior, while combining type vertices require all relevant
inputs unless their transfer role says otherwise.

The solver should consume only graph indexes and source records. It should not
walk the AST to rediscover declarations, external contexts, narrowed reads, or
operands.

## Patching

Patching becomes deliberately uninteresting:

```python
remove_annotations(tree, graph.nodes_for(disabled_units))
apply_conversions(tree, settlement.conversions)
add_imports(tree)
```

A conversion is one of:

```text
identity
box
construct(T)
cast(T)
```

Existing source coercions and inserted coercions use the same representation.
If rebinding shows that a coercion's input already has its output type, its
conversion is identity and the wrapper is removed.

Stage two may remain while it is useful, but it should repeat the same operation
on a newly built graph rather than use a separate tower-specific decision tree.
This gives one patch shape instead of `PatchAdder` rules followed by partially
different `TowerSimplifier` rules.

## A small API

The intended public surface is small:

```python
bound = bind_ast(tree)                       # cinder tables and AST
bg = BindingGraph.build(bound)
units = bg.units("annotation" | "benchmark")
settled = bg.settle(selected_unit_ids)
out = detype_ast(bound.tree, bg, settled)
```

Tests should also use this API. Avoid returning another long tuple from
`get_ast_data`; give the bound result named fields during migration.

## Migration without a rewrite

### Step 1: one edge store

Make `GraphBuilder` add `Edge` objects and derive both current `g` and `deps`
indexes from them. Confirm settlements are unchanged. Then make the solvers read
the indexes directly and delete the duplicate structures.

### Step 2: absorb side context maps

Turn `dyn_ctx` and `elem_ctx` into labelled edges. Turn external and declaration
contexts into source records. Delete the side arguments from `settle_tables`.

### Step 3: absorb reaching-definition tables

Convert `resolved_from` and the needed part of `reverse_outflow` into edges at
build time. Stop passing either table beyond the builder.

### Step 4: own erasure units

Run the existing component and benchmark grouping while building the graph.
Store compact unit ids and source membership. Change `detyper.detype()` to mask
unit ids rather than reconstruct `all_items_to_remove` from roots and
components.

### Step 5: own conversion results

Move the actual/context decision into settlement. Reduce `PatchAdder` to
materializing conversions and make stage two call the same decision logic.

At every step, delete the replaced representation. A successful migration
should shorten `detyper.py`, reduce the `get_ast_data()` result surface, and
leave only one place to inspect why a node changed after erasure.

## Invariants

Keep these assertions cheap and always enabled in tests:

- every semantic dependency exists as exactly one edge,
- every edge appears in both indexes,
- every erasable source belongs to exactly one annotation unit,
- benchmark units are unions of annotation units,
- disabling a unit changes source availability only,
- every non-identity inserted coercion has one settled actual/context pair,
- no solver phase consults `components`, `inflow`, `outflow`, or
  `reverse_outflow` after graph construction.

“One true graph” means one stored fact and several indexes or views. It does not
mean forcing grouping, type transfer, and AST rewriting into the same algorithm.

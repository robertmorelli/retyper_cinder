# The explicit type/context dependency graph

A post-hoc model of what cinderx's `TypeBinder` concluded, and of what it *would*
conclude if some annotations were gone. Built from the fact tables the binder
already produces, not by re-implementing it.

Status: wired into `detyper.detype`, replacing `update_type_context_pairs`.
Against a real rebind of the erased program, over 152 partial maskings that
bind: **90.0% exact type, 87.4% exact context, 90.1% dyn-vs-not**. The detyper
binds 20 of 27 benchmarks under *full* erasure, and every partial mask in the
random crashtest. **Read [Handoff](#handoff) first** — it says what is actually
broken and what the numbers do and do not mean.

Measure it with `measure_types.py`: settle the tables for a masking, then erase
and re-bind for real, and diff the two node by node (paired by source position,
because the compiler rewrites the tree). Maskings whose erased program does not
bind are skipped — they have no ground truth.

---

## The idea, independent of Python or cinderx

Take any gradually typed language: some declarations carry types, some do not,
and a checker fills in the gaps. Now ask a question that checker was never built
to answer — *if I deleted this annotation, and only this one, what would you
conclude instead?*

You cannot get the answer by deleting it and re-running the checker, for two
reasons. The resulting program often does not check at all, so the checker
returns nothing rather than a partial answer. And re-running loses the
connection between the two runs: you get a new set of conclusions with no way to
say which old conclusion each one replaced.

The graph is a model of the checker's reasoning that can be *replayed under a
different set of assumptions*. Three ideas carry it:

**1. Types enter at a few places and flow everywhere else.** Every conclusion a
checker reaches is either read off a declaration, produced by a construct whose
type is fixed by the language (a literal, a constructor, a comparison), or
derived from other conclusions. The first two are *sources*; only the third is
inference. Model the derivations as edges and the sources as a table, and you
have separated the part erasure can change from the part it cannot.

**2. Erasure is deletion from that table, never injection.** The naive move is
to mark everything downstream of a deleted annotation as untyped. That is wrong
whenever the language would have recovered the type by other means — a
constructor on the right-hand side, a surviving declaration further along. The
right question is not "what got poisoned" but "what still has a surviving
source". Same graph, smaller seed set.

**3. Every position has two slots, not one.** A term has a type, and it has a
*context* — what the surrounding program demands of it. These are different
facts with different flow directions: types flow outward from sources toward
uses, contexts flow inward from declarations toward the terms that fill them.
Conflating them is the single most common way to get this wrong, and most of the
subtle bugs in this implementation were a fact landing in the wrong slot.

Given that, the analysis is: seed the surviving sources, propagate to a
fixpoint, and read off each position's type and context. A coercion is needed
exactly where a term's type no longer satisfies its context.

**What makes it worth doing** is that the answer is *relative*. A type checker
tells you what a program means. This tells you what a program would mean under a
counterfactual — which is what you need to attribute a cost to an individual
annotation, and what a checker, being all-or-nothing, cannot give you.

**Where it stops working** is the same place static reasoning always stops: when
a variable's type depends on *where you are* in the program rather than on the
program's text. One vertex per variable cannot express "int here, dynamic three
lines later". That is not a missing edge, it is a missing dimension, and the fix
is the standard one — split the variable into one vertex per program point.

**The nature of the rules is a language question, not an implementation one.**
Whether a literal's type comes from its context, whether an unannotated function
infers its return type from its body, whether a call argument constrains its
parameter or the reverse — every one of these is a decision the language made,
and the graph must mirror it. Get one backwards and the model is confidently
wrong. Most of the effort below was discovering which way each arrow points.

---

## Why it exists

`update_type_tables.update_type_context_pairs` is six lines:

```python
for anno in removed_annotations:
    for read in reads.get(anno) or []:
        types[read] = dyn
    for write in writes.get(anno) or []:
        type_contexts[write] = dyn
```

Erase an annotation, mark everything downstream dynamic. It is unconditional,
and that is the whole problem: it cannot ask whether the thing on the other end
of the write still has a type. When `a: Array[int64]` is erased but `a` still
points at a real `Array[int64]`, the element slot still demands `int64` — and
this code says the context is dynamic, so `patch_adder` emits `box(...)` where
the slot wanted a primitive.

The `_cast(Any, ...)` erasure wrap existed to make that assumption *true*: block
cinderx's inference so every erased variable really is `Any`. It worked, but it
measured the wrong thing — "annotation removed **and** inference defeated"
rather than "annotation removed". Erasing `xs: CheckedList[int] = []` without
the wrap gives `list`, which is what an honestly unannotated program would get.
The graph is what makes the wrap unnecessary: instead of forcing the assumption,
compute the answer.

## What it is

A vertex is `(ast node, is_ctx)`. `False` is the node's type slot, `True` is the
context imposed on it. Two conventions matter:

- for a `FunctionDef`, the type slot means its **return type**, so `Return` and
  the call-result edge share one vertex
- for an `arg`, it means the **parameter's declared type**

`g[v]` is the list of vertices `v` flows into. `graph_construction.GraphBuilder`
is a `NodeVisitor` with one `visit_X` per node kind, and it emits two structures
at once: `g`, the typedness graph, and `deps`, the same edges labelled with
*what kind of derivation* each one is. They are built together because they are
the same edges seen two ways — in separate passes an edge lands in one and not
the other.

Edges are built by four primitives:

| primitive | edge | meaning |
|---|---|---|
| `up(src, dst)` | `(src,TYPE) → (dst,TYPE)` | src's type derives dst's |
| `down(outer, inner)` | `(outer,CTX) → (inner,CTX)` | context flows inward |
| `down_from(src, dst)` | `(src,TYPE) → (dst,CTX)` | src's type *is* dst's context |
| `bind(value, target)` | both directions | assignment: infers **and** constrains |
| `demand(target, value)` | `(target,TYPE) → (value,CTX)` | declared type constrains, never infers |

`bind` vs `demand` is the distinction that took longest to get right, and it is
the one place a naive graph goes wrong. **Only a local assignment infers.**
`maybe_set_local_type` really does derive a local from its RHS. A call argument
does *not* determine its parameter's type — Static Python has no whole-program
inference from call sites — and an unannotated function binds `dynamic` rather
than inferring from its `return` statements. Fabricating those inbound type
edges made parameters look like they had sources when they did not.

## Roles, and turning typedness into a type

Typedness says *whether* a node still has a type. Choosing a coercion needs
*which* type, and a node that stays typed otherwise keeps whatever it was
recorded with before erasure — so `i < n` still claims `cbool` after `i` and `n`
have gone dynamic, and the detyper boxes something unboxable.

The fix is to label each edge with the role its source plays in the destination:

| role | meaning |
|---|---|
| `VALUE` | src's type is dst's type |
| `ELEM` | dst is an element drawn out of the container src |
| `RETURN` | src is the callee whose return type dst takes |
| `RECEIVER` | src only *gates* dst: dynamic receiver, dynamic result |
| `FIELD` | src is the declaration of the field dst reads |
| `BINOP` / `CMPOP` / `UNARY` | src is an operand; the operator decides the result |
| `BRANCH` | src is one arm of a merge: join, do not overwrite |

`type_transfer.transfer` then dispatches on the roles of a node's inflow. **A
role is deliberately not a per-edge lambda.** `a + b` cannot be resolved from
either operand alone, so the rule belongs to the destination node and the edge
only says which slot an input fills; and a closure captured at build time would
bake in the pre-erasure type, which is the exact failure being repaired.

Every rule defers to cinderx where cinderx has one. Merges call the same union
`TypeBinder._join` calls. Element types are read off the live `Value`. Operators
come from a generated table, below.

## `dunder_table` — the 2D operator table

`a op b` is the one place a result cannot be derived from one input. `int64 +
int64` is int64, `list * int` is list, `str % x` is str, and no rule keyed on
the operator alone or on either operand alone gets all three right.

Writing that table by hand would be a second implementation of Static Python's
promotion rules, and a second implementation is a second thing to be wrong. So
it is **generated**: for each pair of types, bind a probe module applying every
operator to a value of each, and read the answers out of `expr_types`. `bind` is
all-or-nothing, so a batch cinder rejects is retried one operator at a time and
the illegal combinations are simply absent — which is the right answer for them.

158 pairs × 18 operators, cached in `data/dunder_table.json`, ~28s to rebuild.
Rows are keyed by `type_descr` rather than by `Value`, because every bind builds
a fresh `Compiler` and no `Value` survives across one.

Worth knowing: this binder types `int + int` as `object`. That is not a bug in
the table — it is what the rebind we validate against also says, and recording
it faithfully is the whole point of generating rather than writing it.

## The solver

`graph_solve.settle` is an **AND fixpoint**, not reachability:

```
a vertex is typed iff it is a surviving source,
or every vertex feeding it is typed
```

AND is correct for every combining vertex: `BinOp`, `Compare`, `BoolOp`,
`IfExp`, container elements, and merges (joining `int` with `dynamic` gives
dynamic, which is what AND produces). There is no OR case. Because AND is not
reachability it has to iterate; typedness only grows from the sources, so it is
monotone and terminates. A dependency cycle with no source inside settles
dynamic, which is the conservative answer.

Two vertex kinds are **not** AND — they ignore their inflow entirely:

- **cut**: a surviving annotation overrides whatever flows into it. `x: int =
  <dynamic>` leaves `x` an `int`, because dynamic assigns to anything. This is
  the mechanism that makes partial masks behave differently from full erasure.
- **generative**: a type that enters without flowing along any edge.

## `known_type_source` — where types enter

The insight that unlocked the whole thing: **a large share of types are not
flowed, they are looked up.** The graph models flow between AST nodes in *this
module*, but function signatures, class field declarations and builtin return
types are not AST nodes in it. Repeatedly, the fix that felt like "a missing
edge" was a missing *vertex*.

Erasure **deletes rows from this table**. Nothing injects dynamic. That framing
is why the query "what survives" beats the query "what got poisoned".

Sources, per `graph_solve.known_type_source`:

- annotated `arg`, `returns`, `AnnAssign` targets — and `self`, which is never
  erased
- constructor calls and coercions: `T(...)`, `cast`, `box`, `int64`, `cbool`,
  `clen`, `double`
- constants and container literals — *a list is a list whatever is in it*
- `Name` loads with **no graph in-degree**: builtins, imports, defs. Nothing in
  the module can change them. The first attempt used "bound anywhere in the
  module", which *lost* two points; in-degree is the correct test.
- operator-determined results: `not x` is `bool` regardless of `x`; `Slice` is a
  slice; `[0] * n` and `str % x` are the sequence
- builtin/unresolvable call returns

Notably **`Compare` is not a source.** `x is None` is always `bool`, but
`Char1Glob == "A"` is dynamic when the operand is. Only `Is`/`IsNot` qualify.

## Reversion: context-supplied types

`settled_type` handles the case a boolean model cannot see on its own. `2` is
`int64` only because something asked for `int64`; `[]` is `chklist[int]` only
while an annotation says so. When a node's **context** vertex goes dynamic, a
literal falls back to its natural type.

This closed the entire exact-type gap. Before it, `chklist[int] → list` scored
as *agreement* — both sides said "typed" while the actual types differed. That
is exactly the `detype_failure.md` bug, invisible to a dyn-vs-not metric.

## Where it is nice

- **It needs no fork changes.** Everything above reads tables cinderx already
  produces. The `Value` objects in `expr_types` are live, so `resolve_attr` /
  `bind_call` can be called *after* the walk to get the same answers the binder
  had. `component_reflow.py` guesses at this syntactically — dumping all of
  `p.args` into inflow — while the resolved callee signature is sitting right
  there in the tables.
- **It is validated cheaply.** Any program that binds gives ground truth: settle
  the tables, diff against a real rebind. That is a standing regression check,
  and it is how the `cbool` bug was found by hand.
- **Erasure-as-deletion composes.** Partial masks are just a smaller source set;
  no separate code path.
- **Partial masks always bind.** 288/288. Only *full* erasure fails to bind, and
  that case is an artifact of testing, not something the experiment needs.

## Where it fails

Ranked by measured cost.

**0. Guard narrowing — recoverable, and done.** Not all flow sensitivity needs
the binder's state. Two shapes are written in the source and can be read
straight off the tree:

```python
if isinstance(x, T):      x.foo        # guarded inside the body
if not isinstance(x, T):  raise ...    # guarded *after* the statement
x.foo
```

The second is the common one -- an argument check that bails out -- and it
narrows the whole rest of the block. `narrowed_reads` handles both, plus
`x is not None` and `and`-chains of guards. This is the only narrowing the
post-hoc graph can see, and it is worth having precisely because it does not
need a fork change.

**1. Merge narrowing — the wall.** ~620 of ~880 residual errors. A variable's type
depends on the **program point**. `length` in chaos has three writes, some
dynamic; cinderx narrows per path, so it is `int` at one read and dynamic at
another. The two available settings bracket the truth and neither is right:

| | errors | |
|---|---|---|
| join edges on (every store reaches every load) | 573 | too pessimistic |
| join edges off (a read takes its declaration) | 882 | too optimistic |

One vertex per variable cannot represent this, no matter which edges are added.
It needs one vertex per `(variable, program point)` — SSA — and the merge facts
live in `maybe_set_local_type` / `local_types`, which are mutable walk state
discarded after the bind. **This is the only item that requires fork
instrumentation.**

It is not merely an accuracy problem: 6 of 13 detyper failures in one fuzz run
were `Optional[Variable]` narrowing. The same missing capability shows up in
both places.

**2. Element projection.** `a[i]` should carry the *element* type, not the
container's. Boolean typedness is unaffected — which is why the graph scored
99.5% while the detyper still failed on every `Array[int64]` store. Fixed for
subscript stores by demanding through the subscript node (whose recorded type is
already the element type) rather than through the container.

**3. Method references** have no declaration to point at, so their type follows
the receiver. Getting this wrong in either direction is expensive: the
unconditional receiver fallback cost 19 points at full erasure, and having no
rule at all cost 5 under partial masks.

## Where it is not powerful enough

**Swapping it into the detyper was a regression until roles landed.** Same
corpus, full detype, compile-only, 27 benchmarks:

| | binds | fails |
|---|---|---|
| `update_type_context_pairs` | 22 | 5 |
| `settle_tables`, typedness only | 18-19 | 8-9 |
| `settle_tables` + roles + transfer | **20** | 7 |

More to the point, the seven remaining failures are now seven *different*
errors — `can't box non-primitive`, `Literal[0] cannot be assigned to int64`,
`cannot subtract int and int64`, `Call argument cannot be a primitive` — where
before they were the same error repeated. Each is now an individual `pick_patch`
decision rather than one systematic table error.

The graph is *more accurate about dynamism* and *less useful to `patch_adder`*,
which is the result that matters. `pick_patch` branches on `is_primative(type)`
and `valid_pair(type, type_ctx)`; feeding it real types instead of blanket
dynamic changes which branch fires everywhere at once. Two consequences surfaced
immediately:

- assignment **targets** started getting wrapped (`box(i): Any = 0`, not valid
  Python) because targets finally had types and no guard existed — the old
  tables made everything dynamic so the case never arose. Fixed with a
  `Load`-context guard in `PatchAdder`.
- primitives assigned into erased declarations produced `int64(...)` where a
  `box(...)` was needed. Boxing on context reversion was tried and rejected: it
  fixed nothing in the detyper and cost 3 points on the graph metric, because an
  unconsumed `int64(x)` legitimately stays primitive until something uses it.
  The boxing decision belongs in `pick_patch`, not in the settled type.

**Author-written coercions are never revisited.** nqueens contains, in the
original benchmark:

```python
a: Array[int64] = Array[int64](box(size))
```

That `box(size)` is not something the detyper emitted. Erase `size: int64` and
`size` settles to `int`, so `box` now receives a non-primitive and the program
will not bind. `patch_adder` only *inserts* coercions; it has no notion of an
existing one whose operand changed underneath it. `tower_simplifier` would
collapse it, but stage two cannot run because stage one's output does not bind.

This is why the graph path fails where the old one did not: the old tables made
everything dynamic, so author-written coercions stayed accidentally valid. Being
right about `size` is what exposes them.

**The remaining failures oscillate rather than converge.** Each individual rule
fix is defensible in isolation, and each one changes *which* six benchmarks fail
rather than how many. That pattern is the diagnosis: `pick_patch` branches on
`is_primative(type)` and `valid_pair(type, ctx)` under the assumption that both
are usually dynamic. Handing it accurate types changes which branch fires
everywhere at once, and the coercion it picks is frequently the wrong one.

The honest reading: **the tables were never the bottleneck.** Stage one was
already measured at 99.6% accurate, and 101/101 failures in `failures.md` were
userspace bugs. A better table only pays off once `pick_patch` is rewritten to
consume it — treating existing coercions as re-decidable, and choosing box /
unbox / cast / nothing from the settled type rather than the recorded one.

## What cinderx gives you, and what it doesn't

**Available post-hoc, no fork needed:**

- `expr_types` / `expr_ctx_types` keyed by AST node, holding live `Value`s
- `constructors`, `components`, `inflow` / `outflow` / `reverse_outflow`
- class bodies and method definitions, reachable through the tables
- alignment across erasure: `get_ast_data` takes a *tree*, so binding an erased
  deep copy and pairing the two structurally beats round-tripping through
  `unparse`

**Not available, and the reason each hurts:**

- **narrowing state.** `local_types` merges at joins are gone after the walk.
  The wall above.
- **parameter types are in no node-keyed table.** `arg` nodes appear in neither
  `expr_types` nor `declared_types` — 0 of 15 in `call_method`. They live in the
  `Function` value's signature.
- **`declared_types` is the wrong side.** The fork records it when `decl_type is
  None`, i.e. locals *already* inferred, deliberately excluding annotated ones —
  which are exactly the set that becomes inferred after erasure. It covers 22.9%
  of Name-Store targets, and wiring the graph to it dropped agreement 80.8% →
  52.7%.
- **`bind()` is all-or-nothing.** It raises on the first violated constraint and
  returns no tables, so an erased program that does not typecheck yields nothing
  to compare against. This blocks full erasure on the 6 primitive-heavy
  `advanced` variants. It does *not* block partial masks, which is what the
  experiment actually runs.
- **`CIntType.boxed` hardcodes `int`**, so `cbool` boxes to `int` while the
  binder says `bool` — see `cbool_not_int_bro.md`. Worked around in
  `patch_picker.boxed_instance`.

## Traps worth remembering

**Each `get_ast_data` builds a fresh `Compiler`, so `DYNAMIC` is a different
object.** Any `is dyn` test across two binds is silently always false. This
produced a 0% reading in the first measurement, and it was live in `detyper.py`,
where stage one's `dyn` was passed into stage two and made every check in
`simplify_coercions` a no-op.

**Vertex vs node.** `settle`'s cut guard read `v[0] in sources`, testing the
*node*, which meant a node that was a type source had its **context** vertex
blocked from ever being derived. Every literal looked contextless and reverted:
`1` became `int` instead of `int64`. One-line fix, +3.1 points.

**Two vertices for one declaration.** `known_type_source` marked an AnnAssign's
*target*; `reverse_outflow` maps reads to the *AnnAssign node*. Never connected,
so `f: Foo = Foo()` looked dynamic at every read. One edge: 84.1% → 94.5%, the
single largest fix.

**Score expressions, not variables.** Variable-level scoring read 98.4% while
expression-level was 85.5% — 320 vertices out of 9,766.

**Full erasure is not the experiment.** Rules tuned for it diverge from partial
masks; the two currently sit at 89.2% and 99.1%.

## What would take it further

1. **Rewrite `pick_patch` to consume settled types.** The tables are ready; the
   consumer is not. Nothing else the graph can do matters until this happens.
2. **SSA vertices + `maybe_set_local_type` instrumentation.** The only fork
   change worth making, and it addresses both the residual accuracy and the
   `Optional` failure class.
3. **Element projection on subscript reads**, matching the store side.
4. **Granularity as an alternative to projection.** For `a: Array[int64] =
   Array[int64](n)`, treat the variable and its allocation as one atomic unit
   and rewrite the allocation on erasure — the same move `anno_remover` now
   makes for `xs: CheckedList[int] = []`. This measures something different
   ("the type is gone") than projection does ("the annotation is gone, the
   object is unchanged"), and which one is right is a question about the
   experiment, not about the implementation.


---

# Handoff

## The one thing to understand before touching anything

**Typedness and type are different problems, and this codebase solved the wrong
one first.** The solver decides, per vertex, whether a node is still typed after
erasure. It got very good at that -- ~99.5% against a real rebind. It bought the
detyper almost nothing, because `pick_patch` does not need to know *whether* a
node is typed. It needs to know *what type it is*, in order to choose between
`box`, `int64(...)`, `cast(T, ...)` and nothing.

A node that stays typed keeps whatever type it was recorded with before erasure.
So `i < n` still claims to be `cbool` after `i` and `n` have gone dynamic, and
the detyper boxes something unboxable. Every remaining detyper failure is this.

Type propagation is now implemented on labelled edges (`deps` out of
`GraphBuilder`, `type_transfer.transfer`, `dunder_table` for operators) and is
**measured at 90.0% exact**. What is left is the consumer: `pick_patch` still
chooses a coercion under assumptions that no longer hold, and the seven
remaining full-erasure failures are seven separate instances of that.

## What each piece is for

- `graph_construction.build_graph` -- topology. Vertices are `(node, is_ctx)`.
- `graph_solve.settle` -- which vertices stay typed. A **greatest** fixpoint.
- `graph_solve.settle_tables` -- the settled type and context tables, i.e. what
  `pick_patch` consumes.
- `type_transfer` -- one transfer function per node kind, dispatched on the
  roles of its inbound edges.
- `dunder_table` -- the generated 2D operator table, cached in `data/`.
- `dunder_resolver` -- operator resolution, split into user-defined (real
  `FunctionDef`s, ordinary graph edges) and builtin (a table, because the result
  depends on both operand types).
- `_cinderx` -- three instrumentation points, see below.

## The cinderx fork, and why each edit exists

1. `declared_types` -- which assignment inferred a local's type. Mostly a dead
   end: it records locals that were *already* inferred, excluding annotated ones,
   which are exactly the set that becomes inferred after erasure. Covers 23% of
   store targets. Wiring the graph to it dropped accuracy by 28 points.
2. `self` kept out of the flow graph -- `self` is never erased.
3. **`local_defs` / `resolved_from`** (the useful one) -- reaching definitions
   per read. `TypeState.local_defs` parallels `local_types`, is unioned wherever
   `LocalsBranch.merge` joins types, and `visitName` records
   `module.resolved_from[read] = frozenset(defs)`. About 20 lines.

   Loop back edges come free: `visitFor`/`visitWhile` already call
   `iterate_to_fixed_point`, so the body is re-walked and the loop-carried
   definition lands in the merge. Verified: a read inside a loop sees both the
   pre-loop definition and the loop-carried one.

## Things that are true and were expensive to learn

- **Erasure deletes rows from a source table; it never injects dynamic.** The
  query is "what still has a surviving source", not "what got poisoned".
- **`bind` vs `demand`.** Only a local assignment infers from its value. A call
  argument does not determine its parameter, and an unannotated function binds
  dynamic rather than inferring from its returns. Fabricating those inbound
  edges makes parameters look like they have sources.
- **A large share of types are looked up, not flowed** -- signatures, class
  fields, builtins. Repeatedly the fix that felt like a missing edge was a
  missing *vertex*. Class and function names are not in `expr_types` at all;
  they have to be recognised by name.
- **The solver must descend, not ascend.** A loop-carried definition makes a
  read depend on itself. A least fixpoint can never let such a cycle become
  typed even when it is stably typed; cinder starts from the pre-loop type and
  iterates down. Start everything typed and remove what an untyped input forces
  dynamic.
- **A literal's type is context-supplied.** `2` is `int64` only because
  something asked; `[]` is `chklist[T]` only while an annotation says so. When
  the context vertex goes dynamic they revert to natural type. Skipping this
  makes `chklist[int] -> list` score as *agreement* while the types differ.
- **A declaration is its own context**, and that is a seed, not an edge -- as an
  edge it would survive erasure, and then a primitive target never boxes.
- **Guard narrowing is recoverable post-hoc.** `if not isinstance(x, T): raise`
  narrows the rest of the block, and that is visible in the tree. Merge
  narrowing is not, which is what `resolved_from` is for.

## Things that are false, tried anyway, and should not be retried

- Linking every write of a name to every read. Too pessimistic; cinder narrows
  per path.
- Linking *reaching* writes computed syntactically (textual predecessors plus
  writes in an enclosing loop). Also worse. Use `resolved_from`.
- Operands supplying each other's context. Invents demands that were never
  there and emits `cast(float, 4)`.
- `Compare` as a blanket source. `x is None` is always bool; `x == "A"` is not.
- Module scope never infers. Class bodies are not module scope, and `func_of`
  returns `None` for both.
- Dunder parameters as unconditional sources.

## Traps that cost hours

- **Every `get_ast_data` builds a fresh `Compiler`, so `DYNAMIC` is a different
  object.** Any `is dyn` test across two binds is silently always false. This
  was live in `detyper.py`, making every check in `simplify_coercions` a no-op.
- **Vertex versus node.** `settle`'s cut guard tested the node, which blocked
  the *context* vertex of every type source from ever being derived.
- **Two vertices for one declaration.** Sources marked an AnnAssign's target;
  `reverse_outflow` maps reads to the AnnAssign node. One edge between them was
  worth ten points.
- **Score expressions, not variables.** Variable-level scoring read 98% while
  expression-level read 85%.
- **Full erasure is not the experiment.** Partial masks are. Rules tuned for one
  diverge from the other.

## What the numbers do not mean

Binding the *erased* program directly works about two thirds of the time, and
where it works it is exact. The graph earns its place only on the other third --
where cinder refuses to bind at all. Any accuracy claim measured on maskings that
happen to bind is measuring the easy case. An earlier "100% of partial masks
bind" claim in this project was wrong: it used annotation roots, not the
bench-granularity roots the detyper actually masks over.

## Two rules that were expensive to find

**A context with no demander is not dynamic.** `ctx_of` used to return `dyn`
whenever the context vertex was untyped, which conflates two situations:
something demanded a type here and lost it (the slot really did go dynamic —
that is what makes a value box), versus nothing demands this position at all.
cinder records an unconstrained expression's context as its own type, so calling
it dynamic invents a mismatch and makes `patch_adder` coerce what was already
correct. Splitting the two took context accuracy from 59.8% to 87.5%.

**An erased declaration is not an absent declaration — it is `: Any`, and a
dynamic slot still makes demands.** cinder narrows the local to whatever was
assigned, so reads of `n` in `n: Any = int64(nb)` still see `int64`; but the
assignment itself must fit a dynamic slot, and a primitive does not. The two
slots diverge at that one node: **the type keeps flowing, the context goes
dynamic**. Forcing the type dynamic as well costs 3 points, because the
narrowing is real and the measurement knows it.

Its consequence: an erased declaration whose value must box holds the *boxed*
type, and so does every read of it. Otherwise the pass emits `box(int64(nb))`
into the slot and then, three lines later, `box(n)` — because it still believed
`n` was the primitive its own rewrite had just boxed.

## Where to pick up

1. `pick_patch`, which is now the whole of the remaining gap. It has been
   starved of correct types the whole project and is no longer starved. It has been starved of correct types the whole
   time, and it also never revisits coercions already present in the source --
   an author-written `box(size)` becomes invalid when `size` stops being
   primitive, and nothing rewrites it.
3. Do not chase table accuracy further without checking the detyper. The two
   diverged for most of this project's history.

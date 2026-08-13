# It's min cut, max flow, baby

**Claim.** On the statement vocabulary Static Python actually gives you, "insert
the fewest coercions" is not a search problem. It is minimum s–t cut, and
max-flow solves it exactly in polynomial time.

**Why to believe it.** A coercion is charged when two positions *disagree* about
representation. Disagreement costs on binary labels are cuts. That is the whole
argument; everything below is bookkeeping and the search for a
counterexample.

Sections 1-9 are the argument. **It was then measured: see "Results,
2026-08-13" at the bottom — ~3,400 projections across 14 programs, zero
non-submodular terms, and a max-flow solver that matches brute force on 10 and
emits programs that compile and run on 9.**

## 1. What the solver does today

`bool_solver.py` is already the right object, one layer of abstraction below
where it needs to be:

- **Variables.** One boolean per local: does it arrive TYPED or DYNAMIC
  (`boolean_search.md`). Concrete Cinder types are pushed to the edges and turned
  back into bits by probes.
- **Factors.** Phase 1 measures, per statement, a table keyed on that
  statement's read bits, giving the minimum wraps that make it typecheck and
  what it delivers upward.
- **Objective.** Phase 3 (`descend`, `bool_solver.py:340`) is a branch-and-bound
  DFS over all assignments, summing factor costs.

That is a factor graph over booleans with non-negative costs, minimised by
exponential search. Which is exactly the object graph cuts minimise — under one
condition.

## 2. The condition

For a pairwise factor over locals `a`, `b`, write

```
A = E(DYN, DYN)   B = E(DYN, TYPED)   C = E(TYPED, DYN)   D = E(TYPED, TYPED)
```

The factor is **submodular** iff

```
A + D  <=  B + C
```

A pairwise pseudo-boolean function is minimisable by min-cut iff every pairwise
term satisfies that inequality (Hammer 1965; Kolmogorov & Zabih 2004). Not
"approximable" — the min cut *is* the minimum.

## 3. Every statement in the vocabulary passes

Straight off `reduction.md`'s tables, which were read out of `decide_type` /
`decide_context` and the patcher's repair vocabulary:

| statement | table | A+D | B+C | submodular |
|---|---|---|---|---|
| Seed `v = 1` | unary: 1 if TYPED | — | — | trivially |
| Typed demand `t: int64 = v` | unary: 1 if `v` DYNAMIC | — | — | trivially |
| Dynamic demand `t: Any = v` | unary: 1 if `v` TYPED | — | — | trivially |
| Conjunction `c: int64 = a + b` | A=1 B=1 C=1 D=0 | 1 | 2 | **yes** |
| Agreement `if a < b:` | A=0 B=1 C=1 D=0 | 0 | 2 | **yes** (the cut term itself) |

The conjunction row is the interesting one, because subsumption ("one wrap high
that subsumes two low") is exactly the kind of thing that breaks tidy models. It
survives: `A + D = 1 + 0` against `B + C = 1 + 1`.

The agreement row is not merely submodular, it is a Potts term — cost iff the two
labels differ. That is the canonical edge of a graph cut. `must_agree`
(`typedness_graph.py:754`) is generating cut edges directly.

## 4. The punchline: the missing statement is the non-submodular one

`reduction.md` builds 3SAT out of typing and stalls on one statement it calls
`OR3`: *costs one wrap exactly when all of its inputs arrive dynamic, nothing
when at least one arrives typed.* Static Python does not give you that statement.

Take its two-input form and tabulate:

```
A = E(DYN, DYN)   = 1
B = E(DYN, TYPED) = 0
C = E(TYPED, DYN) = 0
D = E(TYPED, TYPED) = 0

A + D = 1   >   B + C = 0        NOT submodular
```

So the statement the hardness proof needs is precisely the statement that would
push the problem out of min-cut. These are the same fact seen from two sides:

- **General rule.** Terms of the form *"pay unless everything agrees typed"* are
  submodular. Terms of the form *"pay only when everything is dynamic"* are not.
- **Why the vocabulary only has the first kind.** A coercion exists to bridge a
  representation mismatch. It is charged on disagreement. There is no wrapper
  whose job is to be needed only when nothing is typed — that is not what
  wrappers do.

The 3SAT reduction is stuck for a structural reason, not a missing-feature
reason. And that reason is a polynomial-time algorithm.

## 5. The construction

Two terminals: **S = TYPED**, **T = DYNAMIC**. One node per local's bit.

**Pinned cells.** A kept annotation pins its cell to S, an erased one pins to T,
each with an infinite-capacity terminal edge. Phase 2's row deletions become
these pins.

**Unary terms.** Cost `u` for being TYPED becomes a capacity-`u` edge to T; cost
`u` for being DYNAMIC becomes a capacity-`u` edge to S. Negative coefficients are
absorbed into the constant in the usual way.

**Pairwise terms.** Decompose the measured table exactly:

```
E(x, y) = A + (C - A)*x + (D - C)*y + lambda * (1 - x) * y
          where lambda = B + C - A - D  >= 0
```

Check the four corners: `(0,0) -> A`, `(1,0) -> C`, `(1,1) -> D`,
`(0,1) -> A + (D-C) + (B+C-A-D) = B`. The first three pieces are a constant and
two unary terms; the residual is a single directed edge of capacity `lambda`,
cut exactly when the two bits disagree in one direction. Submodularity is the
statement that this capacity is not negative.

**Higher-arity terms.** A statement reading three locals gives an arity-3 factor.
It is graph-representable iff every 2-variable projection is submodular, and the
"pay unless all typed" shape passes: `lambda * (1 - x1*x2*x3)` projects to
`lambda * (1 - x_i*x_j)` or to a constant, both submodular. One auxiliary node
per such term represents it (Freedman & Drineas 2005; Kolmogorov & Zabih for
triples).

**Then run max-flow.** The min cut labels every free bit; the cut edges are
exactly the positions that need a wrapper, and the cut value is the minimum
number of wrappers. Phase 3's DFS is replaced by one max-flow.

## 6. What has to be checked, and how

Three conditions. Each is mechanical, none requires new theory.

**(a) Every *measured* factor is submodular.** The five statements in section 3
were written by hand; phase 1 measures tables the hand-written list does not
cover. Enumerate every factor phase 1 produces, and for arity ≥ 2 test all
2-variable projections for `A + D <= B + C`. One violation and the result
becomes QPBO/roof-duality territory (partial optimality: some bits provably
optimal, the rest left to search) rather than exact max-flow.

*This is the experiment. Everything else is downstream of it.*

**(b) The binary abstraction is faithful.** If a local can arrive as `int64`
*or* `double` — two distinct non-dynamic representations — then one bit per local
is the wrong domain, and a min cut over bits is the exact optimum of a different
problem. `boolean_search.md` argues the concrete type is forced at the edges and
only the bit is free. That argument carries the entire result, so it is worth
restating as a checkable invariant: for every local, the set of non-dynamic types
it can hold across all feasible assignments has size ≤ 1. If it can exceed 1,
the honest model is multi-label Potts, which is NP-hard, with α-expansion giving
a 2-approximation.

**(c) No hard constraint forces disagreement.** Infinite capacities encode "must
agree" and "must be TYPED/DYNAMIC" fine. A constraint of the form *these two must
differ* is frustration, is non-submodular, and would break the construction. Scan
phase 2's deletions for any that ban both agreeing rows while permitting a
disagreeing one.

## 7. If it holds, what it buys

- **Exact minima where search cannot reach.** deltablue has 36 units; phase 3's
  DFS is hopeless there and `solve_and_apply.py` already fails on fully erased
  Held–Karp because independently chosen sibling repairs conflict. Max-flow does
  not care about program size in the same way.
- **A ground truth to measure the real mediator against.** `typedness2_results.json`
  holds emitted wrapper counts for 721 masks. Compare each against the cut value:
  the gap is exactly how far `type_mediator.py` is from optimal, per mask. Today
  we know one place it over-emits (the `ast.Expr` miss in
  [typedness_findings.md](typedness_findings.md)) and have no idea about the rest.
- **Minimum *runtime* cost, not minimum count.** Weight each edge by the
  execution frequency of the position rather than 1, and the same max-flow returns
  the cheapest wrapper placement instead of the fewest. That is the version that
  speaks to the perf results — and it is the missing frequency term from the
  topology discussion, entering as capacity.
- **A statement about the whole problem.** "Minimum coercion insertion for a
  fixed mask is in P, and here is the certificate" is a much stronger claim than
  "our search found 33 wrappers".

## 8. How it could still die

1. **A non-submodular measured factor.** Most likely from subsumption
   interacting with a statement that reads three or more locals, where one wrap
   can repair several mismatches at once and the saving is not pairwise.
2. **Non-local wrapper cost.** `@inline` argument casts depend on the whole call,
   not on two endpoints (`patch_picker.py`'s `inline_args` branch). If a
   wrapper's cost is not a function of its incident bits, the energy is not
   pairwise-decomposable and min-cut is an approximation.
3. **The bit is a lie somewhere.** Condition (b) failing in one benchmark is
   enough to make the "exact" claim false in general, while leaving it true for
   programs where the invariant holds — which would still be worth having, just
   with a precondition attached.
4. **Feasibility is not free after all.** The model assumes the all-dynamic
   assignment is always available at zero wraps (`reduction.md` says exactly
   this, and it is why `detype` never fails). If some mask makes even that
   infeasible, the cut has no finite value and something is wrong upstream of the
   optimisation.

## 9. Next action

Write the submodularity checker: run phase 1 + phase 2 over the golden
benchmarks at several masks, enumerate factors, test every 2-variable projection,
and print any violation with the statement that produced it. That single number —
violations found — decides whether this file is a result or a nice story.

---

# Results, 2026-08-13

Everything above was an argument. This is what happened when it was run.
Scripts: `stupid_optimal_attempt/check_submodular.py` (the test),
`stupid_optimal_attempt/min_cut_solver.py` (Möbius expansion, Dinic, emission).

## Headline

**No non-submodular cost term exists in the measured tables.** Roughly 3,400
2-variable projections across 8 toy cases and 6 benchmarks, zero real
violations. The condition the whole framing rests on holds everywhere it could
be tested.

**Max-flow is exact where the energy is pairwise**, verified against brute force
over the same factors, and the wrap sets it produces compile and run.

**It is not yet a working replacement for the mediator.** Two blockers, both
upstream of the cut: genuine order-3 terms in four benchmarks, and phase 1
tables that are empty or under-constrained in five.

## 1. Submodularity: measured

`E(D,D) + E(T,T) <= E(D,T) + E(T,D)` on every 2-variable projection of every
factor, with an infeasible corner counted as +inf.

| program | factors | arities | projections | violations |
|---|---|---|---|---|
| 8 toy cases | 3-8 each | up to 3 | 2-8 each | **0** |
| fannkuch | 50 | 1:18 2:27 3:4 4:1 | 70 | **0** |
| call_simple | 64 | 1:23 2:20 3:21 | 145 | **0** (at max-wraps 3) |
| nqueens | 57 | 1:24 2:19 3:10 4:4 | 163 | **0** (at max-wraps 5) |
| call_method | 105 | 1:44 2:20 3:21 4:20 | 625 | **0** |
| call_method_slots | 105 | 1:44 2:20 3:21 4:20 | 625 | **0** |
| chaos | 118 | 1:32 2:45 3:21 4:16 5:4 | 745 | **0** |
| held_karp | 103 | up to 7:1 | 1162 | **0** |
| nbody | 72 | 1:23 2:33 3:13 4:1 5:2 | 287 | 6, all truncation-shaped |
| pystone | 128 | 1:81 2:35 3:12 | 101 | **0** |
| richards, deltablue, float | — | — | **0 tested** | vacuous |

### The truncation artifact, and why it is not a counterexample

`--max-wraps` bounds phase 1's per-statement search, so a corner needing more
wraps than the bound is recorded as *infeasible* rather than *expensive*. An
infeasible **agreeing** corner makes `A + D = inf` and reports a violation. Those
are measurement holes, not non-submodular costs, and they close when the bound
rises:

```
call_simple   max-wraps 2 -> 60 violations
call_simple   max-wraps 3 ->  0
call_simple   max-wraps 4 ->  0
nqueens       max-wraps 2 ->  7 violations
nqueens       max-wraps 3 ->  6
nqueens       max-wraps 5 ->  0
```

nbody's 6 remaining violations at max-wraps 4 have the identical signature (all
reported as "from an infeasible agreeing corner") and are expected to close the
same way; it was not run higher because phase 1 cost grows fast.

**Every violation ever observed was of this shape. Not one was a finite
`A + D > B + C`.** That is the result: the cost tables carry no `OR3`.

### Vacuous verdicts, and a lesson

`richards`, `deltablue` and `float` reported "0 violations" while testing **0
projections** — phase 1 produced no feasible row for any of their factors, so
there was nothing to test. A clean violation count means nothing without its
denominator; the checker now prints both, and the solver refuses to build from
empty tables instead of silently reporting a cut of 0.

## 2. The solver

`min_cut_solver.py`:

1. phase 1 + phase 2 from `bool_solver` produce the tables
2. each factor is expanded by **exact Möbius transform**,
   `E(x) = sum_S c_S prod_{i in S} x_i`, with infeasible corners at a large
   finite constant so hard constraints become capacities
3. coefficients become graph structure:
   - order 0 -> a constant
   - order 1 -> a terminal edge, negatives shifted into the constant
   - order 2, `w * x*y` with `w <= 0` -> constant `w`, a `|w|` edge to S on `x`,
     a `|w|` edge `x -> y`. Corners check: `(1,1) -> w`, all others `0`.
   - order 3+ or a positive order-2 coefficient -> **abort**, never approximate
4. pins from `bit_domains`: infinite terminal edges
5. Dinic; the cut value is the minimum wrap count and the S-side is the set of
   locals that arrive typed
6. `--emit` reads each statement's cheapest row consistent with the chosen bits,
   materialises the program, and compiles or runs it

### Exactness, against brute force over the same factors

| case | min cut | brute force | verdict |
|---|---|---|---|
| scalar | 0 | 0 | MATCH |
| array_store | 1 | 1 | MATCH |
| compare | 0 | 0 | MATCH |
| mixed | 2 | 2 | MATCH |
| nested | 0 | 0 | MATCH |
| index_wrap | 1 | 1 | MATCH |
| call | 0 | 0 | MATCH |
| call_method | 0 | 0 | MATCH |
| call_method_slots | 0 | 0 | MATCH |
| call_simple | 0 | 0 | MATCH |

Max flow runs in **under 1 ms** on every one of these, against phase 3's DFS over
`2^free` assignments. On deltablue the graph is 159 bits, 277 factors — a size
where the DFS is not an option.

### Practical validity: does the emitted program work

| case | wraps | compiles | runs |
|---|---|---|---|
| scalar | 0 | yes | yes |
| array_store | 1 | yes | yes |
| compare | 0 | yes | yes |
| mixed | 2 | yes | yes |
| nested | 0 | yes | yes |
| index_wrap | 1 | yes | yes |
| call_method, full erasure | 0 | yes | yes |
| call_method_slots, full erasure | 0 | yes | yes |
| call_simple, full erasure | 0 | yes | yes |
| call_method_slots, **mask 28** | 0 | **no** | — |

So the wrap sets are real programs, not just numbers — on the toys, exactly
minimal ones. The three full-erasure benchmarks agree with the mediator (both
emit 0), which is agreement on a case where the answer is trivially 0.

## 3. The two blockers

### (a) Genuine order-3 terms

fannkuch, nqueens, nbody, held_karp and one toy have nonzero Möbius coefficients
of order 3 with small weights (`+1`, `+2`, `-2`) — real structure, not the
infeasibility constant. Those factors *are* submodular on every projection, so
they are graph-representable, but not by pairwise edges alone. What they need:

- **triples**: the Kolmogorov & Zabih (2004) construction, one auxiliary node per
  cubic term
- **arity 4-7** (held_karp reaches 7): higher-order clique reduction (Ishikawa
  2009 HOCR), or Freedman & Drineas (2005) for the submodular case

This is bounded work with known constructions, and it does not threaten the
framing — a submodular higher-order term is still exactly minimisable.

### (b) Phase 1 tables that are empty or under-constrained

- **Empty**: float's 27 factors have no feasible row at max-wraps 4 *or* 6, so
  raising the bound is not the fix. Same for richards and deltablue. The probe
  machinery is not producing plans for these programs at all.
- **Under-constrained**: on a *partial* mask the model is too weak. At
  call_method_slots benchmark-granularity mask 28 the cut says 0 wraps and the
  emitted program is rejected with
  `type mismatch: int64 received for positional arg 'a'` — while the mediator
  needs 60 wrappers for that mask. Phase 1 measures each statement in isolation
  and the pins from `bit_domains` do not carry parameter agreement across call
  sites, so the demands that force those 60 never enter the tables.

Both are pre-existing limits of the phase 1 / pinning model that this exercise
surfaced. Neither is evidence against min cut: on mask 28 the cut is the correct
minimum of an energy that is missing constraints.

## 4. What would finish it

1. **Fix the model on partial masks.** A statement's factor has to see the
   demands its callers place on it. Until then the solver is only trustworthy at
   full erasure, where every parameter is dynamic and cross-statement agreement
   is vacuous.
2. **HOCR for order 3+.** Unblocks fannkuch, nqueens, nbody, held_karp.
3. **Find out why float, richards and deltablue produce no rows.** This is a
   probe bug, unrelated to the optimisation.
4. **Then compare against the mediator per mask.** `typedness2_results.json` has
   emitted wrapper counts for 721 masks; the cut value is the lower bound to
   compare each against. That number — mediator minus optimum, per mask — is the
   thing worth publishing.
5. **Weight by frequency.** Capacities are currently 1 per wrap. Weight each edge
   by the execution count of its position and the same max flow returns minimum
   *runtime* cost rather than minimum count, which is what the perf results in
   `typedness_findings.md` actually care about.

## 5. Standing claim

> For a fixed mask, with the cost tables Static Python's repair vocabulary
> produces, minimum coercion insertion is a submodular binary labelling problem
> and therefore exactly solvable in polynomial time by max flow. Measured on 14
> programs, ~3,400 projections, zero non-submodular terms. Verified exact against
> brute force on 10, and the emitted programs compile and run on 9.

The parts still owed are the higher-order reduction and a constraint model that
is faithful on partial masks. Neither touches the claim itself.

---

# The vertex formulation, 2026-08-13

A wrapper is a **position**, not a relationship. That makes the natural object a
vertex, not an edge, and `stupid_optimal_attempt/vertex_cut_solver.py` is the
attempt. It works, it is fast, and it exposed exactly where the model is wrong.

## Why vertices fit better

- **Subsumption is native.** `int64(a + b)` is one wrapper covering two operands.
  In an edge model that has to be smuggled in as two correlated edges, and
  `solve_and_apply.py`'s failure mode -- "independently selected sibling repairs
  conflict after propagation" -- is the price. As a vertex it is one node with
  one capacity, cut once.
- **No probing.** The network is built from `settle` plus `find_mismatches`, in
  graph time. The labelling solver needs bool_solver's phase 1, which produces
  nothing at all for float, richards and deltablue; the vertex solver runs on all
  twelve benchmarks in under a second.
- **Ties break canonically.** Max-flow leaves a residual graph with two extremal
  minimum cuts (Escalante's lattice on minimal (a,b)-separators is this
  structure). Source-side = wrap as early as possible; sink-side (`--late`) = wrap
  as late as possible, which keeps more of the program on the primitive path at
  the same wrapper count. The current picker has no principled way to make that
  choice.

## Complexity, for the record

Minimum vertex cut between **two** terminals is polynomial -- Menger, via node
splitting: replace `v` with `v_in -> v_out` at capacity `cost(v)`, give the
original edges infinite capacity, and no cut can pay for anything but vertices.
What *is* NP-hard is multiway vertex cut (three or more mutually separated
terminals), balanced separators, and max-cut. Two labels, two terminals is where
the tractability lives -- the same boundary as the two-label restriction above.

## The construction as built

```
S --INF--> origin cells --INF--> ... graph edges ... --INF--> mismatch --INF--> T
                                     each cell split in -> out
                                     capacity = wrap cost if wrappable else INF
```

Both of an AST node's cells (TYPE and CONTEXT) share one capacity, because one
wrapper at that position repairs the node however the demand arrived. That
sharing is the thing an edge model cannot express.

## Results, and the bug they expose

| benchmark | cells | edges | mismatches | vertex cut | mediator emits |
|---|---|---|---|---|---|
| held_karp | 656 | 959 | 38 | 38 | **0** |
| richards | 1162 | 1732 | 55 | 55 | 6 |
| deltablue | 1520 | 2271 | 61 | 61 | 3 |
| nbody | 652 | 1054 | 24 | 24 | 40 |
| fannkuch | 252 | 363 | 39 | **23** | 10 |
| call_method_slots | 552 | 954 | 0 | 0 | 0 |
| float | 176 | 271 | 0 | 0 | 0 |
| pystone | 651 | 936 | 0 | 0 | 0 |
| array_store (toy) | 31 | 43 | 4 | 4 | 1 |

Every instance solves in well under a second, including deltablue's 1520 cells --
a size where phase 3's DFS is not an option and phase 1 cannot even build tables.

**The cut equals the mismatch count almost everywhere.** That is the bug. A
correct model would share constantly, because sharing is what subsumption *is*;
only fannkuch showed any (23 against 39 mismatches). And the mismatch count
itself over-counts: held_karp reports 38 repairs where the mediator needs none.

### Why: the sink set is not a fixed set

`find_mismatches` is evaluated on the settled prediction *before* any repair.
But whether a position is a mismatch **depends on which repairs have already been
chosen** -- one upstream wrapper propagates and eliminates several downstream
mismatches. array_store is the minimal demonstration: four mismatches, and the
mediator clears all four with the single `int64(i)` at the array store, because
`record` / `propagate` rewrite the tables as the wrapper goes in.

So the vertex network is separating S from a sink set that is itself a function of
the cut. That is not a defect of vertex cuts; it is the model handing the solver
the wrong terminals. `stupid_optimal_attempt/README.md` already names this from
the other direction: "one patch may create another mismatch".

## The synthesis to build next

Node splitting is right about **cost** (per position, so subsumption is one
vertex) and the labelling model is right about **semantics** (per assignment, so
mismatch existence is derived rather than assumed). The two are compatible:

1. keep the labelling formulation from `min_cut_solver.py`, which matched brute
   force exactly on 10 programs and emits working code on 9
2. move the capacity from the pairwise edge onto a **split position node**, so a
   wrapper that repairs several demands is paid for once
3. derive the demand structure from the labelling, not from a pre-computed
   mismatch set

That keeps every property already verified -- submodularity holds on the measured
tables, and the pairwise terms stay non-positive -- while fixing the one thing the
edge model got wrong about subsumption.

## What this changes about the standing claim

Nothing. Both formulations are two-terminal, two-label, and polynomial. The
evidence for the claim is still ~3,400 projections with zero non-submodular terms
and 10 brute-force-exact matches. What the vertex experiment adds is a second
route to the same answer, one that scales to the whole suite without probes, and
a precise diagnosis of which part of the graph construction is not yet faithful:
the sink set, not the cut.

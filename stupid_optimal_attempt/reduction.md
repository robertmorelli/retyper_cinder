# 3SAT as a typing problem

Take a 3SAT instance, emit a function, ask the minimum-coercion solver for its
answer, and read a satisfying assignment off the program it prints. This is what
that construction looks like, what it proves, and the single statement Static
Python does not currently give you that the construction needs.

Read `boolean_search.md` first. Everything here is in its vocabulary: a **bit**
per local saying whether it arrives typed, a **wrap** per position, and a cost
of one per inserted coercion.

## What the question has to be

Not "does it typecheck". Every erased program typechecks with enough boxes, and
the all-dynamic assignment is always available at zero wraps -- that is why
`detype` never fails and why phase 2 exists at all. Feasibility is trivial, so
the formula has to ride on the **budget**:

> DECISION: given a program and `k`, is there a typing that typechecks with at
> most `k` inserted coercions?

The reduction makes `k = n`, the number of variables, and arranges that the
program costs exactly `n` when the formula is satisfiable and `n + 1` or more
when it is not.

## The statements you get to use

Each is one statement with a cost that depends only on the bits of the locals it
reads. `T` = arrives typed, `F` = arrives dynamic.

**Seed.** `v = 1`

| plan | v | cost |
| --- | --- | ---: |
| `v = 1` | F | 0 |
| `v = int64(1)` | T | 1 |

This is the whole choice. Typing a variable means paying one wrap at its
defining assignment. (`boolean_search.md`, phase 1.)

**Typed demand.** `t: int64 = v` -- charge one when `v` is dynamic.

**Dynamic demand.** `t: Any = v`, or `return v` from `-> int` -- charge one when
`v` is typed, because a primitive cannot be stored in an erased slot and boxing
is the only repair. (`bool_solver.py:168`, the `not ok and not options` branch.)

**Conjunction with subsumption.** `c: int64 = a + b`

| a | b | plan | cost |
| --- | --- | --- | ---: |
| T | T | `a + b` | 0 |
| T | F | `a + int64(b)` | 1 |
| F | T | `int64(a) + b` | 1 |
| F | F | `int64(a + b)` | 1 |

Cost `1` unless both arrive typed -- the last row is the one wrap high that
subsumes two low.

**Agreement.** `if a < b:` -- charge one when the two disagree, because a
comparison coerces neither side (`typedness_graph.py:754`, `must_agree`).

The seed table is measured (`boolean_search.md`); the rest are read off
`decide_type`/`decide_context` and the patcher's repair vocabulary. They are not
measured here -- the checkout's `.venv` cinderx no longer binds
(`'ModuleTable' object has no attribute 'expr_types'`).

## The one statement you need and do not have

Call it **`OR3`**: a statement that costs one wrap exactly when *all* of its
inputs arrive dynamic, and nothing when at least one arrives typed.

Nothing above has that shape. Everything above charges for *disagreement* --
a dynamic value meeting a typed demand, a typed value meeting a dynamic one, two
operands that do not match. `OR3` charges for *agreement*, and agreement is
exactly what a coercion never has to repair.

Spelled as a checking rule:

```text
any_typed(a, b, c)   well typed  ⟺  at least one argument arrives primitive
                     result: int, fixed, whatever the arguments are
```

That is the shape of `box` and `cbool` -- a fixed result plus a bespoke argument
rule -- which in this repo is the `FIXED_RESULT` set (`typedness_graph.py:787`).
As an overload set it is seven signatures, every pattern except all-dynamic:

```python
@overload
def any_typed(a: int64, b: int64, c: int64) -> int: ...
@overload
def any_typed(a: int64, b: int64, c) -> int: ...
@overload
def any_typed(a: int64, b, c: int64) -> int: ...
@overload
def any_typed(a: int64, b, c) -> int: ...
@overload
def any_typed(a, b: int64, c: int64) -> int: ...
@overload
def any_typed(a, b: int64, c) -> int: ...
@overload
def any_typed(a, b, c: int64) -> int: ...
```

Any call with a typed argument matches one signature exactly and pays nothing.
All three dynamic matches none, and the cheapest repair is one `int64(...)` on
any one argument. Cost `0` / cost `1`, on exactly the condition wanted.

Three signatures -- one per argument, the others left dynamic -- do **not**
work, and the failure is worth recording. Under `(a: int64, b, c)` a second
typed argument lands in a dynamic slot and pays a box, so a clause with two true
literals would cost one instead of nothing and the budget argument below
collapses. The seven-signature set is what keeps every satisfying pattern free.

It also cannot be a Python function. Each signature declares a different
argument *representation*, so each is a different calling convention and they
cannot share one body. `any_typed` has to be a compiler intrinsic that checks its
call site and emits nothing.

At arity two the same intrinsic is three signatures, and the pair form
`both_dynamic(v, w)` is what the variable gadget uses.

The rest of this document assumes `OR3` and builds the reduction on it. The
section after that is why you cannot get it out of the type system as bound
today, and what that says about the search.

## The construction

Formula `F` over `x_1..x_n` with clauses `C_1..C_m`.

**Per variable, two locals and two penalties.** `v_i` stands for `x_i`, `n_i`
for `¬x_i`:

```python
    v1 = 1
    w1 = 1
    both_dynamic(v1, w1)       # penalty: costs 1 when both are dynamic
    both_dynamic(w1, v1)       # a second call, so the penalty is paid twice
```

Two seeds, two copies of the pair form over the pair. Two call sites are two
positions: with both locals dynamic each site needs its own coercion, so the
penalty really is 2. The table, with seed costs included:

| v_i | n_i | seeds | penalties | total |
| --- | --- | ---: | ---: | ---: |
| T | T | 2 | 0 | 2 |
| T | F | 1 | 0 | **1** |
| F | T | 1 | 0 | **1** |
| F | F | 0 | 2 | 2 |

Exactly one of the pair typed is uniquely cheapest, at one wrap. That is the NOT
gadget: `n_i` is dynamic precisely when `v_i` is typed. Both configurations that
would let `x_i` and `¬x_i` both count as true, or neither, cost one more.

**Per clause, one penalty.** For `C_j = (ℓ_1 ∨ ℓ_2 ∨ ℓ_3)` write
`any_typed(u_1, u_2, u_3)` where `u_k` is `v_i` when `ℓ_k = x_i` and `n_i` when
`ℓ_k = ¬x_i`. It costs zero when some literal is true (that local arrives typed)
and one when all three are false.

**Budget.** `k = n`.

Every variable pair costs at least one, so any typing costs at least `n`. It
costs exactly `n` iff every pair is split -- a truth assignment -- and every
clause penalty is free -- all clauses satisfied. Both directions:

- satisfiable ⟹ set each pair to match the assignment: `n` wraps, no clause pays;
- a typing at `n` wraps ⟹ every pair is split and every clause is free, so the
  seeds spell out a satisfying assignment.

An unsatisfied clause costs one, and so does setting a pair both-typed to fake
two literals at once. Neither cheat gets under `n + 1`.

**Reading the answer.** The solver prints a program. `x_i` is true iff the
emitted seed is `v_i = int64(1)` rather than `v_i = 1`. Nothing else has to be
decoded -- the wrap positions *are* the assignment.

### Worked example

`(x ∨ y ∨ ¬z) ∧ (¬x ∨ ¬y ∨ z) ∧ (x ∨ ¬y ∨ ¬z)` -- three variables, three
clauses, budget 3.

```python
import __static__
from __static__ import int64

def witness(seed: int) -> int:
    vx = 1;  wx = 1            # vx typed means x true, wx typed means not x
    vy = 1;  wy = 1
    vz = 1;  wz = 1
    both_dynamic(vx, wx)       # x is true or false, not both, not neither
    both_dynamic(wx, vx)
    both_dynamic(vy, wy)
    both_dynamic(wy, vy)
    both_dynamic(vz, wz)
    both_dynamic(wz, vz)
    any_typed(vx, vy, wz)      # (x or y or not z)
    any_typed(wx, wy, vz)      # (not x or not y or z)
    any_typed(vx, wy, wz)      # (x or not y or not z)
    return seed
```

As written every local arrives dynamic, so every pair pays 2 and every clause
pays 1: nine wraps. The minimum is three:

```python
    vx = int64(1);  wx = 1     # x = true
    vy = 1;         wy = int64(1)   # y = false
    vz = 1;         wz = int64(1)   # z = false
```

Every pair is split, so no consistency penalty; every clause has a typed
argument -- `vx`, `wy`, `wy` -- so no clause penalty. `x=1, y=0, z=0`, read
straight off which seeds got wrapped. (`x=y=z=0` is the other three-wrap
solution.)

Now the unsatisfiable companion: the same three variables with all eight clauses
`(±x or ±y or ±z)`, so six pair calls and eight clause calls. Every split of the
three pairs leaves exactly one clause with all three arguments dynamic, and the
minimum is four. The extra wrap lands on an argument of whichever clause lost.

Both numbers are enumerated rather than argued -- `check_reduction.py`, run in
scratch, walks all `2^(2n)` bit configurations against the cost table above and
reports minimum 3 for the satisfiable instance and 4 for the unsatisfiable one.

The shape scales linearly: `2n` assignments, `2n + m` calls, budget `n`.

## Why the clause statement cannot be written today

`OR3` is the only missing piece, and its absence is structural rather than an
oversight.

Write each statement's cost as a function of the bits it reads. A two-input
table is **submodular** when

```text
f(F,F) + f(T,T)  ≤  f(T,F) + f(F,T)
```

Check the catalogue: the conjunction is `1 + 0 ≤ 1 + 1`; the dynamic demand pair
is `0 + 1 ≤ 1 + 1`; agreement is `0 + 0 ≤ 1 + 1`. Every one passes, and they pass
for the same reason: the cost is paid for *disagreement*, and a disagreement
term is the definition of a cut edge. `OR3` fails it -- `f(F,F) = 1` against
`f(T,F) = f(F,T) = 0` -- which is precisely what makes it useful and what makes
it unavailable.

The consequence is not a nuisance, it is a result about the search:

> With two representations (typed and dynamic) and one wrap per repair, minimum
> coercion placement is a minimum s–t cut, and is solvable in polynomial time.

Build the cut: one node per free bit, `TYPED` as source and `DYNAMIC` as sink.
A unary charge for arriving dynamic becomes an arc from the source; a unary
charge for arriving typed becomes an arc to the sink; each submodular pairwise
table becomes one arc between the two nodes plus terminal arcs, by the standard
binary-energy construction. The minimum cut is the cheapest typing. `phase_three`
in `bool_solver.py` is doing by branch and bound what a max-flow does directly --
*as long as every phase-1 table is submodular.*

So a 3SAT reduction into the typed/dynamic model would prove P = NP. The
reduction above is real, but it is a reduction into a model with `OR3` in it, not
into Static Python as bound here.

A brute-force search backs this up, within the sizes it actually covered. Over
gadgets built from unary demands and pairwise agreement terms -- one helper local
at up to 5 labels with weights to 2, and two helper locals at 3 and 4 labels with
weights to 1 -- nothing realises `OR3`, and nothing realises a NOT gadget either
(`gadget2.py`, run in scratch; the two searches return `None` at every size). The
wider sweep, two helpers at weights to 2, does not enumerate in Python in
reasonable time: it was killed at 15 minutes without producing a result, so it
says nothing either way. That is evidence, not a proof; the proof for the
two-label case is the
cut construction above.

## The other door: more than two representations

The bit model is a deliberate simplification. The real system has `int64`,
`int8`, `double`, `cbool`, `Array[int64]`, boxed `int`, and every user class --
and `bool_solver.py` collapses them because `concrete_types` gives each local
exactly one name, so its bit really is binary.

Lift that and the picture changes. With three or more representations, "pay one
per disagreeing pair" stops being a cut and becomes **multiway cut**, which is
NP-hard for three terminals (Dahlhaus, Johnson, Papadimitriou, Seymour,
Yannakakis, 1994). The hardness is real but it does not come from 3SAT directly
-- the route is 3SAT to that problem to this one, and the gadgets are global
rather than the three-line clause above.

Note what this says about the implemented solver: it searches a two-label space
by construction, so it is searching a polynomial problem exponentially. If you
want the search to be hard, you have to let a local choose *between* concrete
types, not merely between one type and dynamic.

## What to check in the code

1. Dump the phase-1 tables and test each for submodularity, pair by pair, on the
   inequality above. Every table that passes is a cut edge.
2. If they all pass, `phase_three` can be replaced by a max-flow, exactly, and
   the branch-and-bound bound-keeping (`keep=4096`) becomes unnecessary.
3. If one fails, that statement is where the hardness lives. Print it -- it is
   the closest thing in the language to `OR3`, and it is worth understanding
   before any further search work.

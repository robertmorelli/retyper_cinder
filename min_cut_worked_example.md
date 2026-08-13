# One wrap set, solved by min cut

A complete worked example, every number measured by
`stupid_optimal_attempt/min_cut_solver.py` on
`stupid_optimal_attempt/toy_cases.py::array_store`. Small enough to check by
hand.

## The program

```python
import __static__
from __static__ import int64, Array

def f(nb: int) -> int:
    n: int64 = int64(nb)
    count: Array[int64] = Array[int64](nb)
    i: int64 = 0
    while i < n:
        count[i] = i + 1
        i = i + 1
    return nb
```

Erase every annotation. Now `n`, `i` and `count` have no declared type, and the
question is: **which of them should the emitted program keep typed, and what is
the cheapest set of wrappers that makes that legal?**

## Step 1: one bit per local

| bit | meaning | state |
|---|---|---|
| `nb` | the parameter | **pinned DYNAMIC** — a parameter has no assignment to wrap, so nothing can make it typed |
| `count` | the array | **free** |
| `i` | the loop counter | **free** |
| `n` | the bound | **free** |

Two labels only: arrives TYPED, or arrives DYNAMIC. That binary domain is the
whole reason this works.

## Step 2: measure a cost table per statement

Phase 1 probes each statement under every combination of its read bits and
records the fewest wrappers that make it compile. Two of the tables:

**`while i < n:`** — a comparison coerces neither side, so it charges when the
two disagree:

| `i` | `n` | wraps |
|---|---|---|
| DYN | DYN | 0 |
| DYN | TYPED | 1 |
| TYPED | DYN | 1 |
| TYPED | TYPED | 0 |

**`count[i] = i + 1`** — an `Array[int64]` slot demands a primitive, so a
dynamic `i` has to be converted at the store:

| `count` | `i` | wraps |
|---|---|---|
| DYN | DYN | 0 |
| TYPED | DYN | 1 |
| TYPED | TYPED | 0 |

(`count` DYNAMIC with `i` TYPED is infeasible — a primitive cannot be stored
into an erased slot without boxing, and the row is absent.)

## Step 3: expand each table exactly

Write `x = 1` for TYPED. Every table is a multilinear polynomial with exactly one
expansion, and the solver computes it by Möbius transform:

```
while i < n:          E = 1*i + 1*n - 2*(i*n)
count[i] = i + 1:     E = 1*count + BIG*i - (BIG+1)*(count*i)
```

Check the comparison at all four corners:

```
i=0 n=0 -> 0            i=1 n=0 -> 1
i=0 n=1 -> 1            i=1 n=1 -> 1 + 1 - 2 = 0     matches the table
```

**The pair coefficient is negative in both.** That is submodularity, and it is
what makes the next step legal: `-2*(i*n)` is a reward for agreeing, never a
penalty, so it can be a cut.

## Step 4: turn coefficients into capacities

Terminals: **S = TYPED**, **T = DYNAMIC**. A node ends up on the S side iff that
local arrives typed. An edge `u -> v` is cut iff `u` is on the S side and `v` on
the T side.

- **order 1**, `c*x`: charge `c` when `x` is TYPED → an edge `x -> T` of
  capacity `c`.
- **order 2**, `w*x*y` with `w <= 0`: use the identity
  `w*x*y = w + |w|*(1-x) + |w|*x*(1-y)`, which is a constant, an edge `S -> x`
  of capacity `|w|`, and an edge `x -> y` of capacity `|w|`. Corners:
  `(1,1) -> w`, everything else `0`.
- **pins**: `S -> nb` at infinite capacity would force `nb` typed, so the
  dynamic pin is the other direction, `nb -> T` at infinite capacity.

For the comparison factor that gives:

```
        i -> T   capacity 1          (the +1*i term)
        n -> T   capacity 1          (the +1*n term)
        S -> i   capacity 2          )
        i -> n   capacity 2          )  the -2*(i*n) term, plus constant -2
```

The whole program's graph is the union of every factor's edges, capacities
summed. Infinite edges pin `nb` to T and, via `count[i] = i + 1`'s `BIG`
coefficients, tie `count` and `i` together.

## Step 5: run max flow

```
CUT = 1 wrap
S side (arrives TYPED): count
T side (arrives DYNAMIC): nb, n, i
```

Read it back: keep the array typed, let the scalars go dynamic. One edge is cut —
the store's conversion — and that is the single wrapper.

Brute force over the same factors, all `2^3` free assignments: **1 wrap**.
Same answer.

## Step 6: emit it

```python
def f(nb: Any) -> Any:
    n: Any = nb
    count: Any = Array[int64](nb)
    i: Any = 0
    while i < n:
        count[i] = int64(i) + 1        # <-- the one cut edge
        i = i + 1
    return nb
```

Compiles under the Static Python loader and runs: `rc=0`.

Exactly one wrapper, `int64(i)`, at exactly the position the cut identified.

## Why this is the right frame

Look at what the alternatives would have cost:

| choice | wraps | why |
|---|---|---|
| everything dynamic | 1 | `count` is a rebuilt `Array[int64]` whatever the mask says, so the store still needs its conversion |
| `count` typed, `i` typed | 1 | the store is free, but now `while i < n` disagrees and pays |
| `count` typed, `i` dynamic | **1** | one conversion at the store, comparison agrees |
| `count` typed, `i` typed, `n` typed | 1 | the store and comparison are free, but typing `n` costs its own seed |

Several tie at 1 here, which is what makes this a *small* example. The point is
the mechanism: **every cost in the table is charged for a disagreement between
two positions, and the cheapest way to place disagreements in a graph is a
minimum cut.** No search over wrapper subsets, no heuristic ordering — one
max-flow, and the cut edges *are* the wrap set.

On a program where the ties break, that becomes the difference between the
mediator's answer and the optimum. See
[ITS_MIN_CUT_MAX_FLOW_BABY.md](ITS_MIN_CUT_MAX_FLOW_BABY.md) for the measured
results, including where this does not yet work.

## Reproducing it

```
.venv/bin/python stupid_optimal_attempt/min_cut_solver.py <program.py> \
    --emit --run --exhaustive
```

`--emit` materialises the wrap set and compiles it, `--run` executes it, and
`--exhaustive` brute forces the same factors so you can see the cut and the true
minimum side by side.

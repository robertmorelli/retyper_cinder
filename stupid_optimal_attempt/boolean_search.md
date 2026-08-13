# Minimum coercions as a boolean problem

The mediator emits a working set of coercions. This is about emitting the
smallest one. The search is over booleans throughout; concrete Cinder types
appear only when generating code, because the typedness graph is what turns a
bit assignment into types.

## The bits

Two kinds, and nothing else:

- **read typedness** — one bit per local a statement reads, saying whether that
  read arrives typed or dynamic. A statement cannot decide this: it is settled
  by whatever statement defines the local, and by whether *that* statement
  wrapped. It is the whole interface between statements.
- **wrap** — one bit per wrappable position inside a statement. Not one bit per
  cast *kind*: between two representations there is exactly one conversion, and
  the graph knows it from the types on either side.

A statement's own contribution is then a small table: for each combination of
incoming read bits, the cheapest set of wrap bits that typechecks, and whether
the result is typed at the root.

## The worked example

```python
a = 1
b = 2
c: int64 = a + b
```

### Phase 1 — a table per statement

`a = 1` has one wrappable position:

| plan | typed at root | cost |
| --- | --- | ---: |
| `a = 1` | F | 0 |
| `a = int64(1)` | T | 1 |

`b = 2` is the same.

`c: int64 = a + b` reads two locals, so it has a row per incoming case. Listing
every row, including the ones where the result lands dynamic:

| a | b | plan | c root | cost |
| --- | --- | --- | --- | ---: |
| T | T | `a + b` | T | 0 |
| T | F | `a + b` | F | 0 |
| T | F | `a + int64(b)` | T | 1 |
| F | T | `a + b` | F | 0 |
| F | T | `int64(a) + b` | T | 1 |
| F | F | `a + b` | F | 0 |
| F | F | `int64(a + b)` | T | 1 |

Note the last row. With both reads dynamic the sum is dynamic, so neither leaf
mismatches anything — the only mismatch is at the root. **One wrap high
subsumes two wraps low.** A pass that repairs where the demand shows up emits
`int64(a) + int64(b)` and pays two. This is where the savings live, and it is
why a wrap has to stay a free bit per position rather than being derived by a
canonical post-order walk.

### Phase 2 — delete the banned rows

`c: int64` forces c's root bit to T. Every row above with `c root = F` is
struck out.

This phase is load-bearing, not bookkeeping. Left in, the `F | F` row with zero
wraps wins outright at total cost 0, and the emitted program is `c = a + b` —
which typechecks and quietly throws the int64 away. Phase 2 is the entire
reason the answer below is 1 rather than 0.

A program whose annotations have all been erased to `Any` bans nothing, so this
phase does no work there. That is a property of the input, not a sign the phase
is idle.

### Phase 3 — unify

The free bits are the ones no annotation pinned: `a` and `b`. A row is usable
only when the typedness it assumes for a local is the typedness that local's
own defining statement produces. Sum the costs of the chosen rows:

| a | b | `a =` | `b =` | `c =` | total |
| --- | --- | ---: | ---: | ---: | ---: |
| F | F | 0 | 0 | 1 | **1** |
| T | F | 1 | 0 | 1 | 2 |
| F | T | 0 | 1 | 1 | 2 |
| T | T | 1 | 1 | 0 | 2 |

Minimum is 1:

```python
a = 1
b = 2
c: int64 = int64(a + b)
```

The winner is the assignment where nothing is typed and the single wrap sits at
the root. Typing `a` and `b` costs two to save one at the use.

## Why it is shaped this way

**Types are not the interface; bits are.** Carrying concrete type names through
the interface means carrying `Literal[0]` against `int`, deciding whether one
is assignable to the other, and maintaining a pool of every type a position was
ever observed to take. All of that is the graph's job. A statement only needs
to know whether a read arrived typed.

**A statement cannot be solved alone, only tabulated alone.** Which row is real
depends on choices made elsewhere, so phase 1 answers for every case and phase
3 picks. Anything that commits to one typing before unification — a single
global `settle`, for instance — has already thrown the search away: every
statement sees one typing, no alternatives exist, and the result can only match
what the mediator already produces.

**The wrap set is not determined by the typing.** Fixing the types leaves
several valid wrap sets of different sizes, because a wrap high can cover what
several low ones would. The canonical choice is one of them, not the cheapest.


## The brute force, and what it settled

`true_minimum.py` is the independent answer. It takes the mediator's output,
peels its coercions back off, and then tries every subset of wrappable
positions up to a depth, every conversion at each, compiling with the Static
Python loader and *running* the result to check it still computes what the
mediator computes. An in-process bind filters first, because the loader and the
runtime each cost a subprocess and reject almost everything.

It is exponential and only usable on toys. That is the point: a solver claiming
a minimum means nothing until something independent says what the minimum is.

What it settled:

| case | mediator | true minimum |
| --- | ---: | ---: |
| array_read | 2 | 2 |
| index_wrap | 1 | 1 |
| array_store | 1 | 1 |
| mixed | 2 | 2 |

The mediator is minimal on every toy. An earlier version of this file, and
several solver runs, claimed otherwise - `array_read` at 2 against 3, and so
on. That was a counting bug, not a result. `call_key` includes line and column,
so after any pass rewrites the tree the author's own `int64(j)` no longer
matches itself, gets counted as generated, and gets stripped - which let the
brute force "win" by deleting author code the mediator is not allowed to touch.
Counting by shape instead makes the discrepancy vanish.

The one real gap that survives correct counting is fannkuch: the mediator uses
10 and a verified 9 exists, checked under both counters and confirmed to
compute `[4, 7, 10, 16]`. That is the only case worth pointing a solver at.

## Approaches that were tried and removed

Kept here so they are not rebuilt. Each was a whole solver; all are deleted.

- **branch and bound over wrapper sets, CinderX as oracle** - one error per
  bind, branching restricted to that error's reverse slice. The restriction is
  unsound: fannkuch's known ten-wrapper answer has no position in the tightest
  error's slice, so it was cut at depth zero.
- **best-first on error count** - reached one error remaining and stalled
  forever. The last error was repairable only by a coordinated set, and error
  count gives no gradient toward it.
- **per-statement tables keyed on concrete types** - needed a pool per local of
  every type it had been observed to take, and answers to questions like
  whether `Literal[0]` satisfies `int`. The pools filled with debris and the
  matching over-constrained. Bits fixed this.
- **one global settle, searching which annotations survive** - cannot express
  "consider both cases and reconcile", because there is only ever one typing.
  Every valid assignment came out at exactly the mediator's count.
- **the graph as a validator** - it is a generator. Asked whether a program is
  well typed it reported 35 mismatches on one that compiles and runs, because
  a node with no context edge keeps the annotated program's demand.

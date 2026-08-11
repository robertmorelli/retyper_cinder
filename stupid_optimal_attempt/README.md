# Stupid optimal attempt

This is an exploratory reduction of the Gradual Dimmer's mediation problem.
It intentionally stops before removing annotations or rewriting the AST.

`print_instances.py`:

1. binds the original program with CinderX;
2. builds the typedness graph;
3. settles a selected erasure mask;
4. finds positions where the predicted produced type does not satisfy the
   predicted context;
5. traces every incoming typedness-graph edge to find all upstream expression
   positions whose representation can influence each mismatch;
6. splits the resulting mismatch/candidate bipartite graph into independent
   components; and
7. prints each component as a weighted partial MaxSAT instance.

Each candidate patch is a Boolean variable. Every mismatch becomes a hard
clause requiring at least one influencing patch, and every selected patch
violates a weight-1 soft clause. Thus the model computes a minimum set cover of
mismatches by candidate patch positions.

This is **not yet an exact coercion model**. The reverse slice is conservative
relative to the typedness graph, but a candidate may influence a mismatch
without having one concrete coercion that repairs it, one patch may create
another mismatch, and different repairs at one position may produce different
representations. The output is meant to expose the shape and size of the
optimization problems before implementing those action/representation
constraints.

## Usage

From the repository root:

```bash
./.venv/bin/python stupid_optimal_attempt/print_instances.py path/to/main.py
```

`--mask 0` (the default) follows `detyper.py` and erases every annotation unit.
Masks accept Python integer syntax, including hexadecimal:

```bash
./.venv/bin/python stupid_optimal_attempt/print_instances.py program.py --mask 0x25
```

Use `--granularity benchmark` to select function-grouped units instead of
individual annotation units. Add `--wcnf` to print DIMACS-style weighted CNF
for each independent component.

## Solving experiments

`solve_and_apply.py` brute-forces the approximate set-cover instances, chooses
one concrete wrapper for every selected abstract patch, removes annotations,
and applies the wrappers. This currently demonstrates why the first reduction
is insufficient: on fully erased Held--Karp it selects 33 wrappers but the
result does not compile, because independently selected sibling repairs can
conflict after propagation.

```bash
./.venv/bin/python stupid_optimal_attempt/solve_and_apply.py program.py \
  -o attempted.py --check compile
```

`brute_force_prune.py` starts from the real mediator's known-good output and
finds the smallest subset of its generated wrapper positions that still
compiles or passes the benchmark. This is an exact brute-force minimum over
those positions, but it cannot discover new wrapper placements.

```bash
./.venv/bin/python stupid_optimal_attempt/brute_force_prune.py program.py \
  -o pruned.py --check run
```

On the integer Held--Karp `main2.py`, the mediator generates two removable
wrapper calls. Brute force proves that one is sufficient: the `cbool` around
the reference assertion can be removed, while the `box` at the primitive array
load must remain.

`global_search.py` searches beyond the mediator's positions. It constructs its
candidate universe from the undirected typedness-graph components containing
mismatches or mediator wrappers, generates concrete box/constructor/cast
actions from the representations in those components, and uses the mediator as
an initial upper bound. It first tightens that bound by pruning mediator
wrappers. It then performs iterative branch-and-bound below the bound.

Every branch is checked with an in-process CinderX bind. When binding reports a
type error, the next branch is restricted to actions in that error's directed
reverse slice; an action outside that slice cannot repair the current error
relative to the graph. Only bind-valid candidates pay for a full Static Python
loader check.

```bash
./.venv/bin/python stupid_optimal_attempt/global_search.py program.py \
  --seconds 30 -o optimal.py
```

Current fully-erased results:

| Benchmark | Mediator | Minimum | Bind attempts | Search time |
| --- | ---: | ---: | ---: | ---: |
| integer Held--Karp `main2.py` | 2 | 1 | 4 | 2.3 s |
| N-queens | 5 | 3 | 145 | 4.0 s |
| Fannkuch | 10 | 10 | 2338 | 32.0 s |

“Minimum” is relative to the typedness graph, generated action vocabulary, and
fixed annotation-removal/non-wrapper normalization strategy. It is not a proof
about arbitrary source rewrites or dependencies absent from the graph.

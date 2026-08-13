# What erasing annotations actually costs

Everything below comes from runs on this machine (Apple M1 Pro, Python 3.14.3,
cinderx 2026.5.26.0 at submodule `e76ac5c5`) between 2026-08-12 and 2026-08-13.
Every timing is the number the benchmark prints for itself, never wall time of
the process, because process wall time is ~2.4 s of interpreter and JIT startup
around a 20-600 ms workload.

## The headline

1. **Typedness does not buy performance monotonically.** Of 9 benchmarks that run
   statically compiled, 3 are faster fully typed, 3 are flat, and pystone is
   2.8x *slower* typed than untyped.
2. **The cost is boundaries, not dynamism.** A full 2⁷ factorial on
   call_method_slots shows erasing one `int64` signature costs 26 ms while
   erasing it *and* its callers costs nothing. Mixed typing is the expensive
   state; uniform typing at either extreme is cheap.
3. **Static compilation is worth 2-8x where it is worth anything**, and roughly
   nothing on a third of the suite. Same source, same JIT, only the compilation
   path differs.
4. **Three cinderx crashes fall out of this**, two of them JIT bugs, all
   reproducible in under 12 lines. Written up separately in
   [jit_bug.md](jit_bug.md).
5. **The old test suite was not testing what it looked like it was testing**:
   830 masks "passed" runtime checks while executing zero benchmark code. Fixed;
   see [The test.py hole](#the-testpy-hole).

## Figures

| file | what it shows |
|---|---|
| `bimodal.png` | the factorial's two findings: bimodal times, and mixed typing being the cost |
| `factorial.png` | all 127 effects for call_method_slots, compiled and plain, with half-normal plots |
| `typedness2.png` | 12 benchmarks, time vs typedness, statically compiled vs run directly, with coercion counts |
| `typedness.png` | the first sweep (JIT inliner on, so deltablue and nbody are missing) |

---

## 1. The typedness sweep

`typedness_sweep2.py`: for each of the 12 advanced benchmarks, up to 10 evenly
spaced proportions of typedness, 10 random masks at each, benchmark granularity.
Every mask runs twice, once statically compiled (`run_compiled.py`) and once as a
plain script (`run_plain.py`), both with the JIT at `compile_after_n_calls(0)` and
the HIR inliner off. A mask whose compiled run fails is logged and replaced by
another draw at the same proportion, up to 5 extra draws.

721 masks kept, 120 failures, 38.7 minutes. Results in
`typedness2_results.json`, plotted in `typedness2.png`.

### Static compilation vs plain, fully typed

Ratio is plain / compiled, so above 1 means static compilation wins. From a
separate 720-run pass over all three variants, 10 runs each:

| benchmark | compiled | plain | ratio |
|---|---|---|---|
| fannkuch | 0.1349 | 1.0239 | **7.59x** |
| richards | 0.0972 | 0.2953 | **3.04x** |
| held_karp | 0.1628 | 0.3524 | **2.17x** |
| call_simple | 0.0222 | 0.0392 | 1.76x |
| nqueens | 0.0533 | 0.0887 | 1.67x |
| deltablue | 0.2182 | 0.3525 | 1.62x |
| float | 0.1098 | 0.1389 | 1.26x |
| call_method | 0.0662 | 0.0642 | 0.97x |
| call_method_slots | 0.0665 | 0.0632 | 0.95x |
| chaos | 0.1189 | 0.1122 | 0.94x |
| pystone | 0.5396 | 0.1913 | **0.35x** |
| nbody | crash | 0.6004 | — |

Two checks that make these trustworthy: every `untyped` variant lands at
0.97-1.00x (compiling a file with no annotations changes nothing, as it must),
and the `shallow` variant is *worse* than plain nearly everywhere (0.47-0.85x)
while `advanced` wins — consistent with point 2 above, partial annotation costing
more than it buys.

### Shapes, by benchmark

- **held_karp, deltablue**: compiled time falls as typedness rises
  (0.19 → 0.14, 0.215 → 0.17). The expected result.
- **pystone**: rises monotonically with typedness, 0.215 → 0.415, while its plain
  line sits flat at 0.147. Zero coercions at every proportion, so this is not
  bridging overhead — the static code itself is slower than the dynamic path.
- **call_method, call_method_slots**: a hump. Both extremes ~0.050, middle
  ~0.069. See section 2 — this is the boundary effect, and the factorial
  explains it exactly.
- **fannkuch, float, richards, nqueens, call_simple**: compiled essentially flat
  across typedness and well below plain. Erasure barely moves them.
- **nbody**: only proportions 0.00-0.44 have data; the rest is a JIT bug.

## 2. The factorial: what a unit is worth, and what pairs are worth

call_method_slots has 7 units, so all 128 masks fit and nothing has to be
inferred from a fraction of the design. `factorial_call_method_slots.py` ran
every mask 8 times in both modes in randomized order (2048 runs, 25.9 min, zero
failures); `analyze_factorial.py` computes all 127 effects by Walsh-Hadamard
contrast, tests each against the pooled within-cell variance, and applies
Benjamini-Hochberg across the 127.

Convention: **+ means erasing that unit is slower**, i.e.
mean(erased) − mean(kept).

Noise floor: within-cell sd **1.37 ms** on 896 df, Lenth PSE 0.083 ms. So a 3 ms
effect is real and a 0.05 ms effect is not.

### Main effects, compiled

| unit | what it covers | effect | q |
|---|---|---|---|
| C | `args a,b,c: int64` | **+15.86 ms** | 0 |
| E | `args a: int64` | −9.41 ms | 0 |
| D | `args a,b: int64` | −3.79 ms | 9e-227 |
| B | `args a,b,c,d: int64` | −2.90 ms | 9e-162 |
| A | all 7 function defs and `__slots__` | −0.22 ms | 0.063 |
| F | local `f` | +0.02 ms | 0.89 |
| G | locals `startTime, runtime, endTime` | +0.05 ms | 0.89 |

The int64 parameter groups carry the entire effect. The function definitions and
the local variable annotations are free: erase them and nothing measurable
happens. That is a useful result on its own — not all annotations are worth the
same, and here the split is clean rather than graded.

### Interactions are half the variance

19 of 127 terms clear q<.05: 4 main, 6 two-way, 9 higher. Variance by order:

```
1-way 53.1%   2-way 40.9%   3-way 4.8%   4-way 1.2%   5-way+ 0.0%
```

The C/D/E cube is the whole story:

| C | D | E | ms |
|---|---|---|---|
| kept | kept | kept | 51.2 |
| kept | kept | erased | 52.1 |
| kept | **erased** | kept | **76.9** |
| kept | erased | erased | 52.5 |
| **erased** | kept | kept | **77.9** |
| erased | kept | erased | 78.9 |
| erased | erased | kept | 75.6 |
| erased | erased | erased | **50.0** |

Erasing D alone costs 26 ms. Erasing D and E together costs 1 ms. Erasing C, D
and E together is 50.0 ms, *faster than fully typed*. The penalty is a boundary
between an int64 signature and a dynamic caller; remove both sides of the
boundary and it goes away.

This is also why the sweep's ∩-shaped timing curves exist: a mid-typedness mask
is the one most likely to split a call chain, and the extremes cannot.

### The response is bimodal

All 128 compiled cell means sit in two clumps, ~50 ms and ~78 ms, with almost
nothing in between (`bimodal.png`, left). Erasure does not degrade performance
gradually; it either keeps the JIT on a fast path or knocks it off, a 56% cliff.
That is worth knowing before modelling typedness as a continuous dial.

### Plain mode is the control, and it behaves like one

Same design, no static compilation: within-cell sd 8.04 ms (6x the compiled
noise), only 5 of 127 terms significant, largest effect 6.1 ms against 15.9, and
D and E flip sign. With no static types there is no boundary to pay for.

## 3. Coercions

`typedness_sweep2.py` counts `cast()`, `box()` and primitive-constructor calls in
each emitted program, alongside what the original source already contained.

- **The ∩ shape is the correct signature.** Coercions exist only at typed↔dynamic
  boundaries, and both extremes have none: fully typed needs no bridging, fully
  erased is uniformly dynamic. deltablue 7 → 35 → 7, held_karp 21 → 40 → 21,
  call_method_slots 0 → 84 → 0. A flat-high or monotone curve would be the bad
  sign, meaning defensive wrapping away from boundaries. ∩ is necessary but not
  sufficient for minimality — it says nothing about over-wrapping *at* a
  boundary.
- **fannkuch and nbody are monotone up toward erasure, and that is also
  correct.** Their type source is `Array[int64]` / `CheckedDict`, which no mask
  can erase. Every erased local becomes `Any` while the container stays
  primitive, so erasing *creates* boundaries:
  `count[i] = int64(i + 1)`, `k: Any = box(perm[0])`. Meanwhile
  `perm[i] = perm1[i]` (int64 → int64) is left unwrapped, so the selector is not
  blanket-wrapping.
- **pystone, chaos and float sit at exactly zero, and that is correct too.** They
  contain no primitives at all, only `CheckedList[int]`, `CheckedList[Point]`,
  `CheckedList[GVector]` — all boxed element types. pystone's one apparent
  `int64` is inside a docstring (`main.py:44`). Nothing to bridge.
- **richards' apparent spike was a metric artifact**, now fixed. Its source
  contains 36 author-written wrappers, and erasing annotations makes the detyper
  *delete* them, so a signed delta goes to −30 and was being clipped below a
  zero-based axis. As absolute counts: 36 as written → 37.7 around 0.8 typed →
  6 fully erased.

### One genuine selection miss

`samples_temp/nbody/max_bits09of09.py:127,129`

```python
double(report_energy(SYSTEM, PAIRS))     # original: report_energy(SYSTEM, PAIRS)
```

A bare expression statement — the value is discarded. The coercion is pure
overhead and can raise on a non-numeric where the bare call would not. This is
the same class as the existing `bare_test` case in `type_mediator.py`, which
already skips coercions in `if`/`while` tests because nothing reads them.
`ast.Expr` statements want the same treatment. Not yet fixed.

## 4. The test.py hole

`static_runner.py` imports the benchmark module and then calls `main()` if the
module has one. Of the 12 golden benchmarks, **only held_karp had a `main()`**.
For the other 11 the import defined classes and functions and executed no
benchmark code at all, so test.py's "runtime" phase was compile-only for 11 of
12 while reporting 830 passing masks.

Fixed by wrapping each benchmark's `if __name__ == "__main__":` block in a
`main()` that the guard then calls — behaviour for a direct `python main.py` run
is unchanged, and all 12 were verified to still run standalone.

A second harness bug surfaced immediately: four benchmarks read an iteration
count from `sys.argv[1]`, and the runner was passing the temp module path, so
`main()` died on `int('/var/folders/.../bench_module.py')`. 254 of the first
run's 465 failures were that artifact. `static_runner.py` now blanks `sys.argv`.

### What the fixed suite reports

894 cases × 2 modes, 8 workers, **23.5 minutes**:

| category | count | phase |
|---|---|---|
| cinderx JIT — inliner (deltablue, `expected 'Planner' ... got 'tuple'`) | 124 | runtime |
| cinderx JIT — codegen (nbody, `incorrect register type.`) | 72 | runtime |
| detyper — `cannot divide dynamic and double` (nbody) | 15 | runtime |
| detyper — `cannot divide dynamic and double` (nbody) | 38 | compile |
| **new type errors from erasure** | **0** | — |

compile 871/894 ok, runtime 698/894 ok, zero regressions. With workloads actually
executing, the detyper produces exactly one genuine bug class — 53 nbody masks
where erasure leaves a dynamic value in a division with a `double` and the
coercion pass does not bridge it. Everything else is cinderx.

## 5. Tooling this produced

| file | purpose |
|---|---|
| `run_compiled.py` | run any file statically compiled, as `__main__`, so its `if __name__` block executes |
| `run_plain.py` | matched counterpart: same JIT settings, no static compilation |
| `timing_runner.py` | import a module under the static loader; `--no-jit`, `--no-inliner` |
| `typedness_sweep.py` | proportions-of-typedness sweep (first version, compiled only) |
| `typedness_sweep2.py` | both modes, mask resampling on failure, coercion counts, progress bar |
| `plot_typedness.py`, `plot_typedness2.py`, `plot_bimodal.py` | the figures |
| `factorial_call_method_slots.py` | full 2⁷ enumeration, randomized replicates |
| `analyze_factorial.py` | Hadamard effects, F-tests, BH correction, Lenth PSE, half-normal plots |
| `jit_bugs/`, `wtf.py`, `wtf2.py` | minimal reproducers for the cinderx crashes |

`run_compiled.py` is the useful one beyond this investigation: it builds a strict
loader named `__main__`, makes a module from that spec and execs it, which is the
only way to get a file's `__main__` block statically compiled — Static Python's
compiler is an import path hook, so a script run directly is compiled by CPython
before any of its own code can install anything.

## 6. What is still open

- The `ast.Expr` coercion miss above.
- The `cannot divide dynamic and double` gap in nbody, 53 masks.
- deltablue has one incomplete proportion in the sweep: 22 masks there failed
  with `TypeError: 'float' object cannot be interpreted as an integer`, which is
  neither of the known cinderx bugs and has not been investigated.
- Whether the two cinderx JIT bugs are regressions. The pinned submodule commit
  is *"Visit instruction arguments in reverse in register allocator when
  calculating last-use data"* — a register allocator change, and one of the bugs
  is a register-type assertion. Testing the parent commit means rebuilding
  cinderx.
- Coercion counts have no lower bound to compare against. Counting typed↔dynamic
  boundary edges per mask from the graph would turn "∩ shaped" into "minimal or
  not".
- The factorial covers one benchmark. call_method (7 units), call_simple (6) and
  float (8) are all small enough to enumerate; held_karp (26) and deltablue (36)
  are not.

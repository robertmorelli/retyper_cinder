# Industrial-strength testing

The test system should answer two independent questions:

1. Does the graph predict the binder's post-erasure tables?
2. Does the detyper produce code that compiles, behaves the same, and performs
   well?

Use one case format, one mask plan, one parallel runner, and one result format
for both. This replaces the growing family of `crashtest_*` scripts without
combining unrelated metrics.

## One test case shape

A case is fully reproducible:

```python
Case(
    benchmark,
    variant,
    granularity,       # annotation or benchmark
    mask,
    seed=None,
)
```

A result is one JSON record:

```python
Result(
    case,
    phase,             # predict, detype, compile, run
    status,
    elapsed_ns,
    metrics={},
    error=None,
)
```

Workers write or return records; only the parent prints summaries and writes
the final JSONL file. A failure line always includes benchmark, variant,
granularity, mask, seed, phase, exception type, and final error line.

Store masks as integers. Sort cases before dispatch and results before summary
so worker completion order does not affect output.

## One mask plan

Generate the same plan for prediction and detyper tests:

1. no erasure,
2. full erasure,
3. every single unit,
4. every inverse-single unit,
5. every pair when the unit count is modest,
6. exhaustive masks when `2**n` is below a configured limit,
7. seeded random masks otherwise.

Random masks should be stratified by density rather than choosing a uniformly
random integer. For each benchmark, sample masks near 10%, 25%, 50%, 75%, and
90% erased. This exercises small perturbations, difficult merges, and nearly
full erasure without requiring a huge count.

Use benchmark granularity as the primary experiment. Annotation granularity is
a diagnostic view and should use the same planner, not separate scripts.

The plan is deterministic from a checked-in seed. On failure, print the exact
mask; replay accepts one `Case` directly.

## Parallel runner

Use process workers, not threads. Binding creates compiler state and CPU-heavy
work, and process isolation also prevents one failed bind from corrupting a
later case.

A simple runner is enough:

```text
parent: enumerate cases
parent: ProcessPoolExecutor(max_workers=N)
worker: load source, execute one case, return Result records
parent: aggregate and report
```

Do not add nested parallelism. A worker owns one case from initial bind through
its requested phase. Give every subprocess a timeout and kill its process group
on expiry.

Default `N` to the available CPU count, with a command-line override. Keep a
serial `--workers 1` mode for debugging.

## 1. Binding predictive power

### Ground-truth procedure

For each case:

1. Bind the original source and build the one graph.
2. Settle the graph with the selected erasure units disabled.
3. On a structurally identical copy, remove those annotations without adding
   detyper patches.
4. Ask cinderx to bind that erased program.
5. If it binds, align original and rebound nodes and compare predicted tables.
6. If it does not bind, record `no_ground_truth` rather than counting it as an
   inaccurate prediction.

The graph and actual rebind must see the same erasure semantics, including the
explicit checked-container constructor rewrite. Otherwise the comparison is
between two different programs.

### Node alignment

Do not align by AST object identity or by unparse/reparse order. Compiler
rewrites can replace nodes.

Use a stable structural key:

```text
(filename, node kind, lineno, col_offset, end_lineno, end_col_offset,
 occurrence-at-that-position)
```

Report missing and extra keys separately. Exclude synthetic nodes unless both
sides intentionally expose the same synthetic shape.

### Type identity

Never compare `Value` objects across binds and never use one bind's `DYNAMIC`
inside another. Normalize each value to a stable description:

```text
dynamic
None
klass.type_descr
klass.type_name.readable_name as fallback
```

Keep primitive exactness in the description. `int64`, `int`, `cbool`, and
`bool` must remain distinct.

### Metrics

Collect counts, not only percentages:

- exact type,
- exact context,
- exact dynamic-versus-nondynamic type,
- exact dynamic-versus-nondynamic context,
- whole-mask exactness,
- missing/extra aligned nodes,
- original bind failure,
- erased rebind failure (`no_ground_truth`),
- graph settlement crash or timeout.

Report both:

- **micro average:** all compared nodes together,
- **macro average:** equal weight per case and per benchmark.

Also partition results by erasure density and granularity. Always print the
denominators. “90% exact” is not useful unless it says how many nodes, cases,
and erased programs were comparable.

### Avoid the easy-case trap

A real rebind exists only for erased programs cinder accepts. Those are the easy
cases. Therefore publish two numbers side by side:

```text
prediction accuracy among rebound cases
coverage: rebound cases / all attempted cases
```

For cases without ground truth, the detyper compile/run tests below are the
important signal. Do not silently drop them from the overall case count.

### High-power schedule

Use three presets, all generated by the same planner:

- **smoke:** no/full/singles, suitable for every edit,
- **standard:** inverse singles, pairs, and a modest seeded random sample,
- **high:** exhaustive small graphs plus thousands of stratified random masks.

Parallelism makes `high` practical, but deterministic seeds keep it debuggable.
A failing high-power case should be copied into a small checked-in regression
case before changing graph rules.

## 2. Detyper compile-time and runtime performance

Correctness is a gate. Do not include a case in performance ratios unless its
output binds, runs successfully, and passes its semantic check.

### Outputs per case

Build three artifacts where available:

```text
original typed source
detyped source
erased-unpatched source (diagnostic only)
```

The performance comparison is typed versus detyped. The unpatched artifact is
for binder prediction and is not expected to compile.

### Compile-time measurement

Measure these separately:

1. original cinder bind time,
2. graph build time,
3. graph settle time,
4. annotation removal and patch materialization time,
5. stage-two rebind/simplification time,
6. final Static Python import/compile time.

Also report the end-to-end detyper transformation time. Individual phases show
where a regression came from; end-to-end time says what a user pays.

Use `perf_counter_ns()`. Warm filesystem caches before recording samples. Run
multiple repetitions and report median and median absolute deviation. Compile
in a fresh temporary module name each repetition so module caches do not turn a
compile test into an import-cache test.

The existing `static_runner.py` path is correct: Static Python must see an
imported module. Add a compile-only worker mode rather than timing benchmark
execution as part of compilation.

Useful ratios are:

```text
detyped static compile / typed static compile
graph build + settle / original bind
end-to-end detype / original static compile
```

Retain absolute milliseconds as well as ratios.

### Runtime correctness

Before timing, run typed and detyped variants with the same inputs and compare a
small semantic result:

- exit status,
- stdout or an explicit checksum/result,
- exception type when an exception is expected.

Avoid comparing timing-oriented log lines. If a benchmark currently only
prints results, normalize that output rather than introducing a second benchmark
implementation.

A binding success is not runtime correctness. Keep compile and run statuses as
separate result phases.

### Runtime measurement

For each accepted case:

1. compile typed and detyped modules ahead of timing,
2. start the Static Python loader and enable the same JIT settings,
3. warm both variants,
4. alternate their order across repetitions,
5. run the same benchmark entry point and inputs,
6. record individual samples,
7. report median time and `detyped / typed` ratio.

Use enough inner iterations that startup noise is small relative to benchmark
work. If process startup is part of the intended measurement, report it as a
separate cold-start metric rather than mixing it into steady-state runtime.

### Parallel performance runs

Parallelize by case, with typed and detyped measurements for one case kept in
the same worker. This preserves paired comparison and shared machine conditions.
Do not measure the typed side in one worker and the detyped side in another.

Concurrent timing creates CPU contention. Keep the design simple:

- allow `--workers N` for high-throughput performance sweeps,
- use one worker per physical core at most,
- optionally pin each worker to a core when the platform supports it,
- report worker count and machine metadata,
- use paired ratios as the primary parallel result,
- confirm release numbers with `--workers 1` or an otherwise idle pinned run.

Parallel compile measurements are generally stable enough for regression
screening. Parallel runtime measurements are excellent for finding large
regressions, but small claimed wins require the serial confirmation run.

### Runtime mask volume

Do not runtime-benchmark thousands of random masks. That spends power in the
wrong place.

Use the high-power plan for binding and compile correctness. For runtime use a
small deterministic performance subset:

- no erasure,
- full erasure when it works,
- representative 25%, 50%, and 75% masks,
- any historically problematic mask.

Compile-time transformation can be measured over the larger standard plan.
Steady-state runtime should focus on enough representative outputs to identify
boxing, casting, and lost-static-compilation costs.

## Reporting and regression policy

Produce one machine-readable JSONL artifact and one concise summary.

The summary should contain:

```text
cases attempted / completed / timed out
rebind coverage and prediction metrics
detyper bind success
static-module success
runtime semantic success
compile-time phase medians and ratios
runtime medians and ratios
failures grouped by stable error message
```

Compare against a checked-in or CI-provided baseline. Fail CI on:

- a new graph crash,
- a reduction in rebound prediction accuracy beyond tolerance,
- a detyper compile or semantic regression,
- a large compile/runtime regression beyond a configured threshold.

Do not fail on tiny timing movement from one run. Require a minimum sample count
and a threshold larger than observed noise.

## Consolidating the current scripts

Replace the behavior of these scripts with presets over the one runner:

- `crashtest.py`
- `crashtest_ones.py`
- `crashtest_bench_ones.py`
- `crashtest_random.py`
- `crashtest_random_bench.py`

For example:

```text
python test_graph.py predict --plan high --workers 12
python test_graph.py compile --plan standard --workers 12
python test_graph.py runtime --plan perf --workers 6
python test_graph.py replay --case case.json --workers 1
```

The old filenames can temporarily be tiny wrappers around these commands, then
be deleted once callers migrate. `run_bench.py` and `static_runner.py` can be
retained as worker helpers until the common runner fully absorbs them.

The simplification target is one case generator, one worker protocol, one result
schema, and several small presets—not one giant test function.

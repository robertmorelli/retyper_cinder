"""How fast is a benchmark at each proportion of typedness?

For every advanced benchmark: pick up to 10 proportions of typedness, and at
each one run 10 random masks that erase that proportion of the benchmark's
annotation units. The number that gets averaged is the one the benchmark prints
itself -- these files time their own workload -- not the wall time of the
subprocess, which is mostly interpreter startup.

Masks are benchmark granularity, so a unit is a whole annotation group and a
mask of n bits picks which groups get erased. Typedness is 1 - popcount/n: 1.0
is the untouched source, 0.0 is everything erased. Both ends have exactly one
mask, so they get one sample each rather than ten of the same thing.

Runs are serial by default. These are timings; a pool of workers competing for
cores would measure the pool.

  python typedness_sweep.py --estimate     time the two endpoints, project the run
  python typedness_sweep.py                the full sweep -> typedness_results.json
  python typedness_sweep.py --benchmark pystone --samples 3
  python typedness_sweep.py --no-jit       nbody and deltablue only run this way

Then: python3 plot_typedness.py (system python, for matplotlib).
"""
from argparse import ArgumentParser
from ast import AsyncFunctionDef, Compare, Constant, FunctionDef, If, Name
from ast import fix_missing_locations, parse, unparse
from json import dump
from math import comb
from os import path
from random import Random
from statistics import mean, median, stdev
from subprocess import run as run_subprocess
from sys import executable, stderr
from tempfile import TemporaryDirectory
from time import perf_counter_ns

from cinderx_binding import get_ast_data
from detyper import detype
from list_benchmarks import get_bench_list
from load_source import load_bench
from typedness_graph import build_binding_graph

RUNNER = path.join(path.dirname(path.abspath(__file__)), "timing_runner.py")
GRANULARITY = "benchmark"
VARIANT = "advanced"
SKIP = {"scratch"}


def unit_count(bench):
    """How many annotation units a mask for this benchmark chooses among."""
    try:
        data = get_ast_data(parse(load_bench(bench, VARIANT)))
        return len(build_binding_graph(data).units(GRANULARITY))
    except Exception:
        return 0


def _is_main_guard(node):
    if not isinstance(node, If):
        return False
    test = node.test
    return (isinstance(test, Compare) and isinstance(test.left, Name)
            and test.left.id == "__name__" and len(test.comparators) == 1
            and isinstance(test.comparators[0], Constant)
            and test.comparators[0].value == "__main__")


def hoist_main_guard(tree):
    """Splice the `__main__` block into the module body it is guarding.

    Static Python compiles imported modules, and an import leaves the guard
    unexecuted, so the workload has to be moved out of it. Hoisting to module
    level rather than into a function keeps every name a global with the
    annotation it already had -- the same scope the benchmark author wrote it
    in, so nothing about how it compiles changes.
    """
    body, found = [], False
    for node in tree.body:
        if _is_main_guard(node):
            body.extend(node.body)
            found = True
        else:
            body.append(node)
    if not found:
        raise ValueError("no `if __name__ == \"__main__\"` block")
    tree.body = body
    return fix_missing_locations(tree)


def has_top_level_main(source):
    return any(isinstance(n, (FunctionDef, AsyncFunctionDef)) and n.name == "main"
               for n in parse(source).body)


def levels(n, proportions):
    """Erased-unit counts for evenly spaced proportions, low to high typedness.

    round() collapses neighbours on a small benchmark: n=2 has three levels, not
    ten, and asking for ten of them would just run the same mask repeatedly.
    """
    if n == 0:
        return []
    if proportions == 1:
        return [n]
    ks = {round(n * i / (proportions - 1)) for i in range(proportions)}
    return sorted(ks, reverse=True)


def masks_for(n, k, samples, rng):
    """Up to `samples` distinct masks with exactly k bits set."""
    if k == 0:
        return [0]
    if k == n:
        return [(1 << n) - 1]
    want = min(samples, comb(n, k))
    out = set()
    while len(out) < want:
        out.add(sum(1 << i for i in rng.sample(range(n), k)))
    return sorted(out)


def run_mask(source, mask, timeout, no_jit, less_any):
    """Detype, run, and return what the benchmark printed for itself.

    detype reads mask=0 as "erase everything", so the fully typed end of the
    sweep is the source itself rather than a detype of it -- the same
    convention test.py follows.

    A benchmark prints one float last; anything it printed before that is its
    own output and gets ignored.
    """
    try:
        tree = (parse(source) if mask == 0 else
                detype(source, mask=mask, bench=True, less_any=less_any))
        text = unparse(hoist_main_guard(tree))
    except Exception as exc:
        return {"status": "detype_failure", "error": f"{type(exc).__name__}: {exc}"}
    with TemporaryDirectory() as tmp:
        module_path = path.join(tmp, "bench_module.py")
        with open(module_path, "w") as f:
            f.write(text)
        cmd = [executable, RUNNER, module_path, "--require-static"]
        if no_jit:
            cmd.append("--no-jit")
        start = perf_counter_ns()
        try:
            proc = run_subprocess(cmd, capture_output=True, text=True,
                                  timeout=timeout)
        except Exception as exc:
            return {"status": "timeout", "error": f"{type(exc).__name__}: {exc}",
                    "wall_ns": perf_counter_ns() - start}
        wall_ns = perf_counter_ns() - start
    if proc.returncode:
        tail = proc.stderr.strip().splitlines()
        return {"status": "run_failure", "wall_ns": wall_ns,
                "error": tail[-1] if tail else f"exit {proc.returncode}"}
    lines = proc.stdout.strip().splitlines()
    try:
        seconds = float(lines[-1])
    except (IndexError, ValueError):
        tail = lines[-1][:60] if lines else ""
        return {"status": "no_timing", "wall_ns": wall_ns,
                "error": f"last stdout line: {tail!r}"}
    return {"status": "ok", "seconds": seconds, "wall_ns": wall_ns}


def sweep_benchmark(bench, args, log):
    source = load_bench(bench, VARIANT)
    n = unit_count(bench)
    rng = Random(args.seed + sum(map(ord, bench)))
    rows = []
    for k in levels(n, args.proportions):
        for mask in masks_for(n, k, args.samples, rng):
            samples = [run_mask(source, mask, args.timeout, args.no_jit,
                                args.less_any) for _ in range(args.repeat)]
            ok = [s["seconds"] for s in samples if s["status"] == "ok"]
            row = {"benchmark": bench, "units": n, "erased": k,
                   "typedness": 1 - k / n if n else None, "mask": mask,
                   "seconds": median(ok) if ok else None,
                   "runs": samples,
                   "status": "ok" if ok else samples[0]["status"],
                   "error": next((s.get("error") for s in samples
                                  if s.get("error")), None)}
            rows.append(row)
            log(f"  {bench} k={k}/{n} typedness={row['typedness']:.2f} "
                f"mask={mask} {row['status']}"
                + (f" {row['seconds']:.4f}s" if ok else f" {row['error'] or ''}"[:70]))
    return n, rows


def aggregate(rows):
    """Mean over the masks at each proportion, plus what failed there."""
    out = {}
    for row in rows:
        out.setdefault((row["benchmark"], row["erased"]), []).append(row)
    points = []
    for (bench, k), group in sorted(out.items(), key=lambda kv: (kv[0][0], -kv[0][1])):
        ok = [r["seconds"] for r in group if r["status"] == "ok"]
        points.append({
            "benchmark": bench, "units": group[0]["units"], "erased": k,
            "typedness": group[0]["typedness"], "masks": len(group),
            "ok": len(ok),
            "mean_seconds": mean(ok) if ok else None,
            "median_seconds": median(ok) if ok else None,
            "stdev_seconds": stdev(ok) if len(ok) > 1 else 0.0 if ok else None,
            "min_seconds": min(ok) if ok else None,
            "max_seconds": max(ok) if ok else None,
            "failures": [r["status"] for r in group if r["status"] != "ok"]})
    return points


def estimate(benches, args):
    """Time the fully typed and fully erased runs, project the whole sweep.

    The two endpoints bracket every mask in between -- erasing annotations only
    makes a benchmark slower -- so their mean times the run count is a fair
    projection, and it costs 2 runs per benchmark to get.
    """
    total_runs, total_ns, table = 0, 0, []
    for bench in benches:
        n = unit_count(bench)
        ks = levels(n, args.proportions)
        runs = sum(len(masks_for(n, k, args.samples, Random(0))) for k in ks) * args.repeat
        typed = run_mask(load_bench(bench, VARIANT), 0, args.timeout,
                         args.no_jit, args.less_any)
        erased = run_mask(load_bench(bench, VARIANT), (1 << n) - 1, args.timeout,
                          args.no_jit, args.less_any)
        walls = [s["wall_ns"] for s in (typed, erased) if s.get("wall_ns")]
        per_run = mean(walls) if walls else 0
        table.append((bench, n, len(ks), runs, typed, erased, per_run))
        total_runs += runs
        total_ns += per_run * runs
        print(f"  {bench:18s} n={n:2d} levels={len(ks):2d} runs={runs:4d} "
              f"typed={typed['status']}/{typed.get('seconds')} "
              f"erased={erased['status']}/{erased.get('seconds')} "
              f"~{per_run / 1e9:.2f}s per run", file=stderr)
    print(f"\n{total_runs} runs, projected {total_ns / 1e9 / 60:.1f} minutes "
          f"serial ({total_ns / 1e9:.0f}s)", file=stderr)
    return table


def main():
    parser = ArgumentParser()
    parser.add_argument("--proportions", type=int, default=10)
    parser.add_argument("--samples", type=int, default=10,
                        help="random masks per proportion")
    parser.add_argument("--repeat", type=int, default=1,
                        help="runs per mask; the median is kept")
    parser.add_argument("--seed", type=int, default=20260812)
    parser.add_argument("--benchmark", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--no-jit", action="store_true",
                        help="run interpreted; nbody and deltablue need it")
    parser.add_argument("--less-any", action="store_true",
                        help="erase to a deleted annotation, not to Any")
    parser.add_argument("--out", default="typedness_results.json")
    parser.add_argument("--estimate", action="store_true",
                        help="time both endpoints per benchmark and project")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    wanted = set(args.benchmark)
    benches = [b for b, v, _ in get_bench_list()
               if v == VARIANT and b not in SKIP and (not wanted or b in wanted)]

    if args.estimate:
        estimate(benches, args)
        return 0

    log = (lambda msg: None) if args.quiet else (lambda msg: print(msg, file=stderr))
    started = perf_counter_ns()
    rows = []
    for bench in benches:
        n, bench_rows = sweep_benchmark(bench, args, log)
        rows.extend(bench_rows)
        log(f"{bench}: {sum(r['status'] == 'ok' for r in bench_rows)}"
            f"/{len(bench_rows)} masks ran")

    results = {"variant": VARIANT, "granularity": GRANULARITY,
               "seed": args.seed, "samples": args.samples,
               "proportions": args.proportions, "repeat": args.repeat,
               "jit": not args.no_jit, "less_any": args.less_any,
               "elapsed_s": (perf_counter_ns() - started) / 1e9,
               "points": aggregate(rows), "masks": rows}
    with open(args.out, "w") as f:
        dump(results, f, indent=1)
    ok = sum(r["status"] == "ok" for r in rows)
    print(f"{ok}/{len(rows)} masks ran, "
          f"{results['elapsed_s'] / 60:.1f} minutes -> {args.out}", file=stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

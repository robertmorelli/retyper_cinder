"""Write readable detyped samples to a directory, one per mask.

`python samples/write_samples_tool.py` and read the files. This is for looking at output
with your eyes -- what the coercion pass actually emits -- not for deciding
whether it works; `testing/mask_harness.py` is the judge of that.

Each benchmark gets its own directory: the untouched source as
typed_original.py, one file per sampled mask, and everything erased as
max_bits<n>of<n>.py. INDEX.txt lists every file with its mask and the verdict
the strict loader reached, so a sample that does not compile is still written
and still labelled rather than quietly missing.

Nothing is compiled or run by default -- this writes files, and writing them
is a detype apiece. Checking them means a subprocess apiece, and running them
means running the benchmarks: minutes each, and a bad mask hangs until the
120s timeout. That is the mask harness's job. `--check` and `--run` ask for it anyway.

  python samples/write_samples_tool.py     samples/, 3 masks a benchmark
  python samples/write_samples_tool.py --count 15      more masks each
  python samples/write_samples_tool.py --benchmark deltablue --benchmark nbody
  python samples/write_samples_tool.py --check         compile each one too
"""
from argparse import ArgumentParser
from concurrent.futures import ProcessPoolExecutor
from os import cpu_count, listdir, makedirs, path, remove
from random import Random
from shutil import rmtree
from sys import path as import_path, stderr

ROOT = path.dirname(path.dirname(path.abspath(__file__)))
for directory in (path.join(ROOT, "src"), ROOT):
    if directory not in import_path:
        import_path.insert(0, directory)

from testing.mask_harness import _run_module, _error
from utilities.graph import count_benchmark_units
from utilities.list_benchmarks import get_bench_list
from utilities.load_source import detyped_source, load_bench

GRANULARITY = "benchmark"
VARIANT = "advanced"
SKIP = {"scratch"}
DENSITIES = (.1, .25, .5, .75, .9)


def sample_masks(n, count, seed):
    """`count` masks at mixed densities, and never 0 or everything.

    Both ends are already covered -- 0 is typed_original.py and full is the
    max file -- so a sample that lands on either is thrown back rather than
    written twice under a different name.
    """
    if n == 0:
        return []
    full = (1 << n) - 1
    rng = Random(seed)
    masks, attempts = set(), 0
    while len(masks) < count and attempts < count * 50:
        attempts += 1
        density = rng.choice(DENSITIES)
        k = max(1, min(n, round(n * density)))
        mask = sum(1 << i for i in rng.sample(range(n), k))
        if mask not in (0, full):
            masks.add(mask)
    return sorted(masks)


def name_for(index, mask, n):
    """`mask01_bits04of35.py`, or `max_...` for index 0, everything erased."""
    bits = bin(mask).count("1")
    stem = "max" if index == 0 else f"mask{index:02d}"
    return f"{stem}_bits{bits:02d}of{n:02d}.py"


def render(bench, mask, n):
    """The detyped source, with a header saying what produced it."""
    source = load_bench(bench, VARIANT)
    body = detyped_source(source, mask, GRANULARITY)
    bits = bin(mask).count("1")
    return (f"# {bench}/{VARIANT}  granularity={GRANULARITY}\n"
            f"# mask={mask}  ({bits}/{n} units erased)\n\n{body}\n")


def verdict(source, run_it):
    """What the strict loader makes of it: compiled, ran, or neither.

    Compiling is cheap and is the default. Running is not: these are
    benchmarks, several take minutes and one mask can hang until the 120s
    timeout, so a full pass spends nearly all of its time executing code that
    the mask harness already executes. `--run` asks for it anyway; the harness is what
    actually judges the output.
    """
    proc, _ = _run_module(source, compile_only=True)
    if proc.returncode:
        return "compile_failure", proc.stderr.strip().splitlines()[-1]
    if not run_it:
        return "compiles", ""
    proc, _ = _run_module(source)
    if proc.returncode:
        return "runtime_failure", proc.stderr.strip().splitlines()[-1]
    return "ok", ""


def one_sample(job):
    bench, index, mask, n, check, run_it = job
    filename = name_for(index, mask, n)
    try:
        text = render(bench, mask, n)
    except Exception as exc:
        return bench, filename, mask, n, "detype_failure", _error(exc), None
    status, error = verdict(text, run_it) if check else ("unchecked", "")
    return bench, filename, mask, n, status, error, text


def jobs_for(bench, count, seed, check, run_it):
    n = count_benchmark_units(bench, VARIANT, GRANULARITY)
    masks = sample_masks(n, count, seed)
    out = [(bench, i, mask, n, check, run_it)
           for i, mask in enumerate(masks, 1)]
    if n:
        # index 0 is the max file: everything erased, always worth seeing
        out.append((bench, 0, (1 << n) - 1, n, check, run_it))
    return n, out


def main():
    parser = ArgumentParser()
    parser.add_argument("--out")
    parser.add_argument("--count", type=int, default=3,
                        help="masks per benchmark, on top of the max mask")
    parser.add_argument("--seed", type=int, default=20260811)
    parser.add_argument("--benchmark", action="append", default=[])
    parser.add_argument("--workers", type=int, default=cpu_count() or 1)
    parser.add_argument("--check", action="store_true",
                        help="also compile each sample through the strict "
                             "loader; a subprocess per sample")
    parser.add_argument("--run", action="store_true",
                        help="also execute each sample; slow, these are "
                             "benchmarks and one mask can hang until timeout")
    args = parser.parse_args()

    out_dir = args.out or path.join(ROOT, "samples")
    wanted = set(args.benchmark)
    benches = [b for b, v, _ in get_bench_list()
               if v == VARIANT and b not in SKIP
               and (not wanted or b in wanted)]

    jobs, shapes = [], {}
    for index, bench in enumerate(benches):
        n, bench_jobs = jobs_for(bench, args.count, args.seed + index,
                                 args.check or args.run,
                                 args.run)
        shapes[bench] = n
        jobs.extend(bench_jobs)

    if path.abspath(out_dir) == path.dirname(path.abspath(__file__)):
        for entry in listdir(out_dir):
            target = path.join(out_dir, entry)
            if target == path.abspath(__file__) or entry == "__pycache__":
                continue
            rmtree(target) if path.isdir(target) else remove(target)
    elif path.isdir(out_dir):
        rmtree(out_dir)
    for bench in benches:
        makedirs(path.join(out_dir, bench), exist_ok=True)
        with open(path.join(out_dir, bench, "typed_original.py"), "w") as f:
            f.write(load_bench(bench, VARIANT))

    print(f"{len(jobs)} samples across {len(benches)} benchmarks -> {out_dir}",
          file=stderr)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(one_sample, jobs))

    lines, counts = [], {}
    for bench, filename, mask, n, status, error, text in results:
        counts[status] = counts.get(status, 0) + 1
        bits = bin(mask).count("1")
        if text is not None:
            with open(path.join(out_dir, bench, filename), "w") as f:
                f.write(text)
        note = f"  {error}" if error else ""
        lines.append(f"{bench}/{filename}  mask={mask}  {bits}/{n}  "
                     f"{status}{note}")

    header = (f"per-benchmark masks, {GRANULARITY} granularity, "
              f"seed={args.seed}")
    with open(path.join(out_dir, "INDEX.txt"), "w") as f:
        f.write(header + "\n\n" + "\n".join(lines) + "\n")

    print(" ".join(f"{k}={v}" for k, v in sorted(counts.items())), file=stderr)
    return 1 if set(counts) - {"ok", "compiles", "unchecked"} else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Fill the holes typedness_sweep2.py left, plain runs only.

typedness_sweep2 ran each mask compiled-first and dropped the mask entirely when
the compiled run failed, so nbody (codegen abort) and the top of deltablue have
no timings at all -- not even the plain ones, which would have been fine. This
script keeps every mask already timed in typedness2_results.json, then for each
benchmark/proportion still short of --samples masks re-runs the recorded failed
masks through run_plain.py only, drawing fresh masks if that is not enough.

  python3 typedness_fill_plain.py            -> typedness3_results.json
  python3 typedness_fill_plain.py --benchmark nbody
"""
from argparse import ArgumentParser
from ast import parse
from json import dump, load
from math import comb
from os import path
from random import Random
from statistics import median
from sys import stderr
from tempfile import TemporaryDirectory
from time import perf_counter

from load_source import load_bench
from typedness_sweep2 import (VARIANT, GRANULARITY, DRAWS, build, wraps,
                              draw, execute, levels)

BAR = 30


def time_plain(source, mask, runs, timeout):
    """(seconds, coercions_added, error) for one mask, plain run only."""
    try:
        text, added = build(source, mask)
    except Exception as exc:
        return None, None, f"detype: {type(exc).__name__}: {exc}"
    with TemporaryDirectory() as tmp:
        module_path = path.join(tmp, "bench_module.py")
        with open(module_path, "w") as f:
            f.write(text)
        samples = []
        for _ in range(runs):
            seconds, error = execute("run_plain.py", module_path, timeout)
            if error:
                return None, added, error
            samples.append(seconds)
    return median(samples), added, None


def aggregate(rows, originals):
    groups = {}
    for row in rows:
        groups.setdefault((row["benchmark"], row["erased"]), []).append(row)
    points = []
    for (bench, k), group in sorted(groups.items(),
                                    key=lambda kv: (kv[0][0], -kv[0][1])):
        written = originals[bench]
        added = [r["coercions"] for r in group]
        vals = [r["plain"] for r in group if r.get("plain") is not None]
        points.append({
            "benchmark": bench, "units": group[0]["units"], "erased": k,
            "typedness": group[0]["typedness"], "masks": len(group),
            "coercions_mean": sum(added) / len(added),
            "coercions_total_mean": written + sum(added) / len(added),
            "coercions_original": written,
            "plain_mean": sum(vals) / len(vals) if vals else None,
            "plain_min": min(vals) if vals else None,
            "plain_max": max(vals) if vals else None,
            "plain_n": len(vals),
        })
    return points


def main():
    parser = ArgumentParser()
    parser.add_argument("--results", default="typedness2_results.json")
    parser.add_argument("--out", default="typedness3_results.json")
    parser.add_argument("--samples", type=int, default=None)
    parser.add_argument("--runs", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--benchmark", action="append", default=[])
    args = parser.parse_args()

    with open(args.results) as f:
        data = load(f)
    samples = args.samples or data["samples"]
    runs = args.runs or data["runs"]
    proportions = data["proportions"]
    wanted = set(args.benchmark)

    rows = [dict(r) for r in data["masks"]]
    benches = sorted({r["benchmark"] for r in rows})
    units = {r["benchmark"]: r["units"] for r in rows}
    sources = {b: load_bench(b, VARIANT) for b in benches}
    originals = {b: wraps(parse(sources[b])) for b in sources}

    have = {}
    for r in rows:
        have.setdefault((r["benchmark"], r["erased"]), set()).add(r["mask"])
    spare = {}
    for fail in data["failures"]:
        if fail["mask"] is not None:
            spare.setdefault((fail["benchmark"], fail["erased"]), []).append(
                fail["mask"])

    todo = []
    for bench in benches:
        if wanted and bench not in wanted:
            continue
        n = units[bench]
        for k in levels(n, proportions):
            want = min(samples, comb(n, k) if 0 < k < n else 1)
            if len(have.get((bench, k), ())) < want:
                todo.append((bench, k, want))
    total = sum(want - len(have.get((b, k), ())) for b, k, want in todo)
    print(f"{total} plain runs to fill {len(todo)} proportions", file=stderr)

    started, done, failures = perf_counter(), 0, []
    for bench, k, want in todo:
        n = units[bench]
        used = set(have.get((bench, k), ()))
        rng = Random(data["seed"] + sum(map(ord, bench)) + k)
        queue = [m for m in spare.get((bench, k), []) if m not in used]
        tries = 0
        while len(used) < want and tries < want + DRAWS:
            if queue:
                mask = queue.pop(0)
            else:
                mask = draw(n, k, used, rng)
                if mask is None:
                    break
            used.add(mask)
            tries += 1
            done += 1
            frac = done / max(total, 1)
            elapsed = perf_counter() - started
            filled = int(BAR * frac)
            print(f"\r[{'#' * filled}{'.' * (BAR - filled)}] {done}/{total} "
                  f"{elapsed / 60:4.1f}m, {elapsed / frac - elapsed:4.0f}s left "
                  f" {bench} k={k}/{n} mask={mask:<12d}", end="", file=stderr,
                  flush=True)
            seconds, added, error = time_plain(sources[bench], mask, runs,
                                               args.timeout)
            if error:
                failures.append({"benchmark": bench, "erased": k, "mask": mask,
                                 "status": "plain_failure", "error": error})
                continue
            rows.append({"benchmark": bench, "units": n, "erased": k,
                         "mask": mask, "typedness": 1 - k / n,
                         "coercions": added, "plain": seconds,
                         "status": "ok", "error": None, "refilled": True})
        if len(used) < want:
            failures.append({"benchmark": bench, "erased": k, "mask": None,
                             "status": "slot_exhausted",
                             "error": f"kept {len(used)}/{want}"})
    print(file=stderr)

    elapsed = perf_counter() - started
    results = {"variant": VARIANT, "granularity": GRANULARITY,
               "seed": data["seed"], "samples": samples,
               "proportions": proportions, "runs": runs, "inliner": False,
               "mode": "plain only", "elapsed_s": elapsed,
               "source": args.results,
               "points": aggregate(rows, originals), "masks": rows,
               "failures": failures}
    with open(args.out, "w") as f:
        dump(results, f, indent=1)
    print(f"{len(rows)} masks, {len(failures)} still missing, "
          f"{elapsed / 60:.1f} minutes -> {args.out}", file=stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Recount the wraps in an existing typedness sweep, keeping its timings.

The sweep stored a wrap count per mask that was really a count of
coercion-shaped calls with a bare Name callee -- it missed every checked
container the mediator built and every boxed conversion. Rerunning
the sweep to fix that would rerun the benchmarks, hours of it, for a number
that does not depend on any timing. So this re-detypes each mask, recounts it
with `typedness_sweep2.wraps`, and writes the same file back with the new
counts and the old timings.

The old count is recomputed alongside and checked against what the file says,
so a mask decoded with the wrong granularity is an error rather than a quiet
difference blamed on the new counter.

  python recount_wraps.py                       typedness8 -> typedness9
  python recount_wraps.py --in x.json --out y.json
"""
from argparse import ArgumentParser
from ast import Call, Name, parse, unparse, walk
from concurrent.futures import ProcessPoolExecutor
from json import dump, load
from os import cpu_count
from sys import stderr

from detyper import detype
from load_source import load_bench
from patch_picker import PRIMITIVE_NAMES
from typedness_sweep2 import VARIANT, wraps

OLD_COERCERS = {"cast", "box"} | set(PRIMITIVE_NAMES)


def old_count(tree):
    """What the sweep counted: Name-callee calls in the old alphabet."""
    return sum(1 for node in walk(tree)
               if isinstance(node, Call) and isinstance(node.func, Name)
               and node.func.id in OLD_COERCERS)


def recount(job):
    bench, mask, bench_granularity, stored = job
    source = load_bench(bench, VARIANT)
    if mask == 0:                      # detype reads mask=0 as "erase all"
        return bench, mask, 0, 0, stored, wraps(parse(source))
    tree = detype(source, mask=mask, bench=bench_granularity)
    original = parse(source)
    return (bench, mask, wraps(tree) - wraps(original),
            old_count(tree) - old_count(original), stored, wraps(original))


def main():
    parser = ArgumentParser()
    parser.add_argument("--in", dest="source", default="typedness8_results.json")
    parser.add_argument("--out", default="typedness9_results.json")
    parser.add_argument("--workers", type=int, default=cpu_count() or 1)
    args = parser.parse_args()

    with open(args.source) as stream:
        data = load(stream)
    granularity = data["granularity"]
    jobs = [(row["benchmark"], row["mask"],
             granularity[row["benchmark"]] == "benchmark", row["coercions"])
            for row in data["masks"]]
    print(f"{len(jobs)} masks", file=stderr)

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(recount, jobs, chunksize=4))

    counts, authored, drifted = {}, {}, 0
    for bench, mask, new, old, stored, original in results:
        counts[(bench, mask)] = new
        authored[bench] = original
        if old != stored:
            drifted += 1
            print(f"{bench} mask={mask}: old counter now {old}, file says "
                  f"{stored}", file=stderr)
    if drifted:
        raise SystemExit(f"{drifted} masks do not reproduce; not writing")

    for row in data["masks"]:
        row["coercions"] = counts[(row["benchmark"], row["mask"])]
    grouped = {}
    for row in data["masks"]:
        grouped.setdefault((row["benchmark"], row["erased"]), []).append(row)
    for point in data["points"]:
        group = grouped[(point["benchmark"], point["erased"])]
        point["coercions_mean"] = sum(r["coercions"] for r in group) / len(group)
        point["coercions_original"] = authored[point["benchmark"]]
        point["coercions_total_mean"] = (point["coercions_original"]
                                         + point["coercions_mean"])
    data["counter"] = "typedness_sweep2.wraps"
    with open(args.out, "w") as stream:
        dump(data, stream, indent=1)
    print(f"wrote {args.out}", file=stderr)


if __name__ == "__main__":
    raise SystemExit(main())

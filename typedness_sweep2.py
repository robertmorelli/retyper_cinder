"""Typedness vs perf, statically compiled against plainly run, side by side.

Same sampling as typedness_sweep.py -- up to 10 proportions of typedness, 10
masks each, benchmark granularity, advanced variant -- but every mask is run
twice: once through run_compiled.py (Static Python + JIT) and once through
run_plain.py (same JIT, no static compilation). Both sides pass --no-inliner,
which is what keeps deltablue alive.

A mask whose compiled run fails is written to the failure log and replaced by
another random mask at the same proportion, up to DRAWS tries. Some proportions
cannot be filled at all -- nbody's codegen abort is not an inliner bug -- so the
graph is expected to have holes.

Also counts the wraps in the output, over what the original source already
had. A wrap is one call that moves a value between representations: a cast, a
box, a primitive or boxed constructor, or a checked container rebuilt around a
value. Every layer counts. `box(double(x))` is two, and should not be in the
output at all -- it is a tower the collapser did not take apart, and a counter
that charged it as one would hide that.

  python typedness_sweep2.py                     -> typedness2_results.json
  python typedness_sweep2.py --benchmark pystone --samples 3
  python typedness_sweep2.py --runs 3            median of 3 runs per mask
"""
from argparse import ArgumentParser
from ast import Call, Name, Subscript, parse, unparse, walk
from json import dump
from math import comb
from os import path
from random import Random
from statistics import median
from subprocess import TimeoutExpired, run as run_subprocess
from sys import executable, stderr
from tempfile import TemporaryDirectory
from time import perf_counter

from cinderx_binding import get_ast_data
from detyper import detype
from list_benchmarks import get_bench_list
from load_source import load_bench
from patch_picker import PRIMITIVE_NAMES
from typedness_graph import build_binding_graph

HERE = path.dirname(path.abspath(__file__))
VARIANT = "advanced"
GRANULARITY = "benchmark"
SKIP = {"scratch"}
DRAWS = 5
CHECKED = {"CheckedList", "CheckedDict", "CheckedSet"}
BOXED = {"int", "float", "str", "bool"}
MODES = (("compiled", "run_compiled.py"), ("plain", "run_plain.py"))
BAR = 30


def unit_count(bench):
    try:
        data = get_ast_data(parse(load_bench(bench, VARIANT)))
        return len(build_binding_graph(data).units(GRANULARITY))
    except Exception:
        return 0


def wrapper_name(node):
    """Which wrapper this call is, or None if it is not one.

    Arity is part of the test. `cast` takes a type and a value, everything
    else takes the one value it converts, and a same-named call with any other
    shape is some other function.

    `clen` is deliberately absent: it computes a length rather than converting
    its operand, and it replaces a `len` one for one, so counting it would
    move the total for a swap that wrapped nothing.

    `int`, `float`, `str` and `bool` are here. They are the boxed spellings of
    the primitives and they convert what they are handed, which is what a wrap
    is; whether the author wrote it or the mediator did decides nothing about
    what the program then does.
    """
    if not isinstance(node, Call):
        return None
    func = node.func
    if isinstance(func, Name):
        if func.id == "cast" and len(node.args) == 2:
            return "cast"
        if func.id == "box" and len(node.args) == 1:
            return "box"
    # `CheckedList[Point](xs)` reaches here two ways. Written by hand it is a
    # Subscript. Built by the mediator it is a Name whose id is the whole
    # string, brackets and all -- `_type_expr` splits a readable name on dots
    # and never on a subscript -- so a check for a Subscript callee sees the
    # author's and misses ours, which is the wrap that moves with the mask.
    if isinstance(func, Subscript) and isinstance(func.value, Name):
        name = func.value.id
    elif isinstance(func, Name):
        name = func.id.split("[")[0]
    else:
        return None
    if len(node.args) == 1 and (name in PRIMITIVE_NAMES or name in CHECKED
                                or name in BOXED):
        return name
    return None


def wraps(tree):
    """How many wraps a tree carries, counting every layer.

    A tower is worth what it costs. `box(double(x))` converts twice and is
    charged twice -- it is also a tower that should have been collapsed, and
    charging it as one wrap would make the counter quietest exactly where the
    output is worst.
    """
    return sum(1 for node in walk(tree) if wrapper_name(node) is not None)


def levels(n, proportions):
    if n == 0:
        return []
    ks = {round(n * i / (proportions - 1)) for i in range(proportions)}
    return sorted(ks, reverse=True)


def draw(n, k, used, rng):
    """A k-bit mask not already used at this proportion, or None if exhausted."""
    if k == 0:
        return None if 0 in used else 0
    full = (1 << n) - 1
    if k == n:
        return None if full in used else full
    if len(used) >= comb(n, k):
        return None
    for _ in range(200):
        mask = sum(1 << i for i in rng.sample(range(n), k))
        if mask not in used:
            return mask
    return None


def build(source, mask):
    """The detyped source and how many wraps the detyper put in it."""
    if mask == 0:                      # detype reads mask=0 as "erase all"
        tree = parse(source)
        return unparse(tree), 0
    tree = detype(source, mask=mask, bench=True)
    return unparse(tree), wraps(tree) - wraps(parse(source))


def execute(script, module_path, timeout):
    cmd = [executable, path.join(HERE, script), module_path, "--no-inliner"]
    try:
        proc = run_subprocess(cmd, capture_output=True, text=True,
                              timeout=timeout, cwd=HERE)
    except TimeoutExpired:
        return None, "timeout"
    if proc.returncode:
        tail = proc.stderr.strip().splitlines()
        named = {-11: "SIGSEGV", -6: "SIGABRT"}.get(proc.returncode, "")
        return None, (tail[-1] if tail else f"rc={proc.returncode} {named}")[:90]
    lines = proc.stdout.strip().splitlines()
    try:
        return float(lines[-1]), None
    except (IndexError, ValueError):
        return None, f"no timing: {(lines[-1] if lines else '')[:40]!r}"


def run_mask(source, mask, runs, timeout):
    """Both modes for one mask. Compiled failing is what rejects a mask."""
    try:
        text, added = build(source, mask)
    except Exception as exc:
        return {"status": "detype_failure", "error": f"{type(exc).__name__}: {exc}"}
    out = {"coercions": added, "status": "ok", "error": None}
    with TemporaryDirectory() as tmp:
        module_path = path.join(tmp, "bench_module.py")
        with open(module_path, "w") as f:
            f.write(text)
        for label, script in MODES:
            samples, error = [], None
            for _ in range(runs):
                seconds, error = execute(script, module_path, timeout)
                if error:
                    break
                samples.append(seconds)
            out[label] = median(samples) if samples else None
            if error:
                out["status"] = f"{label}_failure"
                out["error"] = error
                if label == "compiled":
                    break
    return out


def sweep(bench, source, n, args, rng, progress):
    rows, failures = [], []
    for k in levels(n, args.proportions):
        used, kept, tries = set(), 0, 0
        want = min(args.samples, comb(n, k) if 0 < k < n else 1)
        while kept < want and tries < want + DRAWS:
            mask = draw(n, k, used, rng)
            if mask is None:
                break
            used.add(mask)
            tries += 1
            progress(f"{bench} k={k}/{n} mask={mask}")
            row = run_mask(source, mask, args.runs, args.timeout)
            row.update(benchmark=bench, units=n, erased=k, mask=mask,
                       typedness=1 - k / n)
            if row["status"] == "ok":
                rows.append(row)
                kept += 1
            else:
                failures.append({"benchmark": bench, "erased": k, "mask": mask,
                                 "status": row["status"], "error": row["error"]})
        if kept < want:
            failures.append({"benchmark": bench, "erased": k, "mask": None,
                             "status": "slot_exhausted",
                             "error": f"kept {kept}/{want} after {tries} draws"})
    return rows, failures


def aggregate(rows):
    groups = {}
    for row in rows:
        groups.setdefault((row["benchmark"], row["erased"]), []).append(row)
    points = []
    for (bench, k), group in sorted(groups.items(), key=lambda kv: (kv[0][0], -kv[0][1])):
        point = {"benchmark": bench, "units": group[0]["units"], "erased": k,
                 "typedness": group[0]["typedness"], "masks": len(group),
                 "coercions_mean": sum(r["coercions"] for r in group) / len(group)}
        for label, _ in MODES:
            vals = [r[label] for r in group if r.get(label) is not None]
            point[f"{label}_mean"] = sum(vals) / len(vals) if vals else None
            point[f"{label}_min"] = min(vals) if vals else None
            point[f"{label}_max"] = max(vals) if vals else None
            point[f"{label}_n"] = len(vals)
        points.append(point)
    return points


def main():
    parser = ArgumentParser()
    parser.add_argument("--proportions", type=int, default=10)
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--runs", type=int, default=1,
                        help="runs per mask per mode; the median is kept")
    parser.add_argument("--seed", type=int, default=20260813)
    parser.add_argument("--benchmark", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--out", default="typedness2_results.json")
    args = parser.parse_args()

    wanted = set(args.benchmark)
    benches = [b for b, v, _ in get_bench_list()
               if v == VARIANT and b not in SKIP and (not wanted or b in wanted)]
    shapes = {b: unit_count(b) for b in benches}
    slots = {b: sum(min(args.samples, comb(shapes[b], k) if 0 < k < shapes[b] else 1)
                    for k in levels(shapes[b], args.proportions)) for b in benches}
    total = sum(slots.values())
    started = perf_counter()
    done = [0]

    def progress(task):
        done[0] += 1
        frac = done[0] / max(total, 1)
        filled = int(BAR * frac)
        elapsed = perf_counter() - started
        eta = elapsed / frac - elapsed if frac else 0
        print(f"\r[{'#' * filled}{'.' * (BAR - filled)}] {done[0]}/{total} "
              f"{elapsed / 60:4.1f}m elapsed, {eta / 60:4.1f}m left  {task:<44s}",
              end="", file=stderr, flush=True)

    print(f"{total} mask slots across {len(benches)} benchmarks, "
          f"2 modes each, --no-inliner", file=stderr)
    rows, failures = [], []
    for bench in benches:
        source = load_bench(bench, VARIANT)
        rng = Random(args.seed + sum(map(ord, bench)))
        bench_rows, bench_failures = sweep(bench, source, shapes[bench], args,
                                          rng, progress)
        rows.extend(bench_rows)
        failures.extend(bench_failures)
    print(file=stderr)

    results = {"variant": VARIANT, "granularity": GRANULARITY, "seed": args.seed,
               "samples": args.samples, "proportions": args.proportions,
               "runs": args.runs, "inliner": False,
               "elapsed_s": perf_counter() - started,
               "points": aggregate(rows), "masks": rows, "failures": failures}
    with open(args.out, "w") as f:
        dump(results, f, indent=1)
    print(f"{len(rows)} masks kept, {len(failures)} failures, "
          f"{results['elapsed_s'] / 60:.1f} minutes -> {args.out}", file=stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

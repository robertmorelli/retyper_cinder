"""Full 2^7 factorial over call_method_slots' annotation units.

call_method_slots has 7 units at benchmark granularity, so all 128 masks fit and
nothing has to be estimated from a fraction of the design. Each mask is a corner
of the cube; the response is the seconds the benchmark prints. With replicates
the design is saturated but not confounded: every main effect and all 127
interactions are identifiable by Hadamard contrasts, and the replicates carry
the error term the tests need.

Replicates are run in randomized order across all masks and both modes rather
than back to back, so thermal drift spreads across the design instead of loading
onto whichever masks happened to run late.

  python factorial_call_method_slots.py                 8 reps, both modes
  python factorial_call_method_slots.py --reps 3
  python factorial_call_method_slots.py --mode compiled
"""
from argparse import ArgumentParser
from ast import parse, unparse
from json import dump
from os import path
from random import Random
from statistics import median
from subprocess import TimeoutExpired, run as run_subprocess
from sys import executable, stderr
from tempfile import TemporaryDirectory
from time import perf_counter

from cinderx_binding import get_ast_data
from detyper import detype
from load_source import load_bench
from typedness_graph import build_binding_graph

HERE = path.dirname(path.abspath(__file__))
BENCH = "call_method_slots"
VARIANT = "advanced"
GRANULARITY = "benchmark"
SCRIPTS = {"compiled": "run_compiled.py", "plain": "run_plain.py"}
BAR = 30


def units():
    data = get_ast_data(parse(load_bench(BENCH, VARIANT)))
    graph = build_binding_graph(data)
    return graph.units(GRANULARITY)


def build_sources(n, source):
    """Every mask's detyped text, written once and reused for all replicates."""
    out = {}
    for mask in range(1 << n):
        if mask == 0:
            out[mask] = unparse(parse(source))
        else:
            out[mask] = unparse(detype(source, mask=mask, bench=True))
    return out


def execute(script, module_path, timeout):
    cmd = [executable, path.join(HERE, script), module_path, "--no-inliner"]
    try:
        proc = run_subprocess(cmd, capture_output=True, text=True,
                              timeout=timeout, cwd=HERE)
    except TimeoutExpired:
        return None, "timeout"
    if proc.returncode:
        tail = proc.stderr.strip().splitlines()
        return None, (tail[-1] if tail else f"rc={proc.returncode}")[:90]
    lines = proc.stdout.strip().splitlines()
    try:
        return float(lines[-1]), None
    except (IndexError, ValueError):
        return None, "no timing"


def main():
    parser = ArgumentParser()
    parser.add_argument("--reps", type=int, default=8)
    parser.add_argument("--mode", action="append", default=[],
                        choices=list(SCRIPTS))
    parser.add_argument("--seed", type=int, default=20260813)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--out", default="factorial_call_method_slots.json")
    args = parser.parse_args()
    modes = args.mode or list(SCRIPTS)

    labels = [str(unit) for unit in units()]
    n = len(labels)
    source = load_bench(BENCH, VARIANT)
    print(f"{BENCH}: {n} units, {1 << n} masks, {args.reps} reps, "
          f"modes={','.join(modes)}", file=stderr)

    texts = build_sources(n, source)
    jobs = [(mask, mode, rep) for rep in range(args.reps)
            for mask in range(1 << n) for mode in modes]
    Random(args.seed).shuffle(jobs)

    samples = {(mask, mode): [] for mask in range(1 << n) for mode in modes}
    failures = []
    started = perf_counter()
    with TemporaryDirectory() as tmp:
        paths = {}
        for mask, text in texts.items():
            paths[mask] = path.join(tmp, f"bench_{mask}.py")
            with open(paths[mask], "w") as f:
                f.write(text)
        for index, (mask, mode, rep) in enumerate(jobs, 1):
            seconds, error = execute(SCRIPTS[mode], paths[mask], args.timeout)
            if error:
                failures.append({"mask": mask, "mode": mode, "rep": rep,
                                 "error": error})
            else:
                samples[(mask, mode)].append(seconds)
            frac = index / len(jobs)
            filled = int(BAR * frac)
            elapsed = perf_counter() - started
            print(f"\r[{'#' * filled}{'.' * (BAR - filled)}] {index}/{len(jobs)} "
                  f"{elapsed / 60:5.1f}m elapsed, {elapsed / frac / 60 - elapsed / 60:5.1f}m left"
                  f"  mask={mask:3d} {mode:<8s} rep={rep}", end="", file=stderr,
                  flush=True)
    print(file=stderr)

    results = {"benchmark": BENCH, "variant": VARIANT, "granularity": GRANULARITY,
               "units": labels, "n": n, "reps": args.reps, "modes": modes,
               "seed": args.seed, "inliner": False,
               "elapsed_s": perf_counter() - started,
               "failures": failures,
               "cells": [{"mask": mask, "mode": mode,
                          "bits": bin(mask).count("1"),
                          "samples": samples[(mask, mode)],
                          "median": median(samples[(mask, mode)])
                                    if samples[(mask, mode)] else None}
                         for mask in range(1 << n) for mode in modes]}
    with open(args.out, "w") as f:
        dump(results, f, indent=1)
    ok = sum(1 for c in results["cells"] if c["median"] is not None)
    print(f"{ok}/{len(results['cells'])} cells filled, {len(failures)} failed runs, "
          f"{results['elapsed_s'] / 60:.1f} minutes -> {args.out}", file=stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

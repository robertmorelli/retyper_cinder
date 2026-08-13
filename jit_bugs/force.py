"""Run every reproducer in this directory N times under three configurations.

Each one is supposed to fail the same way every time, so the point of running
them repeatedly is to show they do -- and to show which configuration makes each
one stop. Bug B is the inliner and clears under --no-inliner; bug A is codegen
and only clears with the JIT off; bug C is not the JIT at all and never clears.

  python jit_bugs/force.py            10 runs of each, all three configs
  python jit_bugs/force.py --runs 50
  python jit_bugs/force.py --only b
"""
from argparse import ArgumentParser
from collections import Counter
from glob import glob
from os import path
from subprocess import run
from sys import executable

HERE = path.dirname(path.abspath(__file__))
RUNNER = path.join(path.dirname(HERE), "timing_runner.py")
CONFIGS = (("jit", []), ("jit --no-inliner", ["--no-inliner"]),
           ("--no-jit", ["--no-jit"]))


def signature(proc):
    """What this run did, short enough to count."""
    if proc.returncode == 0:
        return f"ok: {proc.stdout.strip().splitlines()[-1][:40]}" if proc.stdout.strip() else "ok"
    tail = proc.stderr.strip().splitlines()
    named = {-11: "SIGSEGV", -6: "SIGABRT"}.get(proc.returncode)
    detail = tail[-1][:70] if tail else ""
    return f"rc={proc.returncode}" + (f" ({named})" if named else "") + (f" {detail}" if detail else "")


def main():
    parser = ArgumentParser()
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--only", action="append", default=[],
                        help="prefix letter or filename fragment")
    args = parser.parse_args()

    files = sorted(f for f in glob(path.join(HERE, "*.py"))
                   if path.basename(f) != "force.py"
                   and (not args.only or any(o in path.basename(f) for o in args.only)))
    for f in files:
        print(f"\n{path.basename(f)}")
        for label, flags in CONFIGS:
            counts = Counter()
            for _ in range(args.runs):
                proc = run([executable, RUNNER, f, "--require-static", *flags],
                           capture_output=True, text=True, timeout=300)
                counts[signature(proc)] += 1
            for sig, n in counts.most_common():
                print(f"  {label:18s} {n}/{args.runs}  {sig}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

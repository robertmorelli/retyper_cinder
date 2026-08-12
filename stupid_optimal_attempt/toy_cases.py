"""Small Static Python programs, one mechanism each, for fast feedback.

three_phase.py has been debugged against fannkuch, where one run is fifty
seconds and a failure says only "no feasible assignment". That is how five
wrong diagnoses in a row happened. These cases are a few lines each, so a run
is a second and a failure names the construct that broke.

Each case is a fully annotated program the mediator is known to handle. The
mediator's own output is the ground truth: it compiles, and the number of
wrappers it needs is the target to match. A case is passing when three_phase
proposes something that compiles and costs no more than the mediator.

The cases are ordered by what they add:

  scalar        a typed loop counter, no containers
  array_store   `a[i] = <expr>` - the demand comes from the element type, which
                is the case that showed the expression-only probe was blind
  array_read    a subscript load feeding a typed slot
  call          a typed argument, so the demand comes from a signature
  compare       a test position, where cbool rather than a value is wanted
  mixed         two of the above interacting through one local
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(HERE)]

import subprocess
import tempfile

from detyper import detype
from brute_force_prune import check, mark_generated_wrappers


SAMPLES = (4, 5, 6, 7)


def behaviour(source: str):
    """What the program computes, not merely whether it compiles.

    Checking the loader's exit code is not enough and believing it cost a
    wrong answer: a proposal using `cast(bool, ...)` compiled, ran, and raised
    `TypeError: expected bool, got staticarray` only when its function was
    actually called. Every case here exposes `f(int) -> int`, so the cheapest
    honest check is to call it.
    """
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "candidate.py"
        path.write_text(source + f"""

def __check__():
    return [f(k) for k in {SAMPLES!r}]
""")
        result = subprocess.run(
            [sys.executable, "-c",
             "import sys, cinderx; cinderx.init(); sys.path.insert(0, %r);"
             "import candidate; print(candidate.__check__())" % directory],
            capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            tail = (result.stderr or "").strip().splitlines()
            return None, tail[-1] if tail else f"exit {result.returncode}"
        return result.stdout.strip(), None


CASES: dict[str, str] = {
    "scalar": '''
import __static__
from __static__ import int64, box

def f(nb: int) -> int:
    n: int64 = int64(nb)
    i: int64 = 0
    while i < n:
        i = i + 1
    return box(i)
''',
    "array_store": '''
import __static__
from __static__ import int64, Array

def f(nb: int) -> int:
    n: int64 = int64(nb)
    count: Array[int64] = Array[int64](nb)
    i: int64 = 0
    while i < n:
        count[i] = i + 1
        i = i + 1
    return nb
''',
    "array_read": '''
import __static__
from __static__ import int64, Array, box

def f(j: int) -> int:
    a: Array[int64] = Array[int64](j)
    i: int64 = int64(j) - int64(1)
    x: int64 = a[i]
    return box(x)
''',
    "call": '''
import __static__
from __static__ import int64, box

def g(v: int64) -> int64:
    return v

def f(nb: int) -> int:
    n: int64 = int64(nb)
    return box(g(n))
''',
    "compare": '''
import __static__
from __static__ import int64, box

def f(nb: int) -> int:
    n: int64 = int64(nb)
    total: int64 = 0
    if n > 0:
        total = n
    return box(total)
''',
    "index_wrap": '''
import __static__
from __static__ import int64, Array, box

def f(nb: int) -> int:
    a: Array[int64] = Array[int64](nb)
    j: int64 = int64(nb) - int64(1)
    a[j] = int64(7)
    return box(a[j])
''',
    "nested": '''
import __static__
from __static__ import int64, Array, box

def f(nb: int) -> int:
    n: int64 = int64(nb)
    a: Array[int64] = Array[int64](nb)
    i: int64 = 0
    while i < n:
        a[i] = a[i] + int64(1)
        i = i + 1
    return box(a[0])
''',
    "mixed": '''
import __static__
from __static__ import int64, Array, box

def f(nb: int) -> int:
    n: int64 = int64(nb)
    perm: Array[int64] = Array[int64](nb)
    i: int64 = 0
    while i < n:
        perm[i] = i
        i = i + 1
    k: int64 = perm[0]
    return box(k)
''',
}


def ground_truth(name: str, source: str):
    """The mediator's answer: does it compile, and how many wrappers."""
    try:
        mediator = detype(source, mask=0, bench=False)
    except Exception as error:
        return None, f"detype raised {type(error).__name__}: {error}"
    generated = mark_generated_wrappers(source, mediator)
    text = ast.unparse(mediator)
    verdict = check(text, False, 120)
    if verdict.returncode != 0:
        tail = (verdict.stderr or "").strip().splitlines()
        return None, f"mediator output rejected: {tail[-1] if tail else '?'}"
    return len(generated), text


def compare(name: str, source: str, budget: int, verify: int):
    """Ground truth versus what three_phase proposes, for one case."""
    import three_phase

    target, detail = ground_truth(name, source)
    if target is None:
        return f"{name:12s} UNUSABLE   {detail}"

    expected, error = behaviour(detail)
    if expected is None:
        return f"{name:12s} UNUSABLE   mediator output crashes: {error}"
    wraps = [line.strip() for line in detail.splitlines()
             if any(w in line for w in ("int64(", "cbool(", "cast("))
             and "=" in line]
    try:
        result = three_phase.run(source, 0, 2, budget, 8, verbose=False)
    except Exception as error:
        return f"{name:12s} target={target}  three_phase RAISED {type(error).__name__}: {error}"
    if result is None:
        return f"{name:12s} target={target}  no feasible assignment"

    scored, candidates, base, _ = result
    for total, assignment, plans in scored[:verify]:
        text = three_phase.materialize(base, plans)
        try:
            if not three_phase.binds_clean(ast.parse(text)):
                continue
        except SyntaxError:
            continue
        if check(text, False, 120).returncode != 0:
            continue
        mine, error = behaviour(text)
        if mine is None:
            return (f"{name:12s} target={target}  cost {total} compiled but "
                    f"CRASHED: {error}")
        if mine != expected:
            return (f"{name:12s} target={target}  cost {total} compiled but "
                    f"WRONG: {mine} != {expected}")
        mark = "OK " if total <= target else "WORSE"
        return (f"{name:12s} target={target}  VERIFIED at {total}  {mark}"
                f"  same results")
    return (f"{name:12s} target={target}  none of {len(scored)} feasible "
            f"proposals compiled (cheapest {scored[0][0]})")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    wanted = args or list(CASES)
    for name in wanted:
        print(compare(name, CASES[name], 200000, 200), flush=True)


if __name__ == "__main__":
    main()

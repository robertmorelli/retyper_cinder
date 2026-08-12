"""Brute-force the real minimum wrap set for a small program.

Nothing here is clever and that is the point: it is the ground truth the
solvers are judged against. Every subset of wrappable positions up to a depth,
every conversion at each, compiled by the Static Python loader and then run to
check it still computes what the mediator computes.

Only usable on toys - the count is (positions choose k) times kinds^k - which
is exactly what it is for. A solver claiming a minimum is only interesting once
something independent says what the minimum is.
"""
from __future__ import annotations

import argparse
import ast
import copy
import itertools
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(HERE)]

from detyper import detype
from import_adder import add_imports
from brute_force_prune import call_key, check, removable_operand
from print_instances import wrappable

sys.path.insert(0, str(ROOT / "_cinderx/cinderx/PythonLib"))
from cinderx.compiler.errors import CollectingErrorSink
from cinderx.compiler.static import StaticCodeGenerator
from cinderx.compiler.static.compiler import Compiler


def binds_clean(tree) -> bool:
    """In-process filter. The loader and the runtime cost a subprocess each,
    which at thousands of candidates is the whole runtime; a bind is ~15ms and
    rejects almost everything."""
    sink = CollectingErrorSink()
    compiler = Compiler(StaticCodeGenerator, error_sink=sink)
    scratch = copy.deepcopy(tree)
    try:
        compiler.bind("", "", scratch, scratch, optimize=0)
    except Exception:
        return False
    return not sink.errors

KINDS = ("box", "int64", "cbool", "double")
SAMPLES = (4, 5, 6, 7)


class Wrap(ast.NodeTransformer):
    def __init__(self, plan):
        self.plan = plan

    def visit(self, node):
        self.generic_visit(node)
        kind = self.plan.get(getattr(node, "_slot", None))
        if kind is None:
            return node
        return ast.copy_location(
            ast.Call(func=ast.Name(id=kind, ctx=ast.Load()),
                     args=[node], keywords=[]), node)


def author_calls(source: str) -> set:
    """The author's own coercions, identified by shape rather than position.

    `call_key` carries line and column, and every pass here rewrites the tree,
    so an author-written `int64(j)` no longer matches itself afterwards and
    gets treated as something the mediator generated. Stripping on that basis
    deleted the author's cast and let the brute force "beat" the mediator by
    searching a program the mediator is not allowed to write.
    """
    return {ast.unparse(node) for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)}


def strip_wrappers(source: str, tree: ast.AST) -> ast.AST:
    """The erased program with the mediator's own coercions taken back off."""
    original = author_calls(source)

    class Peel(ast.NodeTransformer):
        def visit_Call(self, node):
            self.generic_visit(node)
            if ast.unparse(node) in original:
                return node
            operand = removable_operand(node)
            return ast.copy_location(operand, node) if operand is not None else node

    return ast.fix_missing_locations(Peel().visit(copy.deepcopy(tree)))


def behaviour(source: str):
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
        return result.stdout.strip() if result.returncode == 0 else None


def search(source: str, max_wraps: int, verbose=True):
    mediator = detype(source, mask=0, bench=False)
    expected = behaviour(ast.unparse(mediator))
    if expected is None:
        raise SystemExit("mediator output does not run; nothing to compare to")
    original = author_calls(source)
    baseline = sum(1 for n in ast.walk(mediator)
                   if isinstance(n, ast.Call)
                   and removable_operand(n) is not None
                   and ast.unparse(n) not in original)

    base = strip_wrappers(source, mediator)
    positions = [n for n in ast.walk(base) if wrappable(n)]
    for index, node in enumerate(positions):
        node._slot = index
    if verbose:
        print(f"mediator: {baseline} wraps, result {expected}", file=sys.stderr)
        print(f"positions: {len(positions)}, kinds: {len(KINDS)}",
              file=sys.stderr)

    tried = 0
    for count in range(max_wraps + 1):
        for chosen in itertools.combinations(range(len(positions)), count):
            for kinds in itertools.product(KINDS, repeat=count):
                tried += 1
                plan = dict(zip(chosen, kinds))
                tree = ast.fix_missing_locations(
                    add_imports(Wrap(plan).visit(copy.deepcopy(base))))
                if not binds_clean(tree):
                    continue
                text = ast.unparse(tree)
                if check(text, False, 120).returncode != 0:
                    continue
                if behaviour(text) != expected:
                    continue
                return count, text, tried, baseline
        if verbose:
            print(f"  no solution at {count} wraps ({tried} tried)",
                  file=sys.stderr)
    return None, None, tried, baseline


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("case")
    parser.add_argument("--max-wraps", type=int, default=3)
    args = parser.parse_args()

    from toy_cases import CASES
    source = CASES[args.case] if args.case in CASES else Path(args.case).read_text()
    minimum, text, tried, baseline = search(source, args.max_wraps)
    if minimum is None:
        print(f"no valid program within {args.max_wraps} wraps ({tried} tried)")
        raise SystemExit(2)
    verdict = "MEDIATOR IS NOT MINIMAL" if minimum < baseline else "mediator is minimal"
    print(f"true minimum {minimum}, mediator {baseline} -> {verdict} "
          f"({tried} candidates tried)")
    if minimum < baseline:
        print(text)


if __name__ == "__main__":
    main()

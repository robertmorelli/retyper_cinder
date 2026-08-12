"""Search typedness assignments; let the mediator generate the wraps.

three_phase.py takes each statement apart, rebuilds its context inside a probe,
and asks CinderX what that context implies. Every bug it has had was a hole in
that reconstruction - narrowing measured per statement instead of per function,
a BinOp that does not survive binding as a findable node, an annotation offered
as a place to put a cast. The context it keeps re-deriving is the thing the
typedness graph already holds.

So this does not rebuild anything. For a given typedness assignment:

    pinned_settle   the graph propagates that assignment across the program
    remove_annotations + coerce_tree
                    the mediator's own passes, post-order, calling propagate
                    as they go - the wrap set is *determined* by the types,
                    not searched
    one bind        did it work?

The only thing being searched is which positions keep a type. The wraps follow.
That is one bit per annotation the erasure removed, and nothing else, so the
space is 2^k rather than a product of per-statement tables.

`pinned_settle` is checked against `Graph.settle` on every run, so this cannot
drift from the rule the mediator actually uses.
"""
from __future__ import annotations

import argparse
import ast
import copy
import itertools
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(HERE)]

from annotation_remover import remove_annotations
from cinderx_binding import get_ast_data
from import_adder import add_imports
from inline_call_analysis import find_inline_args
from type_mediator import coerce_tree
from typedness_graph import build_binding_graph
from brute_force_prune import call_key, check, removable_operand
from graph_oracle import check_reference, pinned_settle
from print_instances import parse_mask

sys.path.insert(0, str(ROOT / "_cinderx/cinderx/PythonLib"))
from cinderx.compiler.errors import CollectingErrorSink
from cinderx.compiler.static import StaticCodeGenerator
from cinderx.compiler.static.compiler import Compiler


def binds_clean(tree: ast.AST) -> bool:
    sink = CollectingErrorSink()
    compiler = Compiler(StaticCodeGenerator, error_sink=sink)
    scratch = copy.deepcopy(tree)
    try:
        compiler.bind("", "", scratch, scratch, optimize=0)
    except Exception:
        return False
    return not sink.errors


def pin_candidates(graph, bound, erased):
    """The annotations whose type erasure actually destroys: one bit each.

    These are `classify`'s seeds - a parameter, a return, a bare `x: int64` -
    the ones with nothing left to infer from. `settle` starts its dead set from
    exactly these, so holding one out of that set is what "keep this typed"
    means, and the graph propagates the rest. Looking for them in `bound.types`
    instead found nothing at all: an AnnAssign is a statement, and that table
    is keyed on expressions.
    """
    _, _, seeds = graph.classify(erased, bound)
    return sorted(seeds, key=lambda n: (getattr(n, "lineno", 0),
                                        getattr(n, "col_offset", 0)))


def build(bound, graph, erased, pinned, less_any=False):
    """One typedness assignment, all the way to a tree."""
    predicted = pinned_settle(graph, bound, erased, pinned)
    # Both passes mutate the tree in place and read `types` by node identity,
    # exactly as detyper.py calls them, so each assignment needs its own bind
    # rather than a copy. That is the cost of reusing the real passes instead
    # of reimplementing them.
    tree = remove_annotations(bound.tree, erased, predicted.types,
                              predicted.contexts, less_any)
    tree = coerce_tree(tree, predicted.types, predicted.contexts,
                       bound.dynamic, bound.valid_pair,
                       find_inline_args(tree, bound.reverse_outflow,
                                        predicted.types),
                       graph, bound.constructors)
    return ast.fix_missing_locations(add_imports(tree))


def count_wraps(original_source: str, tree: ast.AST) -> int:
    original = {call_key(node) for node in ast.walk(ast.parse(original_source))
                if isinstance(node, ast.Call)}
    return sum(1 for node in ast.walk(tree)
               if isinstance(node, ast.Call)
               and removable_operand(node) is not None
               and call_key(node) not in original)


def search(source: str, mask: int, granularity: str, limit: int, verbose=True):
    bound = get_ast_data(ast.parse(source))
    graph = build_binding_graph(bound)
    units = graph.units(granularity)
    effective = mask or ((1 << len(units)) - 1)
    erased = graph.nodes_for_mask(effective, granularity)

    drift = check_reference(graph, bound, erased)
    if drift is not None:
        raise SystemExit(f"pinned_settle no longer matches Graph.settle: {drift}")

    candidates = pin_candidates(graph, bound, erased)
    if verbose:
        print(f"typedness bits: {len(candidates)} -> {2 ** len(candidates)} "
              f"assignments", file=sys.stderr)

    best = None
    tried = 0
    # Fewest pins first is not the same as fewest wraps, but it is a decent
    # order to meet a good answer early, and every assignment is checked.
    order = sorted(range(len(candidates)),
                   key=lambda i: (getattr(candidates[i], "lineno", 0), i))
    for size in range(len(candidates) + 1):
        for chosen in itertools.combinations(order, size):
            if tried >= limit:
                return best, tried, len(candidates)
            tried += 1
            fresh = get_ast_data(ast.parse(source))
            fresh_graph = build_binding_graph(fresh)
            fresh_erased = fresh_graph.nodes_for_mask(effective, granularity)
            fresh_candidates = pin_candidates(fresh_graph, fresh, fresh_erased)
            # The value only has to be something other than dynamic; what
            # matters is that the node stays out of `dead`.
            pinned = {fresh_candidates[i]:
                      fresh.types.get(fresh_candidates[i], fresh.dynamic)
                      for i in chosen}
            try:
                tree = build(fresh, fresh_graph, fresh_erased, pinned)
            except Exception:
                continue
            if not binds_clean(tree):
                continue
            wraps = count_wraps(source, tree)
            if best is None or wraps < best[0]:
                best = (wraps, ast.unparse(tree), chosen)
                if verbose:
                    print(f"  {wraps} wraps with {len(chosen)} pins "
                          f"(after {tried} tries)", file=sys.stderr)
    return best, tried, len(candidates)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--mask", type=parse_mask, default=0)
    parser.add_argument("--granularity", choices=("annotation", "benchmark"),
                        default="annotation")
    parser.add_argument("--limit", type=int, default=20000)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()

    source = args.source.read_text()
    started = time.monotonic()
    best, tried, bits = search(source, args.mask, args.granularity, args.limit)
    elapsed = time.monotonic() - started
    print(f"tried {tried} assignments over {bits} bits in {elapsed:.1f}s",
          file=sys.stderr)
    if best is None:
        print("no assignment produced a valid program", file=sys.stderr)
        raise SystemExit(2)
    wraps, text, chosen = best
    verdict = check(text, args.run, 180)
    print(f"minimum wraps: {wraps}  static check "
          f"{'passed' if verdict.returncode == 0 else 'FAILED'}", file=sys.stderr)
    if args.output:
        args.output.write_text(text + "\n")
        print(f"wrote {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()

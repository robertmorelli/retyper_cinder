"""Why the graph cannot be the search's error oracle. Run to reproduce.

graph_oracle.py was written so branch-and-bound could ask the graph instead of
CinderX: a settle costs under 1ms against a bind's ~15ms, and it knows every
mismatch at once rather than the first error an exception carries. The pieces
work - `check_reference` shows pinned_settle reproduces Graph.settle exactly on
every benchmark - but the question they answer is the wrong one.

This probe pins the mediator's own wrappers, the ones whose program compiles
and runs, and counts what the graph still calls wrong. On fannkuch, 35 of 39
mismatches survive. Whatever those are, they are not errors.

The mechanism is in `settle`: `contexts` starts as a copy of
`bound.type_contexts` and only nodes that a context edge feeds are ever
updated. Every other node keeps the demand the *annotated* program made on it,
which the erased program does not make. `find_mismatches` then reads that stale
demand and reports a position needing a wrapper. That is exactly right for the
coercer, which wants to know the original demand so it can preserve it, and
useless for deciding whether anything is broken.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(HERE)]

from detyper import detype
from get_ast_data import get_ast_data
from simple_type_graph import build_binding_graph
from brute_force_prune import mark_generated_wrappers, removable_operand
from global_search import baseline_tree, span
from graph_oracle import build_index, check_reference, settle_with_patches
from print_instances import find_mismatches
from promising_search import all_errors


def probe(path: Path) -> None:
    source = path.read_text()
    bound = get_ast_data(ast.parse(source))
    graph = build_binding_graph(bound)
    units = graph.units("annotation")
    erased = graph.nodes_for_mask((1 << len(units)) - 1, "annotation")

    drift = check_reference(graph, bound, erased)
    predicted = graph.settle(bound, erased)
    before = find_mismatches(graph, bound, predicted)

    mediator = detype(source, mask=0, bench=False)
    generated = mark_generated_wrappers(source, mediator)
    wrapped_spans = {span(removable_operand(c) or c) for c in generated}
    by_span = {span(node): node for node in ast.walk(bound.tree)}
    patches = {}
    for location in wrapped_spans:
        node = by_span.get(location)
        target = bound.type_contexts.get(node) if node is not None else None
        if target is not None:
            patches[node] = target

    state = settle_with_patches(graph, bound, erased, patches)
    after = find_mismatches(graph, bound, state)
    errors = all_errors(baseline_tree(mediator))
    index = build_index(graph, set(before))
    lower, _ = index.lower_bound()

    print(f"\n=== {path.parent.parent.name} ===")
    print(f"pinned_settle matches Graph.settle: {'yes' if drift is None else drift}")
    print(f"graph mismatches, no wrappers:            {len(before)}")
    print(f"graph mismatches, mediator's wrappers in: {len(after)}")
    print(f"cinderx errors, no wrappers:              {len(errors)}")
    print(f"cinderx errors, mediator's wrappers in:   0 (it compiles and runs)")
    print(f"graph lower bound: {lower}   mediator wrappers: {len(generated)}"
          f"   <- a bound above a known solution is not a bound")


def main() -> None:
    names = sys.argv[1:] or ["fannkuch", "nqueens"]
    for name in names:
        path = Path(name)
        if not path.exists():
            path = ROOT / f"static-python-perf/Benchmark/{name}/advanced/main.py"
        probe(path)


if __name__ == "__main__":
    main()

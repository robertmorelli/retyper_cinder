"""Brute-force the approximate patch instances and apply the chosen wrappers.

This is an experiment, not a replacement for type_mediator.py. It solves the
minimum-set-cover reduction from print_instances.py, realizes each selected
position as one concrete coercion, removes the selected annotations, and can
ask the Static Python loader whether the result compiles or runs.
"""
from __future__ import annotations

import argparse
import ast
import itertools
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from annotation_remover import remove_annotations
from inline_call_analysis import find_inline_args
from cinderx_binding import get_ast_data
from import_adder import add_imports
from patch_picker import Wrapper, _choose
from typedness_graph import build_binding_graph
from print_instances import (
    Mismatch,
    candidate_sets,
    find_mismatches,
    independent_instances,
    node_key,
    node_text,
    parse_mask,
    wrapper_depth,
)


def minimum_cover(instance, mismatch_candidates):
    """Exact brute-force minimum set cover for one independent instance."""
    # Prefer local mismatch positions when several equal-size covers exist;
    # they have an unambiguous concrete repair.
    candidates = sorted(
        instance.candidates,
        key=lambda node: (node not in instance.mismatches, node_key(node)),
    )
    for size in range(len(candidates) + 1):
        for choice in itertools.combinations(candidates, size):
            selected = set(choice)
            if all(selected & mismatch_candidates[m]
                   for m in instance.mismatches):
                return selected
    raise RuntimeError("uncoverable mismatch instance")


def choice_for_target(node, produced, target, graph, bound, inline_args):
    try:
        choice = _choose(
            node,
            produced,
            target,
            bound.valid_pair,
            inline_args,
            bound.dynamic,
            graph.must_agree(node),
        )
    except Exception:
        return None
    return None if type(choice) is Wrapper else choice


def realize_patch(
    node,
    covered: list[Mismatch],
    graph,
    bound,
    predicted,
    inline_args,
):
    """Choose one concrete wrapper for an abstract selected patch position.

    Local repairs are exact. For an upstream candidate, try the produced and
    demanded representations of every covered mismatch and choose the wrapper
    whose output makes the most currently mismatching positions compatible
    after one graph propagation step.
    """
    produced = predicted.types.get(node)
    if produced is None:
        return None

    targets = []
    local = next((m for m in covered if m.node is node), None)
    if local is not None:
        targets.append(local.demanded)
    for mismatch in covered:
        targets.extend((mismatch.demanded, mismatch.produced))

    unique_targets = []
    for target in targets:
        if target is not None and all(target is not seen for seen in unique_targets):
            unique_targets.append(target)

    best = None
    for target in unique_targets:
        choice = choice_for_target(
            node, produced, target, graph, bound, inline_args)
        if choice is None or choice.T is None:
            continue
        types = dict(predicted.types)
        contexts = dict(predicted.contexts)
        graph.propagate(node, choice.T, types, contexts)
        repaired = 0
        for mismatch in covered:
            trial = choice_for_target(
                mismatch.node,
                types.get(mismatch.node),
                contexts.get(mismatch.node),
                graph,
                bound,
                inline_args,
            )
            if trial is None:
                repaired += 1
        score = (repaired, -wrapper_depth(choice, node))
        if best is None or score > best[0]:
            best = score, choice
    return None if best is None else best[1]


class ApplyWrappers(ast.NodeTransformer):
    def __init__(self, wrappers):
        self.wrappers = wrappers

    def visit(self, node):
        self.generic_visit(node)
        wrapper = self.wrappers.get(node)
        return wrapper.next_root if wrapper is not None else node


def solve(source: str, mask: int, granularity: str):
    bound = get_ast_data(ast.parse(source))
    graph = build_binding_graph(bound)
    units = graph.units(granularity)
    effective_mask = mask or ((1 << len(units)) - 1)
    erased = graph.nodes_for_mask(effective_mask, granularity)
    predicted = graph.settle(bound, erased)

    mismatches = find_mismatches(graph, bound, predicted)
    mismatch_candidates = candidate_sets(graph, mismatches)
    instances = independent_instances(mismatch_candidates)
    selected = set().union(*(
        minimum_cover(instance, mismatch_candidates) for instance in instances
    )) if instances else set()

    inline_args = find_inline_args(bound.tree, bound.reverse_outflow)
    wrappers = {}
    unrealized = []
    for node in sorted(selected, key=node_key):
        covered = [mismatches[m] for m, candidates in mismatch_candidates.items()
                   if node in candidates]
        wrapper = realize_patch(
            node, covered, graph, bound, predicted, inline_args)
        if wrapper is None:
            unrealized.append(node)
        else:
            wrappers[node] = wrapper
            if wrapper.T is not None:
                graph.propagate(
                    node, wrapper.T, predicted.types, predicted.contexts)

    tree = remove_annotations(
        bound.tree, erased, predicted.types, predicted.contexts)
    tree = ApplyWrappers(wrappers).visit(tree)
    tree = ast.fix_missing_locations(add_imports(tree))
    return ast.unparse(tree), graph, mismatches, instances, selected, wrappers, unrealized


def check_output(source: str, run_program: bool) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "optimized_attempt.py"
        path.write_text(source)
        command = [
            sys.executable,
            str(ROOT / "static_runner.py"),
            str(path),
            "--require-static",
        ]
        if not run_program:
            command.append("--compile-only")
        return subprocess.run(command, capture_output=True, text=True, timeout=180)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--mask", type=parse_mask, default=0)
    parser.add_argument(
        "--granularity", choices=("annotation", "benchmark"),
        default="annotation",
    )
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument(
        "--check", choices=("none", "compile", "run"), default="compile")
    args = parser.parse_args()

    source = args.source.read_text()
    output, graph, mismatches, instances, selected, wrappers, unrealized = solve(
        source, args.mask, args.granularity)

    print(f"annotation units: {len(graph.annotation_units)}", file=sys.stderr)
    print(f"mismatches: {len(mismatches)}", file=sys.stderr)
    print(f"independent instances: {len(instances)}", file=sys.stderr)
    print(f"selected abstract patches: {len(selected)}", file=sys.stderr)
    print(f"realized wrappers: {len(wrappers)}", file=sys.stderr)
    if unrealized:
        print(f"unrealized selections: {len(unrealized)}", file=sys.stderr)
        for node in unrealized:
            print(f"  line {getattr(node, 'lineno', 0)}: "
                  f"{node_text(source, node)}", file=sys.stderr)

    if args.output is None:
        print(output)
    else:
        args.output.write_text(output + "\n")
        print(f"wrote {args.output}", file=sys.stderr)

    if args.check != "none":
        result = check_output(output, args.check == "run")
        status = "PASS" if result.returncode == 0 else "FAIL"
        print(f"{args.check} check: {status} ({result.returncode})", file=sys.stderr)
        if result.stdout.strip():
            print(result.stdout.rstrip(), file=sys.stderr)
        if result.stderr.strip():
            print(result.stderr.rstrip(), file=sys.stderr)
        if result.returncode:
            raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()

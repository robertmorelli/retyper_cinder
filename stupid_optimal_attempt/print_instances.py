"""Print approximate minimum-coercion instances for a Static Python program.

The reduction is a minimum set cover encoded as weighted partial MaxSAT.  It
uses the typedness graph to discover which expression positions can influence
each type/context mismatch, but does not rewrite or execute the program.
"""
from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Running this file directly puts this directory, rather than the repository
# root, first on sys.path.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from find_needs_exact import find_needs_exact
from get_ast_data import get_ast_data, is_primative
from patch_picker import Wrapper, _choose
from simple_type_graph import CONTEXT, TYPE, Graph, build_binding_graph


@dataclass(frozen=True)
class Mismatch:
    node: ast.AST
    produced: object
    demanded: object
    repair: str
    local_cost: int


@dataclass
class Instance:
    mismatches: set[ast.AST]
    candidates: set[ast.AST]


def wrappable(node: ast.AST) -> bool:
    """A syntactic value around which the mediator could put a call."""
    if not isinstance(node, ast.expr) or isinstance(node, ast.Slice):
        return False
    return not hasattr(node, "ctx") or isinstance(node.ctx, ast.Load)


def primitive(value: object) -> bool:
    try:
        return is_primative(value)
    except Exception:
        return False


def representation(value: object, dynamic: object) -> str:
    if value is dynamic:
        return "dynamic/object"
    if primitive(value):
        try:
            return f"machine:{value.klass.type_name.readable_name}"
        except Exception:
            return "machine"
    try:
        return f"object:{value.klass.type_name.readable_name}"
    except Exception:
        return str(value)


def wrapper_depth(wrapper: Wrapper, original: ast.AST) -> int:
    """Count calls on the wrapper spine, e.g. box(T(x)) has cost two."""
    count = 0
    node = wrapper.next_root
    while node is not original and isinstance(node, ast.Call) and node.args:
        count += 1
        # cast(T, x) carries the value in its second argument.
        node = node.args[-1]
    return max(count, 1)


def find_mismatches(graph: Graph, bound, predicted) -> dict[ast.AST, Mismatch]:
    needs_exact = find_needs_exact(bound.tree, bound.reverse_outflow)
    found: dict[ast.AST, Mismatch] = {}
    for node in predicted.types.keys() & predicted.contexts.keys():
        if not wrappable(node):
            continue
        produced = predicted.types[node]
        demanded = predicted.contexts[node]
        if produced is None or demanded is None:
            continue
        try:
            choice = _choose(
                node,
                produced,
                demanded,
                bound.valid_pair,
                needs_exact,
                bound.dynamic,
                graph.must_agree(node),
            )
        except Exception:
            continue
        if type(choice) is Wrapper:
            continue
        found[node] = Mismatch(
            node=node,
            produced=produced,
            demanded=demanded,
            repair=type(choice).__name__,
            local_cost=wrapper_depth(choice, node),
        )
    return found


def candidate_sets(
    graph: Graph, mismatches: dict[ast.AST, Mismatch]
) -> dict[ast.AST, set[ast.AST]]:
    """Every wrappable position in a mismatch's reverse graph slice.

    If changing expression `p` can affect a mismatching type or context cell
    through this graph, there must be a directed path from `(p, TYPE)` to that
    cell. Walking all in-edges therefore gives a conservative candidate set
    relative to the graph. This is intentionally broader than following only
    tagged result links: declaration, call, sibling, slot and context paths are
    all retained.

    It is not a completeness proof relative to CinderX itself; that would
    require the typedness graph to be a complete model of the binder.
    """
    reverse: dict[tuple, set[tuple]] = {}
    for edge in graph.edges:
        reverse.setdefault(edge.target, set()).add(edge.source)

    result: dict[ast.AST, set[ast.AST]] = {}
    for node in mismatches:
        pending = [(node, TYPE), (node, CONTEXT)]
        seen = set(pending)
        candidates = {node}
        while pending:
            cell = pending.pop()
            for predecessor in reverse.get(cell, ()):
                source = predecessor[0]
                if wrappable(source):
                    candidates.add(source)
                if predecessor not in seen:
                    seen.add(predecessor)
                    pending.append(predecessor)
        result[node] = candidates
    return result


def independent_instances(
    mismatch_candidates: dict[ast.AST, set[ast.AST]]
) -> list[Instance]:
    """Connected components of the mismatch/candidate bipartite graph."""
    candidate_mismatches: dict[ast.AST, set[ast.AST]] = {}
    for mismatch, candidates in mismatch_candidates.items():
        for candidate in candidates:
            candidate_mismatches.setdefault(candidate, set()).add(mismatch)

    remaining = set(mismatch_candidates)
    instances: list[Instance] = []
    while remaining:
        first = remaining.pop()
        ms, cs = {first}, set()
        pending_m = [first]
        while pending_m:
            mismatch = pending_m.pop()
            for candidate in mismatch_candidates[mismatch]:
                if candidate in cs:
                    continue
                cs.add(candidate)
                for neighbor in candidate_mismatches[candidate]:
                    if neighbor not in ms:
                        ms.add(neighbor)
                        remaining.discard(neighbor)
                        pending_m.append(neighbor)
        instances.append(Instance(ms, cs))
    return instances


def node_key(node: ast.AST) -> tuple:
    return (
        getattr(node, "lineno", 0),
        getattr(node, "col_offset", 0),
        getattr(node, "end_lineno", 0),
        type(node).__name__,
    )


def node_text(source: str, node: ast.AST) -> str:
    text = ast.get_source_segment(source, node)
    if text is None:
        try:
            text = ast.unparse(node)
        except Exception:
            text = type(node).__name__
    return " ".join(text.split())[:90]


def print_wcnf(
    instance: Instance,
    mismatch_candidates: dict[ast.AST, set[ast.AST]],
    candidate_ids: dict[ast.AST, int],
) -> None:
    # All candidate costs are one in this first reduction. Local exact wrappers
    # are reported separately but not yet represented as multi-action choices.
    clauses = []
    top = len(instance.candidates) + 1
    for mismatch in sorted(instance.mismatches, key=node_key):
        clause = sorted(candidate_ids[c] for c in mismatch_candidates[mismatch]
                        if c in instance.candidates)
        clauses.append((top, clause))
    for candidate in sorted(instance.candidates, key=node_key):
        clauses.append((1, [-candidate_ids[candidate]]))

    print(f"  p wcnf {len(instance.candidates)} {len(clauses)} {top}")
    for weight, literals in clauses:
        print(f"  {weight} {' '.join(map(str, literals))} 0")


def print_instances(
    source: str,
    graph: Graph,
    bound,
    predicted,
    mismatches: dict[ast.AST, Mismatch],
    mismatch_candidates: dict[ast.AST, set[ast.AST]],
    instances: Iterable[Instance],
    wcnf: bool,
) -> None:
    instances = sorted(
        instances,
        key=lambda item: min(node_key(n) for n in item.mismatches),
    )
    print(f"annotation units: {len(graph.annotation_units)}")
    print(f"typed expressions: {len(predicted.types)}")
    print(f"mismatches requiring a local mediator action: {len(mismatches)}")
    print(f"independent optimization instances: {len(instances)}")

    for number, instance in enumerate(instances, 1):
        candidates = sorted(instance.candidates, key=node_key)
        candidate_ids = {node: index for index, node in enumerate(candidates, 1)}
        print()
        print(f"INSTANCE {number}: {len(instance.mismatches)} mismatches, "
              f"{len(candidates)} candidate patch positions")
        print("  candidate variables:")
        for node in candidates:
            value = predicted.types.get(node)
            print(f"    p{candidate_ids[node]}  line {getattr(node, 'lineno', 0)}  "
                  f"[{representation(value, bound.dynamic)}]  "
                  f"{node_text(source, node)}")

        print("  hard mismatch constraints:")
        for index, node in enumerate(sorted(instance.mismatches, key=node_key), 1):
            mismatch = mismatches[node]
            choices = sorted(candidate_ids[c]
                             for c in mismatch_candidates[node]
                             if c in candidate_ids)
            print(
                f"    m{index} line {getattr(node, 'lineno', 0)}: "
                f"{representation(mismatch.produced, bound.dynamic)} -> "
                f"{representation(mismatch.demanded, bound.dynamic)}; "
                f"local={mismatch.repair}/{mismatch.local_cost}; "
                f"cover={' OR '.join(f'p{i}' for i in choices)}; "
                f"{node_text(source, node)}"
            )

        print("  objective: minimize " + " + ".join(
            f"p{candidate_ids[node]}" for node in candidates))
        if wcnf:
            print("  weighted partial MaxSAT:")
            print_wcnf(instance, mismatch_candidates, candidate_ids)


def parse_mask(value: str) -> int:
    return int(value, 0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--mask", type=parse_mask, default=0)
    parser.add_argument(
        "--granularity", choices=("annotation", "benchmark"),
        default="annotation",
    )
    parser.add_argument("--wcnf", action="store_true")
    args = parser.parse_args()

    source = args.source.read_text()
    bound = get_ast_data(ast.parse(source))
    graph = build_binding_graph(bound)
    units = graph.units(args.granularity)
    effective_mask = args.mask or ((1 << len(units)) - 1)
    erased = graph.nodes_for_mask(effective_mask, args.granularity)
    predicted = graph.settle(bound, erased)

    mismatches = find_mismatches(graph, bound, predicted)
    mismatch_candidates = candidate_sets(graph, mismatches)
    instances = independent_instances(mismatch_candidates)
    print_instances(
        source,
        graph,
        bound,
        predicted,
        mismatches,
        mismatch_candidates,
        instances,
        args.wcnf,
    )


if __name__ == "__main__":
    main()

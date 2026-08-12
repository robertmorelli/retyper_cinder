"""Branch-and-bound that uses every error a bind reports, not just the first.

global_search.py binds each branch and reads one error off the exception. The
compiler already found the rest: binding through a CollectingErrorSink returns
all nine of fannkuch's errors for the same ~15ms.

The graph cannot stand in for that bind, though `graph_oracle.py` was written
to try. Its mismatch set answers "would the type mediator put a wrapper here", which
is not "is anything wrong here": on fannkuch the mediator's own ten wrappers
produce a program that compiles and runs, and the graph still reports 35 of its
39 mismatches outstanding. `settle` keeps `bound.type_contexts` for any node no
context edge feeds, so most of those demands are the annotated program's, which
the erased program never makes. The graph is a generator, not a validator.

Knowing all the errors buys three things a single error cannot:

  branch    on the error with the fewest candidate positions. Any solution must
            repair it from inside that set, so this is complete, and it is the
            smallest branching set available.
  order     inside that set by how many *other* open errors a position can
            reach. A position feeding several is the one that might close them
            with one wrapper.
  prune     errors whose candidate sets are pairwise disjoint each need a
            wrapper of their own; when that count exceeds what is left of the
            budget, the subtree is dead.

Ordering is a heuristic and cannot prune. The bound is sound relative to the
graph and does nothing else. They read the same index from opposite ends.
"""
from __future__ import annotations

import argparse
import ast
import copy
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(HERE)]

from detyper import detype
from inline_call_analysis import find_inline_args
from cinderx_binding import get_ast_data
from patch_picker import Wrapper, _choose
from typedness_graph import build_binding_graph
from brute_force_prune import check, mark_generated_wrappers, removable_operand
from graph_oracle import ReachIndex, check_reference
from typedness_graph import CONTEXT, TYPE

sys.path.insert(0, str(ROOT / "_cinderx/cinderx/PythonLib"))
from cinderx.compiler.errors import CollectingErrorSink
from cinderx.compiler.static import StaticCodeGenerator
from cinderx.compiler.static.compiler import Compiler
from global_search import (
    baseline_positions, baseline_tree, make_template, tighten_mediator_bound,
    map_position, materialize_tree, span, template_call_count,
    undirected_candidate_nodes, unique_identity, SENTINEL,
)
from print_instances import find_mismatches, node_key, parse_mask, wrappable


@dataclass(frozen=True)
class GraphAction:
    """A wrapper, remembered as the graph sees it and as the tree needs it."""
    node: ast.AST           # the graph node to pin
    produced: object        # what the wrapper makes this position yield
    position: int           # index into the baseline tree's wrappable nodes
    cost: int
    description: str
    template: ast.AST
    origin_span: tuple


def all_errors(tree):
    """Every error one bind finds, instead of the first one it raises.

    Compiler.bind re-raises errors[0] when the sink has any; a non-throwing
    sink keeps the whole list, which is what the branching and the bound below
    are built on.
    """
    sink = CollectingErrorSink()
    compiler = Compiler(StaticCodeGenerator, error_sink=sink)
    scratch = copy.deepcopy(tree)
    try:
        compiler.bind("", "", scratch, scratch, optimize=0)
    except Exception as error:                       # outside the sink's reach
        if not sink.errors:
            return [error]
    return sink.errors


def error_positions(error, graph, bound, reverse):
    """Wrappable positions on a directed path into one error's location.

    An action outside this set cannot repair this error relative to the graph,
    which is the same assumption global_search.relevant_actions makes. Unlike
    that function this returns the empty set rather than falling back to every
    action: an error the graph cannot reach is a modelling gap, and spending
    the budget enumerating unrelated wrappers does not close it.
    """
    line, offset = getattr(error, "lineno", None), getattr(error, "offset", None)
    targets = []
    for node in ast.walk(bound.tree):
        if getattr(node, "lineno", None) != line:
            continue
        start = getattr(node, "col_offset", 0) + 1
        end = getattr(node, "end_col_offset", start) + 1
        if offset is None or start <= offset <= end:
            targets.extend(((node, TYPE), (node, CONTEXT)))
    if not targets:
        targets = [(node, slot) for node in ast.walk(bound.tree)
                   if getattr(node, "lineno", None) == line
                   for slot in (TYPE, CONTEXT)]

    seen, pending, positions = set(targets), list(targets), set()
    while pending:
        cell = pending.pop()
        if wrappable(cell[0]):
            positions.add(cell[0])
        for predecessor in reverse.get(cell, ()):
            if predecessor not in seen:
                seen.add(predecessor)
                pending.append(predecessor)
    return positions


def build_graph_actions(graph, bound, predicted, mismatches, candidate_nodes,
                        positions):
    """The same action vocabulary global_search uses, plus the graph node.

    Keeping the vocabulary identical is what makes the two searches
    comparable: any difference in the answer is the search, not the moves.
    """
    inline_args = find_inline_args(bound.tree, bound.reverse_outflow)
    component_types = unique_identity([
        value for node in mismatches
        for value in (predicted.types.get(node), predicted.contexts.get(node))
    ])
    actions, seen = [], set()
    for graph_node in sorted(candidate_nodes, key=node_key):
        position_node = map_position(graph_node, positions)
        produced = predicted.types.get(graph_node)
        if position_node is None or produced is None:
            continue
        targets = unique_identity([
            predicted.contexts.get(graph_node),
            bound.type_contexts.get(graph_node),
            *component_types,
        ])
        for target in targets:
            try:
                wrapper = _choose(graph_node, produced, target,
                                  bound.valid_pair, inline_args, bound.dynamic,
                                  graph.must_agree(graph_node))
            except Exception:
                continue
            if type(wrapper) is Wrapper or wrapper.T is None:
                continue
            template = make_template(wrapper, graph_node)
            dump = ast.dump(template, include_attributes=False)
            position = position_node._global_position
            if (position, dump) in seen:
                continue
            seen.add((position, dump))
            actions.append(GraphAction(
                node=graph_node,
                produced=target,
                position=position,
                cost=template_call_count(template),
                description=ast.unparse(template).replace(SENTINEL, "VALUE"),
                template=template,
                origin_span=span(graph_node),
            ))
    return actions


class Stats:
    def __init__(self):
        self.binds = 0
        self.checks = 0
        self.pruned = 0
        self.unreachable = 0
        self.branch_sizes = []
        self.first_sizes = []


def guided_search(base, graph, bound, actions, budget, stats, deadline,
                  use_order=True, use_bound=True, use_smallest=True,
                  complete=False):
    """Depth-first to a fixed budget, branching on every error the bind found."""
    by_node = {}
    for action in actions:
        by_node.setdefault(action.node, []).append(action)
    reverse = {}
    for edge in graph.edges:
        reverse.setdefault(edge.target, set()).add(edge.source)
    slice_cache = {}
    memo = set()

    def slice_of(error):
        key = (error.lineno, error.offset, str(error)[:60])
        if key not in slice_cache:
            slice_cache[key] = error_positions(error, graph, bound, reverse)
        return slice_cache[key]

    def visit(selected, remaining, used_positions):
        if time.monotonic() >= deadline:
            return None
        key = tuple(sorted((a.position, a.description) for a in selected))
        if key in memo:
            return None
        memo.add(key)

        stats.binds += 1
        errors = all_errors(materialize_tree(base, tuple(selected)))
        if not errors:
            return tuple(selected)
        if remaining == 0:
            return None

        index = ReachIndex({id(e): slice_of(e) for e in errors})
        if use_bound:
            needed, _ = index.lower_bound()
            if needed > remaining:
                stats.pruned += 1
                return None

        if complete:
            # Every applicable action, ordered but never restricted. The slice
            # is only allowed to say "try this first", which reordering can
            # always do soundly - unlike the branching restriction below, which
            # cut fannkuch's known ten-wrapper solution at depth zero because
            # none of its positions sit in the tightest error's slice.
            uncovered = set(index.candidates_of)
            branches = [a for a in actions if a.position not in used_positions
                        and a.cost <= remaining]
            branches.sort(key=lambda a: ((-index.score(a.node, uncovered),)
                                         if use_order else ())
                                        + (a.cost, node_key(a.node), a.description))
            stats.branch_sizes.append(len(branches))
            stats.first_sizes.append(len(branches))
            for action in branches:
                used_positions.add(action.position)
                selected.append(action)
                result = visit(selected, remaining - action.cost, used_positions)
                selected.pop()
                used_positions.remove(action.position)
                if result is not None:
                    return result
            return None

        # An error no wrappable position reaches is a hole in the graph, not a
        # proof that this branch is dead: fannkuch's three "unions cannot
        # include primitives" errors have empty slices, and the mediator does
        # repair them - with wrappers placed elsewhere that change what goes
        # into the union. Cutting here on the first version of this search
        # ended it at depth zero. So they are excluded from the branching
        # choice and from the bound, and steer nothing.
        routable = {e: c for e, c in index.candidates_of.items() if c}
        if not routable:
            stats.unreachable += 1
            branches = [a for a in actions if a.position not in used_positions
                        and a.cost <= remaining]
            for action in branches:
                used_positions.add(action.position)
                selected.append(action)
                result = visit(selected, remaining - action.cost, used_positions)
                selected.pop()
                used_positions.remove(action.position)
                if result is not None:
                    return result
            return None

        # Branch on the tightest error; every solution must repair it.
        target = (min(routable, key=lambda e: len(routable[e])) if use_smallest
                  else next(e for e in (id(x) for x in errors) if e in routable))
        stats.branch_sizes.append(len(routable[target]))
        stats.first_sizes.append(len(index.candidates_of[id(errors[0])]) or len(actions))
        uncovered = set(routable)
        branches = [action for position in routable[target]
                    for action in by_node.get(position, ())
                    if action.position not in used_positions
                    and action.cost <= remaining]
        branches.sort(key=lambda a: ((-index.score(a.node, uncovered),) if use_order
                                     else ()) + (a.cost, node_key(a.node),
                                                 a.description))

        for action in branches:
            used_positions.add(action.position)
            selected.append(action)
            result = visit(selected, remaining - action.cost, used_positions)
            selected.pop()
            used_positions.remove(action.position)
            if result is not None:
                return result
        return None

    return visit([], budget, set())


def solve(source, mask, granularity, seconds, final_run, verbose=True, **knobs):
    bound = get_ast_data(ast.parse(source))
    graph = build_binding_graph(bound)
    units = graph.units(granularity)
    effective = mask or ((1 << len(units)) - 1)
    erased = graph.nodes_for_mask(effective, granularity)

    drift = check_reference(graph, bound, erased)
    if drift is not None:
        raise SystemExit(f"pinned_settle no longer matches Graph.settle: {drift}")

    predicted = graph.settle(bound, erased)
    mismatches = find_mismatches(graph, bound, predicted)

    mediator = detype(source, mask=mask, bench=granularity == "benchmark")
    generated = mark_generated_wrappers(source, mediator)
    generated_spans = {span(removable_operand(c) or c) for c in generated}

    base = baseline_tree(mediator)
    positions = baseline_positions(base)
    roots = set(mismatches)
    for node in ast.walk(bound.tree):
        if wrappable(node) and span(node) in generated_spans:
            roots.add(node)
    candidates = undirected_candidate_nodes(graph, roots)
    actions = build_graph_actions(graph, bound, predicted, mismatches,
                                  candidates, positions)

    stats = Stats()
    reverse = {}
    for edge in graph.edges:
        reverse.setdefault(edge.target, set()).add(edge.source)
    root_errors = all_errors(base)
    root_index = ReachIndex(
        {id(e): error_positions(e, graph, bound, reverse) for e in root_errors})
    root_bound, _ = root_index.lower_bound()
    if verbose:
        print(f"actions={len(actions)} positions={len({a.position for a in actions})}",
              file=sys.stderr)
        print(f"root errors={len(root_errors)} "
              f"unreachable={len(root_index.unreachable())} "
              f"root lower bound={root_bound}", file=sys.stderr)

    deadline = time.monotonic() + seconds
    # The baseline prunes the mediator's own wrappers before searching below
    # it. Skipping that left nqueens searching two budget levels that the
    # tightened bound rules out, which cost more than the guidance saved.
    upper, best_source, tighten_attempts = tighten_mediator_bound(
        source, mediator, generated, final_run)
    stats.binds += tighten_attempts
    if verbose:
        print(f"tightened mediator upper bound: {upper} "
              f"({tighten_attempts} attempts)", file=sys.stderr)

    for budget in range(root_bound, upper + 1):
        started = time.monotonic()
        selected = guided_search(base, graph, bound, actions, budget, stats,
                                 deadline, **knobs)
        if verbose:
            print(f"  budget {budget}: {stats.binds} binds cumulative, "
                  f"{time.monotonic() - started:.2f}s", file=sys.stderr)
        if selected is None:
            if time.monotonic() >= deadline:
                return None, stats, actions, root_bound
            continue
        candidate = ast.unparse(materialize_tree(base, selected))
        stats.checks += 1
        confirmed = check(candidate, final_run, 180)
        if confirmed.returncode == 0:
            return (selected, candidate), stats, actions, root_bound
        if verbose:
            tail = confirmed.stderr.strip().splitlines()
            print(f"  budget {budget}: bind-valid but rejected: "
                  f"{tail[-1] if tail else confirmed.returncode}", file=sys.stderr)
    # Nothing below the mediator survived, so the mediator is the answer -
    # the same fallback global_search makes, reported the same way.
    return (None, best_source), stats, actions, root_bound


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--mask", type=parse_mask, default=0)
    parser.add_argument("--granularity", choices=("annotation", "benchmark"),
                        default="annotation")
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()

    started = time.monotonic()
    result, stats, actions, root_bound = solve(
        args.source.read_text(), args.mask, args.granularity, args.seconds,
        args.run)
    elapsed = time.monotonic() - started

    print(f"binds: {stats.binds}", file=sys.stderr)
    print(f"static-python checks: {stats.checks}", file=sys.stderr)
    print(f"subtrees pruned by lower bound: {stats.pruned}", file=sys.stderr)
    print(f"subtrees cut as unreachable: {stats.unreachable}", file=sys.stderr)
    if stats.branch_sizes:
        chosen = sum(stats.branch_sizes) / len(stats.branch_sizes)
        first = sum(stats.first_sizes) / len(stats.first_sizes)
        print(f"mean branch positions: chosen={chosen:.1f} "
              f"first-error-would-be={first:.1f}", file=sys.stderr)
    print(f"elapsed: {elapsed:.3f}s", file=sys.stderr)
    if result is None:
        print("no result within budget", file=sys.stderr)
        raise SystemExit(2)
    selected, output = result
    if selected is None:
        print("minimum wrappers: mediator's own (nothing smaller found)",
              file=sys.stderr)
    else:
        print(f"minimum wrappers: {sum(a.cost for a in selected)}", file=sys.stderr)
        for action in selected:
            print(f"  line {getattr(action.node, 'lineno', 0)}: {action.description}",
                  file=sys.stderr)
    if args.output:
        args.output.write_text(output + "\n")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()

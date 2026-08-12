"""Best-first search over wrapper sets, ordered by how many errors remain.

The depth-first searches all failed the same way. `promising_search.py`
restricted branching to an error's reverse slice and cut fannkuch's known
ten-wrapper solution at depth zero. Removing the restriction made the tree
unsearchable - 8251 binds at budget ten, not close to exhausting. And greedy
descent on the reach score walked uphill, nine errors to twenty-eight, because
a wrapper can create errors and hill-climbing cannot back out of that.

A frontier backs out of it for free: the twenty-eight-error state sinks in the
queue and something better gets expanded instead. That is the whole reason to
try this.

    f = g + weight * h      g = wrappers spent, h = errors outstanding

`h` is not admissible. One wrapper can close several errors, so error count can
overestimate what is left to pay, and this is therefore weighted best-first
rather than A* in the guarantee sense - it finds solutions, it does not certify
minima. Nothing here ever certified minima: the reverse-slice assumption every
earlier bound rested on is false, so a real guarantee was never on the table.
`--weight 0` is the honest optimal setting - uniform-cost, expanding strictly
by wrapper count - and is exactly as expensive as that sounds.

Binds are the budget, so expansion is lazy. A child is pushed with its parent's
`h` as a stand-in and is only bound when it is popped; a pop whose true `f` is
worse than the estimate goes back on the queue instead of being expanded. One
bind per pop, rather than one per child.
"""
from __future__ import annotations

import argparse
import ast
import heapq
import sys
import time
from itertools import count
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(HERE)]

from detyper import detype
from cinderx_binding import get_ast_data
from typedness_graph import build_binding_graph
from brute_force_prune import check, mark_generated_wrappers, removable_operand
from global_search import (
    baseline_positions, baseline_tree, materialize_tree, span,
    undirected_candidate_nodes,
)
from print_instances import find_mismatches, node_key, parse_mask, wrappable
from promising_search import (
    ReachIndex, all_errors, build_graph_actions, error_positions,
)
from typedness_graph import TYPE


def forward_cone(graph, node):
    """Wrappable positions a change at `node` can reach going downstream.

    The reverse slice says which positions can repair an error. This is the
    other direction: which positions a wrapper here disturbs, and so roughly
    how much new breakage it risks. Every search before this one modelled only
    the repair half, which is why they all walked into states with more errors
    than they started with.
    """
    outgoing = graph.outgoing()
    seen = {(node, TYPE)}
    pending = [(node, TYPE)]
    touched = set()
    while pending:
        cell = pending.pop()
        if wrappable(cell[0]):
            touched.add(cell[0])
        for target in outgoing.get(cell, ()):
            if target not in seen:
                seen.add(target)
                pending.append(target)
    return touched


class Frontier:
    """A binary heap keyed on f, with insertion order breaking ties."""

    def __init__(self):
        self.heap = []
        self.tick = count()

    def push(self, f, blast, g, state):
        heapq.heappush(self.heap, (f, blast, g, next(self.tick), state))

    def pop(self):
        f, blast, g, _, state = heapq.heappop(self.heap)
        return f, g, state

    def __len__(self):
        return len(self.heap)


def search(base, actions, weight, max_binds, deadline, cap, verbose=True,
           graph=None, bound=None, estimate=False):
    """Expand states cheapest-f first until one binds clean."""
    cones = {}
    reverse = {}
    if estimate:
        for edge in graph.edges:
            reverse.setdefault(edge.target, set()).add(edge.source)
        for action in actions:
            if action.node not in cones:
                cones[action.node] = len(forward_cone(graph, action.node))

    frontier = Frontier()
    frontier.push(0, 0, 0, ())
    seen = {(): 0}
    binds = 0
    reinserted = 0
    best_h = None
    started = time.monotonic()

    while frontier and binds < max_binds and time.monotonic() < deadline:
        f, g, state = frontier.pop()
        selected = tuple(actions[i] for i in state)

        binds += 1
        errors = all_errors(materialize_tree(base, selected))
        h = len(errors)
        if h == 0:
            return SearchResult(selected, binds, reinserted, len(frontier),
                                best_h, time.monotonic() - started)

        true_f = g + weight * h
        if true_f > f:
            # The parent's estimate flattered this state. Re-file it rather
            # than expanding on a stale key; that is what keeps the frontier
            # ordered when h is only known after the bind.
            reinserted += 1
            frontier.push(true_f, 0, g, state)
            continue

        if best_h is None or h < best_h:
            best_h = h
            if verbose:
                print(f"  {binds:6d} binds  g={g:2d} h={h:2d} "
                      f"frontier={len(frontier)}", file=sys.stderr)

        if g >= cap:
            continue
        # How many of the errors standing right now does each position reach?
        # That is the repair half. The cone is the damage half.
        reach = {}
        if estimate:
            index = ReachIndex({id(e): error_positions(e, graph, bound, reverse)
                                for e in errors})
            for position, hit in index.reaches.items():
                reach[position] = len(hit)

        used = {actions[i].position for i in state}
        for idx, action in enumerate(actions):
            if action.position in used or idx in state:
                continue
            child = tuple(sorted(state + (idx,)))
            child_g = g + action.cost
            if child_g > cap:
                continue
            if seen.get(child, 1 << 30) <= child_g:
                continue
            seen[child] = child_g
            if estimate:
                # A wrapper cannot close what it does not reach, so the best
                # this child can be is h minus what it reaches. Ties go to the
                # smaller blast radius: of two equally promising repairs, the
                # one disturbing fewer positions is the one less likely to hand
                # the next state more errors than it removed.
                child_h = max(0, h - reach.get(action.node, 0))
                blast = cones.get(action.node, 0)
            else:
                child_h, blast = h, 0
            frontier.push(child_g + weight * child_h, blast, child_g, child)

    return SearchResult(None, binds, reinserted, len(frontier), best_h,
                        time.monotonic() - started)


class SearchResult:
    def __init__(self, selected, binds, reinserted, frontier, best_h, elapsed):
        self.selected = selected
        self.binds = binds
        self.reinserted = reinserted
        self.frontier = frontier
        self.best_h = best_h
        self.elapsed = elapsed


def prepare(source, mask, granularity):
    bound = get_ast_data(ast.parse(source))
    graph = build_binding_graph(bound)
    units = graph.units(granularity)
    effective = mask or ((1 << len(units)) - 1)
    erased = graph.nodes_for_mask(effective, granularity)
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
    actions.sort(key=lambda a: (node_key(a.node), a.description))
    return base, actions, mediator, generated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--mask", type=parse_mask, default=0)
    parser.add_argument("--granularity", choices=("annotation", "benchmark"),
                        default="annotation")
    parser.add_argument("--weight", type=float, default=1.0,
                        help="0 = uniform cost, large = greedy best-first")
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--max-binds", type=int, default=1_000_000)
    parser.add_argument("--cap", type=int, default=None,
                        help="highest wrapper cost to consider")
    parser.add_argument("--estimate", action="store_true",
                        help="order children by (errors reached, blast radius)")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()

    source = args.source.read_text()
    base, actions, mediator, generated = prepare(source, args.mask,
                                                 args.granularity)
    cap = args.cap if args.cap is not None else len(generated)
    print(f"actions={len(actions)} mediator={len(generated)} cap={cap} "
          f"weight={args.weight}", file=sys.stderr)

    bound = get_ast_data(ast.parse(source))
    graph = build_binding_graph(bound)
    result = search(base, actions, args.weight, args.max_binds,
                    time.monotonic() + args.seconds, cap, graph=graph,
                    bound=bound, estimate=args.estimate)
    print(f"binds={result.binds} reinserted={result.reinserted} "
          f"frontier={result.frontier} best_h={result.best_h} "
          f"elapsed={result.elapsed:.1f}s", file=sys.stderr)

    if result.selected is None:
        print("no solution found", file=sys.stderr)
        raise SystemExit(2)

    candidate = ast.unparse(materialize_tree(base, result.selected))
    confirmed = check(candidate, args.run, 180)
    cost = sum(a.cost for a in result.selected)
    print(f"found {len(result.selected)} wrappers, cost {cost}; "
          f"static check {'passed' if confirmed.returncode == 0 else 'FAILED'}",
          file=sys.stderr)
    for action in result.selected:
        print(f"  line {getattr(action.node, 'lineno', 0)}: {action.description}",
              file=sys.stderr)
    if args.output:
        args.output.write_text(candidate + "\n")
        print(f"wrote {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()

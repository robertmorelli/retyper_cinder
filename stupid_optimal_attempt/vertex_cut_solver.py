"""Minimum wrap set as a minimum vertex separator, straight off the graph.

`min_cut_solver.py` labels *locals* and needs bool_solver's phase 1 to measure a
cost table per statement, which is where five of the twelve benchmarks die. This
takes the other formulation: a wrapper is a **position**, not a relationship, so
the object to cut is a vertex.

    S ---> ...typedness graph edges... ---> mismatch ---> T

Every mismatch (a position where the settled produced type does not satisfy the
settled demanded context) is a sink. Every path reaching it from an origin has to
be interrupted by a wrapper at some wrappable position along the way. That is
exactly a minimum (S,T) vertex separator, which node splitting turns into an edge
max-flow: each wrappable position becomes `in -> out` with capacity equal to its
wrap cost, every graph edge gets infinite capacity, and no cut can do anything
except pay for positions.

Why this fits better than the edge formulation:

  * subsumption is native. `int64(a + b)` is one vertex covering both operands,
    where an edge model has to correlate two edges and `solve_and_apply.py`'s
    "independently selected sibling repairs conflict" is what that costs.
  * no probing. The network comes from `settle` plus `find_mismatches`, so it is
    built in graph time and does not care whether phase 1 can fill a table.
  * the cut is a lattice. Max-flow leaves a residual graph whose source-side and
    sink-side extremal cuts are both minimum, so ties break canonically: wrap as
    early as possible, or as late as possible (`--late`), the latter keeping more
    of the program on the primitive path at the same wrapper count.

Minimum vertex cut between two terminals is polynomial (Menger); it is *multiway*
vertex cut, three or more mutually separated labels, that is NP-hard. Two
terminals is where the tractability lives, which is the same boundary as the
two-label restriction in ../ITS_MIN_CUT_MAX_FLOW_BABY.md.

  python stupid_optimal_attempt/vertex_cut_solver.py program.py
  python stupid_optimal_attempt/vertex_cut_solver.py program.py --late --emit
  python stupid_optimal_attempt/vertex_cut_solver.py program.py --mask 0x25
"""
from __future__ import annotations

import argparse
import ast
import sys
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(HERE)]

from cinderx_binding import get_ast_data
from detyper import detype
from inline_call_analysis import find_inline_args
from patch_picker import pick_patch
from print_instances import (find_mismatches, parse_mask, wrapper_depth,
                             wrappable)
from typedness_graph import CONTEXT, TYPE, build_binding_graph

INF = float("inf")


class Flow:
    def __init__(self):
        self.graph: list[list] = []

    def node(self):
        self.graph.append([])
        return len(self.graph) - 1

    def add(self, u, v, capacity):
        self.graph[u].append([v, capacity, len(self.graph[v])])
        self.graph[v].append([u, 0, len(self.graph[u]) - 1])

    def max_flow(self, source, sink):
        total = 0
        while True:
            level = [-1] * len(self.graph)
            level[source] = 0
            queue = deque([source])
            while queue:
                u = queue.popleft()
                for v, capacity, _ in self.graph[u]:
                    if capacity > 0 and level[v] < 0:
                        level[v] = level[u] + 1
                        queue.append(v)
            if level[sink] < 0:
                return total
            iterator = [0] * len(self.graph)
            stack_limit = sys.getrecursionlimit()
            sys.setrecursionlimit(max(stack_limit, len(self.graph) * 4 + 1000))

            def augment(u, limit):
                if u == sink:
                    return limit
                while iterator[u] < len(self.graph[u]):
                    edge = self.graph[u][iterator[u]]
                    v, capacity, back = edge
                    if capacity > 0 and level[v] == level[u] + 1:
                        pushed = augment(v, min(limit, capacity))
                        if pushed:
                            edge[1] -= pushed
                            self.graph[v][back][1] += pushed
                            return pushed
                    iterator[u] += 1
                return 0

            while True:
                pushed = augment(source, INF)
                if not pushed:
                    break
                total += pushed

    def side(self, start, forward=True):
        """Residual reachability: forward from S, or backward from T."""
        seen = {start}
        queue = deque([start])
        while queue:
            u = queue.popleft()
            if forward:
                for v, capacity, _ in self.graph[u]:
                    if capacity > 0 and v not in seen:
                        seen.add(v)
                        queue.append(v)
            else:
                for v, _capacity, back in self.graph[u]:
                    if self.graph[v][back][1] > 0 and v not in seen:
                        seen.add(v)
                        queue.append(v)
        return seen


def network(graph, mismatches, costs):
    """Split every cell; only wrappable positions get a finite capacity.

    A cell is `(ast node, slot)`. Both of a node's cells share its single
    capacity, because one wrapper at that position repairs the node however the
    demand arrived -- that sharing is the thing an edge model cannot say.
    """
    flow = Flow()
    source, sink = flow.node(), flow.node()
    cells = {edge.source for edge in graph.edges} | {edge.target
                                                    for edge in graph.edges}
    for node in mismatches:
        cells |= {(node, TYPE), (node, CONTEXT)}

    gate: dict[ast.AST, tuple] = {}       # ast node -> (in, out)
    for node in {cell[0] for cell in cells}:
        entry, exit_ = flow.node(), flow.node()
        gate[node] = (entry, exit_)
        flow.add(entry, exit_, costs.get(node, INF) if wrappable(node) else INF)

    for edge in graph.edges:
        flow.add(gate[edge.source[0]][1], gate[edge.target[0]][0], INF)

    reverse: dict[tuple, set] = {}
    for edge in graph.edges:
        reverse.setdefault(edge.target, set()).add(edge.source)

    # Origins: cells in a mismatch's reverse slice with nothing feeding them.
    origins, sinks = set(), set()
    for node in mismatches:
        sinks.add(node)
        pending = [(node, TYPE), (node, CONTEXT)]
        seen = set(pending)
        while pending:
            cell = pending.pop()
            feeds = reverse.get(cell, ())
            if not feeds:
                origins.add(cell[0])
            for predecessor in feeds:
                if predecessor not in seen:
                    seen.add(predecessor)
                    pending.append(predecessor)
    for node in origins:
        flow.add(source, gate[node][0], INF)
    for node in sinks:
        flow.add(gate[node][1], sink, INF)
    return flow, gate, source, sink, origins, sinks


def cut_positions(flow, gate, source, sink, late):
    """The split edges the cut pays for: the wrap set."""
    reachable = flow.side(sink, forward=False) if late else flow.side(source)
    chosen = []
    for node, (entry, exit_) in gate.items():
        if late:
            crossing = exit_ in reachable and entry not in reachable
        else:
            crossing = entry in reachable and exit_ not in reachable
        if crossing:
            chosen.append(node)
    return chosen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--mask", type=parse_mask, default=0)
    parser.add_argument("--granularity", choices=("annotation", "benchmark"),
                        default="annotation")
    parser.add_argument("--late", action="store_true",
                        help="the sink-side minimum cut: wrap as late as "
                             "possible, keeping more of the program primitive")
    parser.add_argument("--emit", action="store_true")
    parser.add_argument("--show", type=int, default=10)
    args = parser.parse_args()

    source = args.source.read_text()
    bound = get_ast_data(ast.parse(source))
    graph = build_binding_graph(bound)
    units = graph.units(args.granularity)
    erased = graph.nodes_for_mask(args.mask or ((1 << len(units)) - 1),
                                 args.granularity)
    predicted = graph.settle(bound, erased)
    mismatches = find_mismatches(graph, bound, predicted)
    costs = {node: mismatch.local_cost for node, mismatch in mismatches.items()}
    for node in predicted.types:
        costs.setdefault(node, 1)

    started = time.monotonic()
    flow, gate, s, t, origins, sinks = network(graph, mismatches, costs)
    cut = flow.max_flow(s, t)
    chosen = cut_positions(flow, gate, s, t, args.late)
    elapsed = time.monotonic() - started

    mediator = detype(source, mask=args.mask,
                      bench=args.granularity == "benchmark")
    from patch_picker import PRIMITIVE_NAMES
    wrapper_names = {"cast", "box"} | set(PRIMITIVE_NAMES)
    mediator_wraps = sum(
        1 for n in ast.walk(mediator)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id in wrapper_names)
    original_wraps = sum(
        1 for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id in wrapper_names)

    print(f"{args.source.name}: {len(units)} units, mask={args.mask}")
    print(f"  cells {len(gate)}, graph edges {len(graph.edges)}, "
          f"mismatches {len(mismatches)}, origins {len(origins)}")
    print(f"  MIN VERTEX CUT = {cut:g}   "
          f"({'sink-side, wrap late' if args.late else 'source-side, wrap early'})")
    print(f"  positions chosen: {len(chosen)}")
    print(f"  mediator emits {mediator_wraps} wrappers "
          f"(source itself has {original_wraps})")
    print(f"  built and solved in {elapsed:.2f}s")
    for node in chosen[:args.show]:
        text = ast.unparse(node)[:52]
        print(f"    line {getattr(node, 'lineno', 0)}: {text}")

    if args.emit:
        inline_args = find_inline_args(bound.tree, bound.reverse_outflow,
                                      predicted.types)
        applied = 0
        for node in chosen:
            produced = predicted.types.get(node)
            demanded = predicted.contexts.get(node)
            if produced is None or demanded is None:
                continue
            try:
                pick_patch(node, produced, demanded, bound.valid_pair,
                           inline_args, predicted.types, predicted.contexts,
                           bound.dynamic, bound.constructors)
                applied += 1
            except Exception as error:
                print(f"    could not pick a wrapper at line "
                      f"{getattr(node, 'lineno', 0)}: {error}")
        print(f"  wrappers picked at {applied}/{len(chosen)} positions "
              "(emission of the whole tree is not wired up yet)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

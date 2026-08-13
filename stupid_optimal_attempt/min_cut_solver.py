"""Minimum coercions by max flow, exactly, when the energy is submodular.

`check_submodular.py` says the measured cost tables satisfy
`E(D,D) + E(T,T) <= E(D,T) + E(T,D)` on every 2-variable projection. That is the
condition under which minimising the total wrap count is not a search but an s-t
min cut, so this builds the cut and runs Dinic instead of phase 3's DFS.

The graph, with S = TYPED and T = DYNAMIC:

  * one node per local's bit; pinned bits get an infinite terminal edge
  * a unary cost `u` for being TYPED becomes a `u` edge to T, and for being
    DYNAMIC a `u` edge to S
  * a pairwise table (A,B,C,D) decomposes exactly as
        E(x,y) = A + (C-A)x + (D-C)y + lambda * (1-x)y,  lambda = B+C-A-D >= 0
    whose residual is one directed edge of capacity lambda
  * an arity-k table is reduced pair by pair the same way after fixing the other
    variables at their cheapest setting, which is exact only when the table is
    pairwise-decomposable; --exhaustive checks that claim against brute force

Negative unary coefficients are shifted into the constant, so every capacity is
non-negative and Dinic applies.

  python stupid_optimal_attempt/min_cut_solver.py program.py
  python stupid_optimal_attempt/min_cut_solver.py program.py --exhaustive
  python stupid_optimal_attempt/min_cut_solver.py program.py --mask 0x25
"""
from __future__ import annotations

import argparse
import ast
import itertools
import sys
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(HERE)]

from bool_solver import (TYPED, UNTYPED, bit_domains, concrete_types,
                         phase_one, phase_two, scope_of)
from brute_force_prune import mark_generated_wrappers
from bool_solver import materialize as materialize_plans
from brute_force_prune import check
from check_submodular import factors_for
from cinderx_binding import get_ast_data
from detyper import detype
from global_search import baseline_tree
from print_instances import parse_mask
from probes import find_candidates, strip_module
from typedness_graph import build_binding_graph

INF = float("inf")


class Flow:
    """Dinic. Capacities are wrap counts, so ints, so no epsilon games."""

    def __init__(self, nodes):
        self.graph = [[] for _ in range(nodes)]

    def add(self, u, v, capacity, reverse=0):
        self.graph[u].append([v, capacity, len(self.graph[v])])
        self.graph[v].append([u, reverse, len(self.graph[u]) - 1])

    def max_flow(self, source, sink):
        flow = 0
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
                return flow
            iterator = [0] * len(self.graph)

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
                flow += pushed

    def reachable(self, source):
        seen = {source}
        queue = deque([source])
        while queue:
            u = queue.popleft()
            for v, capacity, _ in self.graph[u]:
                if capacity > 0 and v not in seen:
                    seen.add(v)
                    queue.append(v)
        return seen


def mobius(table, arity, big):
    """Exact multilinear expansion: E(x) = sum_S c_S * prod_{i in S} x_i.

    x_i = 1 means that local arrives TYPED. An assignment the probe found
    infeasible is `big` rather than infinity, so a hard constraint stays a
    capacity the cut can respect instead of an arithmetic hole.
    """
    def value(bits):
        return table.get(tuple(TYPED if b else UNTYPED for b in bits), big)

    coefficients = {}
    for mask in range(1 << arity):
        subset = tuple(i for i in range(arity) if mask >> i & 1)
        total = 0
        for sub in range(1 << arity):
            if sub & ~mask:
                continue
            bits = [1 if sub >> i & 1 else 0 for i in range(arity)]
            total += (-1) ** bin(mask ^ sub).count("1") * value(bits)
        if total:
            coefficients[subset] = total
    return coefficients


def factors_with_plans(candidates, tables):
    """Same factors, but each row keeps the wrap plan that achieved its cost."""
    out = []
    for candidate in candidates:
        scope = scope_of(candidate.function)
        variables = [(scope, name) for name in candidate.reads]
        root_var = (scope, candidate.root_name) if candidate.root_name else None
        if root_var and root_var not in variables:
            variables.append(root_var)
        allowed = {}
        for (bits, root_bit), (cost, plan) in tables.get(candidate.key, {}).items():
            base = dict(zip([(scope, n) for n in candidate.reads], bits))
            options = (TYPED, UNTYPED) if root_var else (None,)
            for value in options:
                assignment = dict(base)
                if root_var:
                    if value is TYPED and root_bit is not TYPED:
                        continue
                    if root_var in assignment and assignment[root_var] is not value:
                        continue
                    assignment[root_var] = value
                key = tuple(assignment[name] for name in variables)
                if key not in allowed or cost < allowed[key][0]:
                    allowed[key] = (cost, plan)
        if variables:
            out.append((candidate, tuple(variables), allowed))
    return out


def build(factors, pins, big):
    """Terminal and internal capacities for the whole program's energy.

    Every factor is expanded exactly, then each coefficient becomes graph
    structure:

      order 0   a constant
      order 1   c * x   ->  a terminal edge (either direction, after shifting a
                            negative coefficient into the constant)
      order 2   w * x*y with w <= 0  ->  constant w, a `|w|` edge to S on x,
                            and a `|w|` edge x -> y. Verified at the corners:
                            (1,1) -> w, every other corner -> 0.

    A positive order-2 coefficient is non-submodular and an order-3 or higher
    coefficient is not pairwise; either aborts rather than being approximated.
    """
    names = sorted({name for _, variables, _ in factors for name in variables})
    index = {name: position + 2 for position, name in enumerate(names)}
    SOURCE, SINK = 0, 1
    flow = Flow(len(names) + 2)
    constant = 0
    to_sink: dict[int, float] = {}     # cost of being TYPED
    to_source: dict[int, float] = {}   # cost of being DYNAMIC
    internal: dict[tuple, float] = {}
    orders: dict[int, int] = {}

    def typed_cost(node, cost):
        """Charge `cost` when this bit is TYPED; negatives flip to the constant."""
        nonlocal constant
        if cost >= 0:
            to_sink[node] = to_sink.get(node, 0) + cost
        else:
            constant += cost
            to_source[node] = to_source.get(node, 0) - cost

    empty = 0
    for _candidate, variables, table in factors:
        if not table:
            # No feasible row at all. Skipping it silently is how this reported
            # "0 wraps" for programs whose factors phase 1 never filled, so it
            # is counted and surfaced instead.
            empty += 1
            continue
        coefficients = mobius(table, len(variables), big)
        for subset, weight in coefficients.items():
            orders[len(subset)] = orders.get(len(subset), 0) + 1
            if not subset:
                constant += weight
            elif len(subset) == 1:
                typed_cost(index[variables[subset[0]]], weight)
            elif len(subset) == 2:
                if weight > 0:
                    raise SystemExit(
                        f"non-submodular pair coefficient {weight} on "
                        f"{variables[subset[0]][1]} x {variables[subset[1]][1]}; "
                        "run check_submodular.py")
                x = index[variables[subset[0]]]
                y = index[variables[subset[1]]]
                constant += weight
                to_source[x] = to_source.get(x, 0) - weight
                internal[(x, y)] = internal.get((x, y), 0) - weight
            else:
                raise SystemExit(f"order-{len(subset)} coefficient {weight}; "
                                 "the energy is not pairwise, so this needs "
                                 "auxiliary nodes")

    for name, node in index.items():
        pin = pins.get(name)
        if pin is TYPED:
            flow.add(SOURCE, node, INF)
        elif pin is UNTYPED:
            flow.add(node, SINK, INF)
    for node, capacity in to_sink.items():
        flow.add(node, SINK, capacity)
    for node, capacity in to_source.items():
        flow.add(SOURCE, node, capacity)
    for (u, v), capacity in internal.items():
        flow.add(u, v, capacity)
    return flow, index, constant, SOURCE, SINK, orders, empty


def brute_force(factors, pins, names, big):
    """Exhaustive minimum over the same factors, for validating the cut."""
    free = [n for n in names if n not in pins]
    best = INF
    for combination in itertools.product((UNTYPED, TYPED), repeat=len(free)):
        assignment = dict(pins)
        assignment.update(zip(free, combination))
        total = 0
        for _candidate, variables, table in factors:
            total += table.get(tuple(assignment[v] for v in variables), big)
        best = min(best, total)
    return best


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--mask", type=parse_mask, default=0)
    parser.add_argument("--granularity", choices=("annotation", "benchmark"),
                        default="annotation")
    parser.add_argument("--max-wraps", type=int, default=4)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--emit", action="store_true",
                        help="materialise the wrap set and compile it")
    parser.add_argument("--run", action="store_true",
                        help="with --emit, run it as well as compiling")
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--exhaustive", action="store_true",
                        help="also brute force the same factors and compare")
    args = parser.parse_args()

    source = args.source.read_text()
    bound = get_ast_data(ast.parse(source))
    graph = build_binding_graph(bound)
    units = graph.units(args.granularity)
    erased = graph.nodes_for_mask(args.mask or ((1 << len(units)) - 1),
                                 args.granularity)
    mediator = detype(source, mask=args.mask,
                      bench=args.granularity == "benchmark")
    mark_generated_wrappers(source, mediator)
    base = baseline_tree(mediator)
    probe_base = strip_module(base)
    candidates = find_candidates(base)
    concrete = concrete_types(bound)

    started = time.monotonic()
    tables, probes = phase_one(probe_base, candidates, concrete, args.max_wraps,
                               args.workers, True)
    phase_two(candidates, tables, True)
    factors = factors_for(candidates, tables)
    plan_tables = factors_with_plans(candidates, tables)
    measured = time.monotonic() - started

    fixed, free, untyped = bit_domains(graph, bound, erased, candidates)
    pins = {}
    names = sorted({name for _, variables, _ in factors for name in variables})
    for name in names:
        if name in untyped:
            pins[name] = UNTYPED
        elif name in fixed:
            pins[name] = TYPED

    cut_started = time.monotonic()
    big = 10 ** 6
    flow, index, constant, source_node, sink_node, orders, empty = build(
        factors, pins, big)
    cut = flow.max_flow(source_node, sink_node)
    reachable = flow.reachable(source_node)
    cut_time = time.monotonic() - cut_started

    typed = sorted(name[1] for name, node in index.items() if node in reachable)
    print(f"\n{args.source.name}: {len(names)} bits "
          f"({len(pins)} pinned, {len(names) - len(pins)} free), "
          f"{len(factors)} factors, {probes} probes")
    print(f"min cut  = {constant + cut:g} wraps  "
          f"(constant {constant:g} + cut {cut:g})")
    print(f"typed bits at the optimum: {', '.join(typed) or 'none'}")
    if empty or constant <= -big or cut >= big:
        print(f"UNUSABLE: {empty} factors had no feasible row and the "
              f"infeasibility constant leaked into the arithmetic. Phase 1 "
              f"could not fill this program's tables at --max-wraps "
              f"{args.max_wraps}.")
    print("coefficient orders: " +
          " ".join(f"{k}:{v}" for k, v in sorted(orders.items())))
    print(f"phase 1 {measured:.1f}s, max flow {cut_time * 1000:.1f}ms")

    if args.emit:
        # Read a concrete wrap plan off the optimal assignment: each statement
        # takes its cheapest row consistent with the bits the cut chose.
        assignment = {}
        for name, node in index.items():
            assignment[name] = TYPED if node in reachable else UNTYPED
        assignment.update(pins)
        plans, missing = {}, 0
        for candidate, variables, table in plan_tables:
            key = tuple(assignment[v] for v in variables)
            hit = table.get(key)
            if hit is None:
                missing += 1
                continue
            plans[candidate.key] = hit[1]
        text = materialize_plans(base, plans)
        wraps = sum(len(plan) for plan in plans.values())
        print(f"emitted {wraps} wrappers from {len(plans)} statements"
              + (f", {missing} statements had no row for the chosen bits"
                 if missing else ""))
        result = check(text, args.run, 180)
        print("COMPILES" if result.returncode == 0 else
              "REJECTED: " + (result.stderr.strip().splitlines() or [""])[-1][:110])
        if args.output:
            args.output.write_text(text + "\n")

    if args.exhaustive:
        free_count = len(names) - len(pins)
        if free_count > 22:
            print(f"brute force skipped: 2^{free_count} assignments")
        else:
            started = time.monotonic()
            best = brute_force(factors, pins, names, big)
            print(f"brute force = {best:g} wraps  "
                  f"({time.monotonic() - started:.1f}s over 2^{free_count})")
            print("MATCH" if best == constant + cut else
                  f"MISMATCH: cut {constant + cut:g} vs true {best:g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

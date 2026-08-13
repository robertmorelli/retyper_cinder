"""Is the coercion-cost energy submodular? If it is, min cut solves it exactly.

Phase 1 of bool_solver measures, per statement, the cheapest wrap plan for every
combination of "which of my read locals arrive typed". That is a factor over
boolean variables. A pairwise pseudo-boolean energy is minimisable exactly by
s-t min cut iff every factor satisfies

    E(DYN,DYN) + E(TYPED,TYPED)  <=  E(DYN,TYPED) + E(TYPED,DYN)

on every 2-variable projection (Hammer 1965; Kolmogorov & Zabih 2004). This
enumerates the measured factors and tests exactly that. See
../ITS_MIN_CUT_MAX_FLOW_BABY.md.

An infeasible corner counts as +inf, which makes the test also answer "is any
hard constraint a *must differ*": that is the one kind of pin that breaks the
construction, and it shows up here as a violation with an infinite left side.

  python stupid_optimal_attempt/check_submodular.py path/to/main.py
  python stupid_optimal_attempt/check_submodular.py program.py --mask 0x25
"""
from __future__ import annotations

import argparse
import ast
import itertools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(HERE)]

from bool_solver import (TYPED, UNTYPED, concrete_types, phase_one, phase_two,
                         scope_of)
from brute_force_prune import mark_generated_wrappers
from cinderx_binding import get_ast_data
from detyper import detype
from global_search import baseline_tree
from print_instances import parse_mask
from probes import find_candidates, strip_module
from typedness_graph import build_binding_graph

INF = float("inf")


def factors_for(candidates, tables):
    """The same factor construction phase_three does, without the search.

    A factor is (statement key, variable tuple, {bit tuple: cost}); a bit tuple
    absent from the table is infeasible, not free.
    """
    factors = []
    for candidate in candidates:
        scope = scope_of(candidate.function)
        variables = [(scope, name) for name in candidate.reads]
        root_var = (scope, candidate.root_name) if candidate.root_name else None
        if root_var and root_var not in variables:
            variables.append(root_var)
        allowed: dict[tuple, float] = {}
        for (bits, root_bit), (cost, _plan) in tables.get(candidate.key, {}).items():
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
                if key not in allowed or cost < allowed[key]:
                    allowed[key] = cost
        if variables:
            factors.append((candidate, tuple(variables), allowed))
    return factors


def projections(variables, allowed):
    """Every 2-variable projection: the other variables held fixed."""
    for i, j in itertools.combinations(range(len(variables)), 2):
        others = [k for k in range(len(variables)) if k not in (i, j)]
        for rest in itertools.product((UNTYPED, TYPED), repeat=len(others)):
            fixed = dict(zip(others, rest))

            def corner(bit_i, bit_j):
                key = [None] * len(variables)
                for index, value in fixed.items():
                    key[index] = value
                key[i], key[j] = bit_i, bit_j
                return allowed.get(tuple(key), INF)

            yield (i, j, fixed,
                   corner(UNTYPED, UNTYPED), corner(UNTYPED, TYPED),
                   corner(TYPED, UNTYPED), corner(TYPED, TYPED))


def describe(candidate, variables, i, j):
    line = getattr(candidate.statement, "lineno", 0)
    text = ast.unparse(candidate.statement).splitlines()[0][:60]
    return (f"line {line}: {text}\n"
            f"      {variables[i][1]} x {variables[j][1]}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--mask", type=parse_mask, default=0)
    parser.add_argument("--granularity", choices=("annotation", "benchmark"),
                        default="annotation")
    parser.add_argument("--max-wraps", type=int, default=2)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--show", type=int, default=12,
                        help="violations to print in full")
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

    print(f"{args.source.name}: {len(units)} units, mask={args.mask}, "
          f"{len(erased)} erased nodes, {len(candidates)} statements",
          file=sys.stderr)
    tables, probes = phase_one(probe_base, candidates, concrete, args.max_wraps,
                               args.workers, True)
    print(f"phase 1: {probes} probed rows", file=sys.stderr)
    phase_two(candidates, tables, True)
    factors = factors_for(candidates, tables)

    arities: dict[int, int] = {}
    tested = violations = infinite = 0
    shown = 0
    for candidate, variables, allowed in factors:
        arities[len(variables)] = arities.get(len(variables), 0) + 1
        for i, j, _fixed, a, b, c, d in projections(variables, allowed):
            if a == INF and d == INF:
                continue                      # nothing to compare
            tested += 1
            left, right = a + d, b + c
            if left > right:
                violations += 1
                if left == INF:
                    infinite += 1
                if shown < args.show:
                    shown += 1
                    print(f"  VIOLATION  A+D={left} > B+C={right}   "
                          f"(A={a} B={b} C={c} D={d})\n"
                          f"      {describe(candidate, variables, i, j)}")
    print(f"\nfactors {len(factors)}  arities " +
          " ".join(f"{k}:{v}" for k, v in sorted(arities.items())))
    print(f"projections tested {tested}")
    print(f"violations {violations}" +
          (f" ({infinite} from an infeasible agreeing corner)" if infinite else ""))
    print("SUBMODULAR -- min cut is exact for this instance" if not violations
          else "NOT submodular -- max-flow would only bound the optimum")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Minimum coercions, evaluated on the typedness graph. See boolean_search.md.

Every earlier attempt in this folder asked CinderX about each statement and
rebuilt the statement's context to do it. That context is what the graph holds,
and rebuilding it by hand went wrong in the same way each time. This asks the
graph instead, and asks CinderX exactly once, about the answer.

Identity comes from edges, never from names. `visit_Name` links every binder of
a name to each read of it, so `by_type[read]` is the back-edge to the
assignments that decide that read's type. Keying on the name instead welded
`v` in `__init__`, `advance`, `offset_momentum` and `report_energy` into one
variable - nbody shares 14 of its 41 local names across functions - and made
the conjunction unsatisfiable for reasons that had nothing to do with the
program.

  bits      one per binder whose type a wrap could change. `survives_erasure`
            decides whether inference gets the type back on its own, in which
            case there is nothing to choose.
  evaluate  `pinned_settle` propagates a bit assignment; the coercion at each
            position follows from the types either side of it; `propagate`
            carries each one's effect as it is applied, post-order, the way the
            mediator does.
  check     the graph decides whether an assignment is coherent. CinderX
            confirms the winner.
"""
from __future__ import annotations

import argparse
import ast
import copy
import itertools
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(HERE)]

from annotation_remover import remove_annotations
from cinderx_binding import get_ast_data
from detyper import detype
from import_adder import add_imports
from inline_call_analysis import find_inline_args
from type_mediator import coerce_tree
from typedness_graph import CONTEXT, TYPE, build_binding_graph
from brute_force_prune import call_key, check, removable_operand
from graph_oracle import full_propagate, pinned_settle, check_reference
from print_instances import parse_mask, wrappable
from three_phase import DYNAMIC, determined_kind, type_name


@dataclass(frozen=True)
class Bit:
    """One binder whose typedness is ours to choose.

    `node` is the binder the graph indexed - an AnnAssign, or the Name being
    stored to - not a name. Two binders that happen to share a spelling are two
    bits, because they are two nodes.
    """
    node: ast.AST
    value: ast.AST | None       # the expression a wrap would go around
    scope: str                  # for reporting only
    name: str                   # for reporting only


def binder_value(statement) -> ast.AST | None:
    if isinstance(statement, (ast.Assign, ast.AnnAssign)):
        return statement.value
    return None


def free_bits(graph, bound, erased) -> list[Bit]:
    """Binders whose type a wrap could change.

    A binder is not a choice when inference puts the type back by itself -
    `survives_erasure` is the graph's own answer to that - nor when it has no
    value expression to wrap.
    """
    parents = {}
    for parent in ast.walk(bound.tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent

    bits = []
    seen = set()
    for (scope, name), binders in graph.bindings.items():
        for binder in binders:
            if id(binder) in seen:
                continue
            seen.add(id(binder))
            statement = binder if isinstance(binder, ast.AnnAssign) else \
                parents.get(id(binder))
            value = binder_value(statement)
            if value is None or not wrappable(value):
                continue
            if graph.survives_erasure(binder, bound):
                # inference recovers this on its own; nothing to decide
                continue
            bits.append(Bit(node=binder, value=value,
                            scope=getattr(scope, "name", "<module>"),
                            name=name))
    return sorted(bits, key=lambda b: (getattr(b.node, "lineno", 0),
                                       getattr(b.node, "col_offset", 0)))


def reads_of(graph, bit: Bit) -> list:
    """Reads this binder feeds, by out-edge. Reporting and sanity only."""
    outgoing = graph.outgoing()
    return [target[0] for target in outgoing.get(graph.cell(bit.node, TYPE), ())
            if target[1] == TYPE]


def settle_for(graph, bound, erased, chosen: frozenset, bits):
    """The types implied by one bit assignment."""
    pinned = {}
    for index in chosen:
        node = bits[index].node
        value = bound.types.get(node, bound.dynamic)
        pinned[node] = value
    return pinned_settle(graph, bound, erased, pinned)


def coercions_for(graph, bound, state):
    """Where a coercion is needed, and which one, given a settled state.

    Both halves come from the settled state, so this is the graph answering
    rather than a bind: a position's produced type against the type its context
    demands. `propagate` carries each decision onward as it is taken, so a
    position sees the effect of the ones below it - which is why this walks
    post-order like the mediator, and why one wrap high can remove the need for
    two below.
    """
    types = dict(state.types)
    contexts = dict(state.contexts)
    plan = {}
    order = []

    def post(node):
        for child in ast.iter_child_nodes(node):
            post(child)
        order.append(node)
    post(bound.tree)

    for node in order:
        if not wrappable(node):
            continue
        produced = type_name(types.get(node), bound.dynamic)
        demanded = type_name(contexts.get(node), bound.dynamic)
        kind = determined_kind(produced, demanded)
        if kind is None:
            continue
        plan[id(node)] = (node, kind)
        # the position now yields what its context asked for
        types[node] = contexts.get(node)
        full_propagate(graph, {node: contexts.get(node)}, types, contexts)
    return plan, types, contexts


def coherent(graph, bound, types, contexts) -> bool:
    """Does anything still disagree once every coercion has been applied?

    Only positions the graph actually links are asked about: a node with no
    context edge keeps whatever demand the annotated program made on it, which
    the erased program never makes, and reading those stale entries is what
    made an earlier version report 35 mismatches on a program that compiles.
    """
    _, by_context = graph.sources()
    for node in by_context:
        if not wrappable(node):
            continue
        produced = types.get(node)
        demanded = contexts.get(node)
        if produced is None or demanded is None:
            continue
        if determined_kind(type_name(produced, bound.dynamic),
                           type_name(demanded, bound.dynamic)) is not None:
            return False
    return True


def emit(source, mask, granularity, chosen, bits_len):
    """The mediator's own passes under a pinned settle, for a real answer."""
    bound = get_ast_data(ast.parse(source))
    graph = build_binding_graph(bound)
    units = graph.units(granularity)
    erased = graph.nodes_for_mask(mask or ((1 << len(units)) - 1), granularity)
    bits = free_bits(graph, bound, erased)
    state = settle_for(graph, bound, erased, chosen, bits)
    tree = remove_annotations(bound.tree, erased, state.types, state.contexts,
                              False)
    tree = coerce_tree(tree, state.types, state.contexts, bound.dynamic,
                       bound.valid_pair,
                       find_inline_args(tree, bound.reverse_outflow,
                                        state.types),
                       graph, bound.constructors)
    return ast.fix_missing_locations(add_imports(tree))


def count_wraps(source, tree) -> int:
    original = {call_key(n) for n in ast.walk(ast.parse(source))
                if isinstance(n, ast.Call)}
    return sum(1 for n in ast.walk(tree)
               if isinstance(n, ast.Call) and removable_operand(n) is not None
               and call_key(n) not in original)


def search(source, mask=0, granularity="annotation", limit=200000, verbose=True):
    bound = get_ast_data(ast.parse(source))
    graph = build_binding_graph(bound)
    units = graph.units(granularity)
    erased = graph.nodes_for_mask(mask or ((1 << len(units)) - 1), granularity)

    drift = check_reference(graph, bound, erased)
    if drift is not None:
        raise SystemExit(f"pinned_settle drifted from Graph.settle: {drift}")

    bits = free_bits(graph, bound, erased)
    if verbose:
        print(f"free bits: {len(bits)} -> {2 ** len(bits)} assignments",
              file=sys.stderr)
        for bit in bits[:12]:
            print(f"   {bit.scope}.{bit.name} at line "
                  f"{getattr(bit.node, 'lineno', 0)} feeds "
                  f"{len(reads_of(graph, bit))} reads", file=sys.stderr)

    scored = []
    started = time.monotonic()
    evaluated = 0
    for size in range(len(bits) + 1):
        for combination in itertools.combinations(range(len(bits)), size):
            if evaluated >= limit:
                break
            evaluated += 1
            chosen = frozenset(combination)
            state = settle_for(graph, bound, erased, chosen, bits)
            plan, types, contexts = coercions_for(graph, bound, state)
            if not coherent(graph, bound, types, contexts):
                continue
            scored.append((len(plan), chosen))
    scored.sort(key=lambda item: item[0])
    if verbose:
        print(f"graph-coherent assignments: {len(scored)} of {evaluated} in "
              f"{time.monotonic() - started:.2f}s", file=sys.stderr)
        if scored:
            print(f"   cheapest by graph: {scored[0][0]} wraps",
                  file=sys.stderr)
    return scored, bits


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--mask", type=parse_mask, default=0)
    parser.add_argument("--granularity", choices=("annotation", "benchmark"),
                        default="annotation")
    parser.add_argument("--limit", type=int, default=200000)
    parser.add_argument("--verify", type=int, default=64)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()

    source = args.source.read_text()
    mediator = detype(source, mask=args.mask,
                      bench=args.granularity == "benchmark")
    baseline = count_wraps(source, mediator)
    scored, bits = search(source, args.mask, args.granularity, args.limit)

    for predicted, chosen in scored[:args.verify]:
        tree = emit(source, args.mask, args.granularity, chosen, len(bits))
        text = ast.unparse(tree)
        actual = count_wraps(source, tree)
        if check(text, False, 180).returncode != 0:
            continue
        print(f"VERIFIED at {actual} wraps (graph predicted {predicted}, "
              f"mediator used {baseline})", file=sys.stderr)
        if args.output:
            args.output.write_text(text + "\n")
        return
    print(f"nothing verified of {len(scored)} (mediator used {baseline})",
          file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()

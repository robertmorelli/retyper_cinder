"""Minimum coercions: binder bits chosen, everything else derived. See
boolean_search.md.

This is the two halves put together. `bool_solver.py` had per-statement tables
but decided compatibility by comparing variable *names*, which welded `v` in
four different nbody functions into one variable. `graph_solver.py` used the
graph but had no tables, so it ranked by a number that had nothing to do with
the program it emitted.

Here the only free variables are the binders - one bit each, "does this
assignment produce a typed value". Everything else follows:

  derive     `pinned_settle` propagates the bits, and what each read sees is
             read straight off the settled state. There is no same-name
             agreement rule, because a read's type comes from the binders its
             in-edges point at, and the graph already joins them when a
             variable has several assignments. Names never enter it.
  look up    each statement's table is keyed on (what its reads are, what its
             root must be). Both are now derived, so this is a lookup, not a
             search. A statement with no row for the situation the graph
             produced makes that assignment infeasible.
  legality   a bit may only claim a type the erased declaration can hold. An
             `Any` slot cannot hold a primitive - `m: Any = int64(n - 1)` is
             `type mismatch: int64 cannot be assigned to dynamic` - so for a
             machine type the typed alternative is its boxed form.

Tables are filled deliberately: for every read situation the code asks for both
a typed and an untyped root rather than recording whichever the probe happened
to produce. The holes that left were what made unification fail on rows that
were perfectly achievable.
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

from cinderx_binding import get_ast_data
from detyper import detype
from typedness_graph import TYPE, build_binding_graph
from brute_force_prune import check, mark_generated_wrappers, removable_operand
from global_search import baseline_tree
from graph_oracle import check_reference, pinned_settle
from print_instances import parse_mask, wrappable
from three_phase import (
    DYNAMIC, WrapAt, add_imports, determined_kind, find_candidates,
    probe_module, probe_slot_types, strip_module, type_name,
)
from true_minimum import author_calls

TYPED, UNTYPED = True, False

# What `box()` turns each machine type into. An erased `Any` slot cannot hold a
# primitive, so this is the only shape a "typed" claim can legally take there.
BOXED = {
    "cbool": "bool", "double": "float",
    "int8": "int", "int16": "int", "int32": "int", "int64": "int",
    "uint8": "int", "uint16": "int", "uint32": "int", "uint64": "int",
}


def lookups(bound, graph):
    """Line-and-name maps from the erased tree back to the graph's nodes.

    Spans cannot do this. Erasure rewrites `x: int64` to `x: Any`, so every
    column after the annotation shifts and no span in the erased tree matches
    the original - which made every assignment infeasible because no read could
    be located. Line numbers survive erasure; two reads of one name on one line
    share their binders and so share a typedness, which is all this is asked
    for.
    """
    reads = {}
    for node in ast.walk(bound.tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            reads.setdefault((getattr(node, "lineno", 0), node.id), node)
    binders = {}
    for (_, name), nodes in graph.bindings.items():
        for node in nodes:
            binders.setdefault((getattr(node, "lineno", 0), name), node)
    return reads, binders


def target_name(statement):
    if isinstance(statement, ast.AnnAssign) and isinstance(statement.target,
                                                           ast.Name):
        return statement.target.id
    targets = getattr(statement, "targets", None)
    if targets and isinstance(targets[0], ast.Name):
        return targets[0].id
    return None


@dataclass(frozen=True)
class Binder:
    """One assignment whose typedness is ours to choose."""
    node: ast.AST            # the binder the graph indexed, not a name
    typed_name: str          # the type a typed claim means, boxed if machine
    machine: bool            # was the underlying type a primitive
    label: str               # reporting only


def binders_for(graph, bound, erased) -> list[Binder]:
    """Binders that erasure leaves undecided, with a legal typed alternative.

    `survives_erasure` drops the ones inference recovers by itself. The rest
    claim their own type when typed - except a machine type, which an `Any`
    slot cannot hold, so the claim is the boxed form instead.
    """
    found = []
    seen = set()
    for (scope, name), nodes in graph.bindings.items():
        for node in nodes:
            if id(node) in seen:
                continue
            seen.add(id(node))
            if graph.survives_erasure(node, bound):
                continue
            # An AnnAssign is a statement, and `types` is keyed on
            # expressions, so asking it about the binder directly found nothing
            # and dropped two of scalar's three binders on the floor. The
            # binder's type is its value's.
            value = bound.types.get(node)
            if value is None:
                holder = node if isinstance(node, ast.AnnAssign) else None
                if holder is not None and holder.value is not None:
                    value = bound.types.get(holder.value)
            if value is None or value is bound.dynamic:
                continue
            spelled = type_name(value, bound.dynamic)
            if spelled == DYNAMIC:
                continue
            machine = graph.machine(value)
            typed_name = BOXED.get(spelled, spelled) if machine else spelled
            found.append(Binder(
                node=node, typed_name=typed_name, machine=machine,
                label=f"{getattr(scope, 'name', '<module>')}.{name}@"
                      f"{getattr(node, 'lineno', 0)}"))
    return sorted(found, key=lambda b: (getattr(b.node, "lineno", 0),
                                        getattr(b.node, "col_offset", 0)))


def read_nodes(statement, expression, field):
    """One representative Name load per read, keyed by name.

    The table is keyed on `candidate.reads` - unique names, sorted - so the
    derived bits have to be in that shape too. Returning every Name node gave a
    longer tuple in a different order, so no lookup ever matched and every
    assignment came back infeasible. Repeated reads of one name share their
    binders, so one representative carries the same typedness as all of them.
    """
    reach = statement if field == "value" and isinstance(
        statement, (ast.Assign, ast.AnnAssign)) else expression
    found = {}
    for node in ast.walk(reach):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            found.setdefault(node.id, node)
    return found


def derive(state, bound, nodes) -> tuple:
    """What the graph says each of these positions is, as bits."""
    return tuple(type_name(state.types.get(node), bound.dynamic) != DYNAMIC
                 for node in nodes)


def fill_table(probe_base, candidate, concrete, max_wraps):
    """Cheapest plan for every (read bits, root bit) this statement can reach.

    Both root values are asked for deliberately. Recording only what a probe
    produced left holes in combinations that were achievable, and unification
    cannot tell a hole from an impossibility.
    """
    table = {}
    for bits in itertools.product((UNTYPED, TYPED), repeat=len(candidate.reads)):
        interface = {name: (concrete.get(name, DYNAMIC) if bit else DYNAMIC)
                     for name, bit in zip(candidate.reads, bits)}
        frontier, seen = [{}], set()
        for _ in range(max_wraps + 1):
            following = []
            for plan in frontier:
                items = tuple(sorted(plan.items()))
                if items in seen:
                    continue
                seen.add(items)
                try:
                    ok, root, slots = probe_slot_types(
                        probe_module(probe_base, candidate, plan, interface))
                except Exception:
                    continue
                if ok:
                    key = (bits, root != DYNAMIC)
                    if key not in table or len(items) < table[key][0]:
                        table[key] = (len(items), dict(items))
                if len(plan) >= max_wraps:
                    continue
                options = {}
                for slot, (produced, demanded) in slots.items():
                    kind = determined_kind(produced, demanded)
                    if kind is not None:
                        options[slot] = kind
                if candidate.value_slot is not None and candidate.declared:
                    outer = determined_kind(root, candidate.declared)
                    if outer is not None:
                        options.setdefault(candidate.value_slot, outer)
                # Ask for the other root too, rather than waiting for a demand
                # to point at it: boxing the value is how a statement delivers
                # a typed root into an Any slot.
                if candidate.value_slot is not None and root != DYNAMIC:
                    options.setdefault(candidate.value_slot, "box")
                for slot, kind in options.items():
                    if slot not in plan:
                        following.append({**plan, slot: kind})
            frontier = following
            if not frontier:
                break
    return table


def evaluate(graph, bound, erased, binders, chosen, candidates, tables,
             read_map, statement_bit):
    """Cost of one bit assignment, or None if the graph makes it impossible."""
    pinned = {}
    for index in chosen:
        binder = binders[index]
        pinned[binder.node] = bound.types.get(binder.node, bound.dynamic)
    state = pinned_settle(graph, bound, erased, pinned)

    total, plans = 0, {}
    for candidate in candidates:
        nodes = []
        for name in candidate.reads:
            local = candidate.read_nodes.get(name)
            nodes.append(None if local is None else
                         read_map.get((getattr(local, "lineno", 0), name)))
        if any(node is None for node in nodes):
            return None, None
        bits = derive(state, bound, nodes)
        # The root key is what *this statement* yields, which is its own bit -
        # not the typedness of the variable, which is the join over every
        # binder of it and is what the read bits already carry. Looking the
        # root up from the joined variable asked each table for a row it had no
        # reason to have.
        index = statement_bit.get(candidate.key)
        root_bit = TYPED if index is not None and index in chosen else UNTYPED
        hit = tables.get(candidate.key, {}).get((bits, root_bit))
        if hit is None:
            return None, None
        total += hit[0]
        plans[candidate.key] = hit[1]
    return total, plans


def materialize(base, plans):
    merged = {}
    for plan in plans.values():
        merged.update(plan)
    tree = WrapAt(merged).visit(copy.deepcopy(base))
    return ast.unparse(ast.fix_missing_locations(add_imports(tree)))


def count_wraps(source, text) -> int:
    original = author_calls(source)
    return sum(1 for n in ast.walk(ast.parse(text))
               if isinstance(n, ast.Call) and removable_operand(n) is not None
               and ast.unparse(n) not in original)


def solve(source, mask=0, granularity="annotation", max_wraps=2, limit=100000,
          verbose=True):
    bound = get_ast_data(ast.parse(source))
    graph = build_binding_graph(bound)
    units = graph.units(granularity)
    erased = graph.nodes_for_mask(mask or ((1 << len(units)) - 1), granularity)

    drift = check_reference(graph, bound, erased)
    if drift is not None:
        raise SystemExit(f"pinned_settle drifted: {drift}")

    binders = binders_for(graph, bound, erased)
    read_map, binder_map = lookups(bound, graph)

    mediator = detype(source, mask=mask, bench=granularity == "benchmark")
    mark_generated_wrappers(source, mediator)
    base = baseline_tree(mediator)
    probe_base = strip_module(base)
    candidates = find_candidates(base)
    for candidate in candidates:
        object.__setattr__(candidate, "read_nodes",
                           read_nodes(candidate.statement, candidate.expression,
                                      candidate.key[2]))

    # What "typed" means for a read is the type the graph gives it when
    # everything upstream is typed - not the binder's own type recovered from a
    # label string, which was both name-keyed and the wrong side of the box.
    everything = pinned_settle(graph, bound, erased,
                               {b.node: bound.types.get(b.node, bound.dynamic)
                                for b in binders})
    concrete = {}
    for candidate in candidates:
        local = {}
        for name, node in candidate.read_nodes.items():
            source = read_map.get((getattr(node, "lineno", 0), name))
            spelled = DYNAMIC if source is None else type_name(
                everything.types.get(source), bound.dynamic)
            if spelled != DYNAMIC:
                local[name] = BOXED.get(spelled, spelled)
        concrete[candidate.key] = local

    # Which bit owns each statement's root, by line and target name.
    position = {id(b.node): i for i, b in enumerate(binders)}
    statement_bit = {}
    for candidate in candidates:
        name = target_name(candidate.statement)
        binder = binder_map.get(
            (getattr(candidate.statement, "lineno", 0), name))
        if binder is not None and id(binder) in position:
            statement_bit[candidate.key] = position[id(binder)]

    started = time.monotonic()
    tables = {c.key: fill_table(probe_base, c, concrete[c.key], max_wraps)
              for c in candidates}
    filled = time.monotonic() - started
    if verbose:
        rows = sum(len(t) for t in tables.values())
        print(f"tables: {rows} rows over {len(candidates)} statements "
              f"in {filled:.1f}s", file=sys.stderr)
        print(f"binders: {len(binders)} bits "
              f"({sum(1 for b in binders if b.machine)} machine, boxed when "
              f"typed)", file=sys.stderr)

    # A bit is only free if its statement can actually deliver both roots.
    # `i: Any = 0` yields an int however it is wrapped - there is no way to
    # make a literal dynamic - so offering it as a choice generated assignments
    # nothing could satisfy, and every toy came back infeasible on that one
    # statement alone.
    forced = {}
    for key, index in statement_bit.items():
        roots = {root for (_, root) in tables.get(key, {})}
        if roots == {TYPED}:
            forced[index] = TYPED
        elif roots == {UNTYPED}:
            forced[index] = UNTYPED
    free = [i for i in range(len(binders)) if i not in forced]
    always = frozenset(i for i, value in forced.items() if value is TYPED)
    if verbose:
        print(f"bits: {len(binders)} total, {len(forced)} forced, "
              f"{len(free)} free", file=sys.stderr)

    scored = []
    evaluated = 0
    for size in range(len(free) + 1):
        for combination in itertools.combinations(free, size):
            if evaluated >= limit:
                break
            evaluated += 1
            cost, plans = evaluate(graph, bound, erased, binders,
                                   frozenset(combination) | always, candidates,
                                   tables, read_map, statement_bit)
            if cost is not None:
                scored.append((cost, plans))
    scored.sort(key=lambda item: item[0])
    if verbose:
        print(f"feasible: {len(scored)} of {evaluated} evaluated, cheapest "
              f"{scored[0][0] if scored else 'none'}", file=sys.stderr)
    return scored, base


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--mask", type=parse_mask, default=0)
    parser.add_argument("--granularity", choices=("annotation", "benchmark"),
                        default="annotation")
    parser.add_argument("--max-wraps", type=int, default=2)
    parser.add_argument("--limit", type=int, default=100000)
    parser.add_argument("--verify", type=int, default=32)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()

    source = args.source.read_text()
    mediator = detype(source, mask=args.mask,
                      bench=args.granularity == "benchmark")
    baseline = count_wraps(source, ast.unparse(mediator))
    scored, base = solve(source, args.mask, args.granularity, args.max_wraps,
                         args.limit)
    for cost, plans in scored[:args.verify]:
        text = materialize(base, plans)
        if check(text, False, 180).returncode != 0:
            continue
        actual = count_wraps(source, text)
        print(f"VERIFIED at {actual} wraps (mediator used {baseline})",
              file=sys.stderr)
        if args.output:
            args.output.write_text(text + "\n")
        return
    print(f"nothing verified of {len(scored)} (mediator used {baseline})",
          file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()

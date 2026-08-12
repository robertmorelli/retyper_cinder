"""Minimum coercions, searched over booleans. See boolean_search.md.

Two kinds of bit and nothing else: whether each local a statement reads arrives
typed, and whether each wrappable position inside it gets a wrap. Concrete
Cinder types are needed only to emit code, so they are pushed to the edges -
the graph turns a bit into a type when a probe has to be written, and turns the
type back into a bit when the probe answers.

That is the difference from three_phase.py, which carried type names through
the interface. Doing so meant maintaining a pool per local of every type it had
ever been observed to take, deciding whether `Literal[0]` satisfies `int`, and
matching concrete types exactly during unification. The pools grew debris
(`i` acquired `bool` and `cbool`), the matching over-constrained, and the
arithmetic was multiplicative in pool size. With a bit there is nothing to
match but T against T.

  phase 1   per statement, a row per (read bits, wrap bits): does it typecheck,
            and is the root typed
  phase 2   delete rows an annotation bans
  phase 3   choose the free bits to minimise total wraps
"""
from __future__ import annotations

import argparse
import ast
import itertools
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(HERE)]

from cinderx_binding import get_ast_data
from detyper import detype
from typedness_graph import build_binding_graph
from brute_force_prune import check, mark_generated_wrappers
from global_search import baseline_tree
from print_instances import find_mismatches, parse_mask
from three_phase import (
    DYNAMIC, WrapAt, add_imports, determined_kind, find_candidates,
    probe_module, probe_slot_types, strip_module, type_name,
)

TYPED, UNTYPED = True, False


def scope_of(function) -> tuple:
    """A function's identity. Bare names are not unique across a module."""
    return (getattr(function, "name", "?"), getattr(function, "lineno", 0))


def concrete_types(bound) -> dict:
    """What each local is called when it is typed, keyed per function.

    Keying this by bare name treated `v` in `__init__`, `advance`,
    `offset_momentum` and `report_energy` as one variable - nbody shares 14 of
    its 41 local names across functions - so unification demanded all four have
    the same typedness and no assignment could satisfy that. It also handed
    probes the wrong type: `y` is a double in one function and a Body in
    another, and the first one seen won.
    """
    owner: dict[int, tuple] = {}
    for function in ast.walk(bound.tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(function):
            owner.setdefault(id(node), scope_of(function))

    names: dict[tuple, str] = {}
    for node, value in bound.types.items():
        name = (node.id if isinstance(node, ast.Name)
                else node.arg if isinstance(node, ast.arg) else None)
        if name is None:
            continue
        key = (owner.get(id(node), ("?", 0)), name)
        if key in names:
            continue
        spelled = type_name(value, bound.dynamic)
        if spelled != DYNAMIC:
            names[key] = spelled
    return names


def interface_for(candidate, bits, concrete):
    """Bits to annotations: typed reads get their name, untyped get Any.

    The parameter is still spelled with the bare name - that is what the probe
    body says - but which type it stands for is looked up per function.
    """
    scope = scope_of(candidate.function)
    return {name: (concrete.get((scope, name), DYNAMIC) if bit else DYNAMIC)
            for name, bit in zip(candidate.reads, bits)}


_WORKER = {}


def _worker_init(probe_base, candidates, concrete):
    _WORKER["base"] = probe_base
    _WORKER["candidates"] = {c.key: c for c in candidates}
    _WORKER["concrete"] = concrete


def _worker_row(task):
    """Every wrap subset worth trying for one statement under one read-bit row.

    Wrapping changes the types around it and so reveals coercions that were not
    visible before - `int64(j)` has to go in before the outer subtraction has a
    type at all - so the frontier grows a wrap at a time rather than being
    enumerated up front. Each step's coercion is still determined by the types;
    what is explored is which positions get one.
    """
    key, bits, max_wraps = task
    candidate = _WORKER["candidates"][key]
    base = _WORKER["base"]
    interface = interface_for(candidate, bits, _WORKER["concrete"])

    rows, seen, frontier = [], set(), [{}]
    for _ in range(max_wraps + 1):
        following = []
        for plan in frontier:
            items = tuple(sorted(plan.items()))
            if items in seen:
                continue
            seen.add(items)
            try:
                ok, root, slots = probe_slot_types(
                    probe_module(base, candidate, plan, interface))
            except Exception:
                continue
            rows.append((items, ok, root != DYNAMIC))
            if len(plan) >= max_wraps:
                continue
            options = {}
            for slot, (produced, demanded) in slots.items():
                kind = determined_kind(produced, demanded)
                if kind is not None:
                    options[slot] = kind
            # The whole value expression frequently does not survive binding as
            # a findable node - a primitive BinOp gets rewritten - so its demand
            # comes from the target's declaration instead. Without this the
            # wrap that sits *above* both operands is never a candidate, and
            # that is exactly the one that can subsume two below it.
            if candidate.value_slot is not None and candidate.declared:
                outer = determined_kind(root, candidate.declared)
                if outer is not None:
                    options.setdefault(candidate.value_slot, outer)
            for slot, kind in options.items():
                if slot not in plan:
                    following.append({**plan, slot: kind})
        frontier = following
        if not frontier:
            break
    return key, bits, rows


def phase_one(probe_base, candidates, concrete, max_wraps, workers, verbose):
    """A table per statement, keyed on the read bits."""
    tables: dict = {}
    tasks = [(c.key, bits, max_wraps) for c in candidates
             for bits in itertools.product((UNTYPED, TYPED),
                                           repeat=len(c.reads))]
    if verbose:
        print(f"phase 1: {len(tasks)} statement x read-bit rows",
              file=sys.stderr)
    probes = 0
    by_key = {c.key: c for c in candidates}

    def absorb(results):
        nonlocal probes
        for key, bits, rows in results:
            probes += len(rows)
            candidate = by_key[key]
            table = tables.setdefault(key, {})
            for items, ok, root_bit in rows:
                if not ok:
                    continue
                offered = [root_bit]
                # A statement whose target is declared `Any` cannot know what
                # its readers will see: narrowing depends on every *other*
                # assignment to that local, which a probe holding one statement
                # cannot observe. `i: Any = 0` narrows to int in isolation and
                # is dynamic in a function that also does `i = i + 1`. So the
                # untyped reading is offered too and phase 3 reconciles it -
                # measuring the bit here forced `i` typed and cost a wrap that
                # the mediator does not need.
                if root_bit and candidate.declared in (None, DYNAMIC):
                    offered.append(UNTYPED)
                for bit in offered:
                    slot = (bits, bit)
                    if slot not in table or len(items) < table[slot][0]:
                        table[slot] = (len(items), dict(items))

    # Spinning up a pool costs more than the work when the program is small,
    # and the toy cases are all small - so they run in process.
    if workers == 1 or len(tasks) < 64:
        _worker_init(probe_base, candidates, concrete)
        absorb(_worker_row(task) for task in tasks)
        return tables, probes

    with ProcessPoolExecutor(max_workers=workers, initializer=_worker_init,
                             initargs=(probe_base, candidates, concrete)) as pool:
        absorb(pool.map(_worker_row, tasks, chunksize=8))
    return tables, probes


def phase_two(candidates, tables, verbose):
    """Delete rows an annotation bans.

    An annotated target has to end up typed, so a row leaving it dynamic is not
    a cheaper option, it is a wrong program - one that typechecks while quietly
    dropping the type the author asked for.
    """
    dropped = 0
    for candidate in candidates:
        if not candidate.declared or candidate.declared == DYNAMIC:
            continue
        table = tables.get(candidate.key, {})
        for slot in [s for s in table if s[1] is not TYPED]:
            del table[slot]
            dropped += 1
    if verbose:
        print(f"phase 2: dropped {dropped} banned rows", file=sys.stderr)
    return dropped


def phase_three(candidates, tables, verbose, keep=32):
    """Cheapest assignment of the free bits, by branch and bound.

    A row is usable only when the typedness it assumes for a local is the
    typedness that local's own defining statement produces, so a variable's bit
    is shared between the statement that writes it and every statement that
    reads it.
    """
    factors = []
    for candidate in candidates:
        scope = scope_of(candidate.function)
        variables = [(scope, name) for name in candidate.reads]
        root_var = (scope, candidate.root_name) if candidate.root_name else None
        if root_var and root_var not in variables:
            variables.append(root_var)
        allowed: dict[tuple, tuple] = {}
        for (bits, root_bit), (cost, plan) in tables.get(candidate.key, {}).items():
            assignment = dict(zip([(scope, n) for n in candidate.reads], bits))
            if root_var:
                seen = assignment.get(root_var)
                if seen is not None and seen is not root_bit:
                    continue          # a self-assignment must agree with itself
                assignment[root_var] = root_bit
            key = tuple(assignment[name] for name in variables)
            if key not in allowed or cost < allowed[key][0]:
                allowed[key] = (cost, plan)
        factors.append((candidate.key, tuple(variables), allowed))

    names = sorted({n for _, variables, _ in factors for n in variables})
    touching: dict[str, list] = {n: [] for n in names}
    for index, (_, variables, _) in enumerate(factors):
        for name in variables:
            touching[name].append(index)
    order = sorted(names, key=lambda n: -len(touching[n]))
    position = {name: index for index, name in enumerate(order)}
    ready: dict[int, list] = {}
    for index, (_, variables, _) in enumerate(factors):
        depth = max((position[v] for v in variables), default=-1)
        ready.setdefault(depth, []).append(index)

    found: list = []
    assignment: dict[str, bool] = {}

    def descend(depth, cost, plans):
        if len(found) >= keep and cost > found[-1][0]:
            return
        if depth == len(order):
            found.append((cost, dict(assignment), dict(plans)))
            found.sort(key=lambda item: item[0])
            del found[keep:]
            return
        name = order[depth]
        for value in (UNTYPED, TYPED):
            assignment[name] = value
            extra, chosen, ok = 0, [], True
            for index in ready.get(depth, ()):
                key, variables, allowed = factors[index]
                hit = allowed.get(tuple(assignment[v] for v in variables))
                if hit is None:
                    ok = False
                    break
                extra += hit[0]
                chosen.append((key, hit[1]))
            if ok:
                for key, plan in chosen:
                    plans[key] = plan
                descend(depth + 1, cost + extra, plans)
                for key, _ in chosen:
                    plans.pop(key, None)
            del assignment[name]

    descend(0, 0, {})
    if verbose:
        print(f"phase 3: {len(order)} free bits (space {2 ** len(order)}), "
              f"{len(found)} kept, cheapest "
              f"{found[0][0] if found else 'none'}", file=sys.stderr)
    return found


def materialize(base, plans):
    merged = {}
    for plan in plans.values():
        merged.update(plan)
    tree = WrapAt(merged).visit(__import__("copy").deepcopy(base))
    return ast.unparse(ast.fix_missing_locations(add_imports(tree)))


def solve(source, mask=0, max_wraps=2, workers=None, verbose=True,
          granularity="annotation"):
    bound = get_ast_data(ast.parse(source))
    graph = build_binding_graph(bound)
    units = graph.units(granularity)
    erased = graph.nodes_for_mask(mask or ((1 << len(units)) - 1), granularity)
    predicted = graph.settle(bound, erased)
    find_mismatches(graph, bound, predicted)

    mediator = detype(source, mask=mask, bench=granularity == "benchmark")
    generated = mark_generated_wrappers(source, mediator)
    base = baseline_tree(mediator)
    probe_base = strip_module(base)
    candidates = find_candidates(base)
    concrete = concrete_types(bound)
    workers = workers or max(1, (os.cpu_count() or 2) - 1)

    started = time.monotonic()
    tables, probes = phase_one(probe_base, candidates, concrete, max_wraps,
                               workers, verbose)
    phase_two(candidates, tables, verbose)
    found = phase_three(candidates, tables, verbose)
    elapsed = time.monotonic() - started
    if verbose:
        print(f"{probes} probes, {elapsed:.2f}s (mediator used {len(generated)})",
              file=sys.stderr)
    return found, base, len(generated), probes, elapsed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--mask", type=parse_mask, default=0)
    parser.add_argument("--max-wraps", type=int, default=2)
    parser.add_argument("--granularity", choices=("annotation", "benchmark"),
                        default="annotation")
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--verify", type=int, default=32)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()

    source = args.source.read_text()
    found, base, mediator_count, probes, elapsed = solve(
        source, args.mask, args.max_wraps, args.workers,
        granularity=args.granularity)
    for cost, assignment, plans in found[:args.verify]:
        text = materialize(base, plans)
        if check(text, False, 180).returncode != 0:
            continue
        print(f"VERIFIED at {cost} wraps (mediator used {mediator_count})",
              file=sys.stderr)
        if args.output:
            args.output.write_text(text + "\n")
        return
    print(f"no proposal verified (of {len(found)})", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()

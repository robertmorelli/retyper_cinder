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
from probes import (
    DYNAMIC, WrapAt, add_imports, check_reference, determined_kind,
    find_candidates, pinned_settle, probe_module, probe_slot_types,
    strip_module, type_name,
)
from typedness_graph import build_binding_graph

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
            # A primitive cannot be stored in a slot erasure left as `Any`, and
            # boxing the whole value is the only fix. Nothing else proposes it:
            # in `j: Any = int64(nb) - int64(1)` both operands already agree, so
            # no position mismatches and no coercion is determined anywhere -
            # the statement's table came out empty and took the whole program
            # with it.
            # When the statement does not typecheck and nothing suggests a
            # repair, box the value. `j: Any = int64(nb) - int64(1)` is the
            # case: both operands agree so no position mismatches, the BinOp
            # and the int64 calls do not survive binding as findable nodes so
            # they report nothing, and the root reads back as the target's
            # declared `Any` rather than the primitive that was assigned. With
            # no option proposed the table came out empty and the whole program
            # went infeasible.
            if not ok and not options and candidate.value_slot is not None:
                options[candidate.value_slot] = "box"
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
                # What this statement delivers, and nothing else. Recording a
                # typed result as also-untyped let every statement claim either
                # root, so the solver could believe `n` was typed while its own
                # assignment made it dynamic - and emit `box(n)` on a dynamic
                # value. Reconciling the two readings is unification's job, not
                # a duplicated row's.
                slot = (bits, root_bit)
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


def bit_domains(graph, bound, erased, candidates):
    """Which variables are genuinely ours to choose.

    `classify` already sorts the erased annotations: `rebuilt` ones the remover
    puts the type back on whatever the mask did, `recovers` ones CinderX
    re-infers from their initializer, and `seeds` the ones left with nothing.
    Only the seeds are a choice. Offering the others as free bits let the
    solver pick an all-dynamic assignment for fannkuch - claiming zero wraps -
    when `count: Any = Array[int64](nb)` is a rebuilt container that narrows to
    Array[int64] no matter what, so no wrap could ever produce the program it
    was costing.
    """
    rebuilt, recovers, seeds = graph.classify(erased, bound)
    fixed, free, untyped = set(), set(), set()
    for (scope, name), binders in graph.bindings.items():
        # Keys have to be scoped exactly as phase 3 scopes them. Returning bare
        # names meant `name in fixed` never matched a `(scope, name)` variable,
        # so this reported four bits forced and forced none of them.
        key = (scope_of(scope) if scope is not None else ("<module>", 0), name)
        for binder in binders:
            if isinstance(binder, ast.arg):
                # A parameter has no assignment to wrap. Erasure leaves it
                # `Any` and nothing can make it typed, so it is not a choice -
                # `classify` calls it a seed because nothing infers it, which
                # is not the same as being free. Believing `nb` could be typed
                # is what produced `box(n)` on a dynamic value.
                untyped.add(key)
            elif binder in rebuilt or binder in recovers:
                fixed.add(key)
            elif binder in seeds:
                free.add(key)
    # A name bound both ways is not free: the fixed binder decides it.
    fixed -= untyped
    free -= fixed | untyped
    return fixed, free, untyped


def phase_three(candidates, tables, verbose, keep=4096, fixed=(), free=None,
                untyped=()):
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
            base = dict(zip([(scope, n) for n in candidate.reads], bits))
            # The variable stays free; this only filters. Claiming a variable
            # typed requires every binder of it to deliver typed, so a row that
            # delivers untyped is barred from that value. Claiming it untyped
            # bars nothing, because some other assignment may be what made it
            # dynamic. Assigning the variable here instead of filtering forced
            # all its binders to agree, which is the wrong rule and made every
            # toy with a re-assigned loop counter infeasible.
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
        # A statement constraining no variable - `while 1:`, a bare literal -
        # still costs whatever it costs. Filing it at -1 meant it was never
        # scored at any depth, so its wraps were free: fannkuch has a dozen of
        # these and the search duly reported a total of zero for a program that
        # needs nine.
        depth = max((position[v] for v in variables), default=0)
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
        if name in untyped:
            domain = (UNTYPED,)
        elif name in fixed:
            domain = (TYPED,)
        elif free is not None and name not in free:
            domain = (UNTYPED, TYPED)
        else:
            domain = (UNTYPED, TYPED)
        for value in domain:
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


def free_binders(graph, bound, erased):
    """Binders whose typedness is genuinely ours to choose.

    `classify` sorts the erased annotations: `rebuilt` the remover puts back,
    `recovers` CinderX re-infers, `seeds` are left with nothing. Only seeds are
    a choice, and a parameter is not one even though it is a seed - there is no
    assignment to wrap, so it stays dynamic whatever we do.
    """
    rebuilt, recovers, seeds = graph.classify(erased, bound)
    # A rebuilt or recovered declaration keeps its type in the emitted program:
    # anno_remover puts a checked container back whatever the mask did. The
    # settle's fixpoint can still kill a recovered one, and when it did the
    # solver derived `perm1` as dynamic, saw no demand at `perm1[r] = first`,
    # and emitted a program where that assignment is an int-into-int64 error.
    # Pinning them keeps the model and the emitted program agreeing.
    always = [node for node in (set(rebuilt) | set(recovers))]
    chooseable = []
    for (_, _), binders in graph.bindings.items():
        for binder in binders:
            if isinstance(binder, ast.arg):
                continue
            if binder in seeds and binder not in rebuilt and binder not in recovers:
                chooseable.append(binder)
    order = lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0))
    return sorted(set(chooseable), key=order), sorted(set(always), key=order)


def read_index(bound):
    """Line and name to the graph's own Name node. Spans do not survive
    erasure - it rewrites `x: int64` to `x: Any` and every column after it
    shifts - so the erased tree and the graph are matched on line."""
    found = {}
    for node in ast.walk(bound.tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            found.setdefault((getattr(node, "lineno", 0), node.id), node)
    return found


def derived_interface(candidate, state, bound, reads):
    """What the graph says this statement's reads are, under this settle.

    This is the whole point of the rewrite. The interface used to be invented -
    a parameter annotated `int64` because some bit combination said so - which
    let the solver record moves for programs that cannot exist, such as a typed
    `nb` when `nb` is a parameter erased to `Any`. Now it is read off the
    settled state, so a probe is only ever asked about a situation the graph
    actually produces.
    """
    interface = {}
    for name in candidate.reads:
        local = candidate.read_nodes.get(name)
        node = reads.get((getattr(local, "lineno", 0), name)) if local else None
        spelled = DYNAMIC if node is None else type_name(
            state.types.get(node), bound.dynamic)
        interface[name] = spelled
    return interface


def cheapest_plan(probe_base, candidate, interface, max_wraps, cache,
                  want_root=None):
    """Fewest wraps that typecheck under that interface AND deliver want_root.

    The root requirement is what makes a pin honest. Pinning a binder typed in
    the graph is an assertion until some plan actually produces a typed value
    there; without this check the solver pinned `n` typed, propagated that
    through the settle, and emitted `box(n)` on what is really a dynamic - the
    same failure the invented annotations produced, moved into the graph.
    """
    key = (candidate.key, tuple(sorted(interface.items())), want_root)
    if key in cache:
        return cache[key]
    frontier, seen, best = [{}], set(), None
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
            delivers = root != DYNAMIC
            if (ok and (want_root is None or delivers is want_root)
                    and (best is None or len(items) < best[0])):
                best = (len(items), dict(items))
            if best is not None or len(plan) >= max_wraps:
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
            if not ok and not options and candidate.value_slot is not None:
                options[candidate.value_slot] = "box"
            for slot, kind in options.items():
                if slot not in plan:
                    following.append({**plan, slot: kind})
        frontier = following
        if not frontier:
            break
    cache[key] = best
    return best


def solve(source, mask=0, granularity="annotation", max_wraps=2, limit=100000,
          verbose=True):
    bound = get_ast_data(ast.parse(source))
    graph = build_binding_graph(bound)
    units = graph.units(granularity)
    erased = graph.nodes_for_mask(mask or ((1 << len(units)) - 1), granularity)

    drift = check_reference(graph, bound, erased)
    if drift is not None:
        raise SystemExit(f"pinned_settle drifted from Graph.settle: {drift}")

    binders, always = free_binders(graph, bound, erased)
    reads = read_index(bound)

    mediator = detype(source, mask=mask, bench=granularity == "benchmark")
    mark_generated_wrappers(source, mediator)
    base = baseline_tree(mediator)
    probe_base = strip_module(base)
    candidates = find_candidates(base)
    for candidate in candidates:
        candidate.read_nodes = {}
        reach = (candidate.statement
                 if candidate.key[2] == "value"
                 and isinstance(candidate.statement, (ast.Assign, ast.AnnAssign))
                 else candidate.expression)
        for node in ast.walk(reach):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                candidate.read_nodes.setdefault(node.id, node)

    # Which binder each statement is responsible for, by line and target name.
    binder_of = {}
    by_line = {}
    for (_, name), nodes in graph.bindings.items():
        for node in nodes:
            by_line.setdefault((getattr(node, "lineno", 0), name), node)
    for candidate in candidates:
        statement = candidate.statement
        target = None
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target,
                                                               ast.Name):
            target = statement.target.id
        elif getattr(statement, "targets", None) and isinstance(
                statement.targets[0], ast.Name):
            target = statement.targets[0].id
        if target is not None:
            node = by_line.get((getattr(statement, "lineno", 0), target))
            if node in binders:
                binder_of[candidate.key] = node

    if verbose:
        print(f"free binders: {len(binders)} -> {2 ** len(binders)} assignments",
              file=sys.stderr)

    cache, scored, probes, evaluated = {}, [], 0, 0
    started = time.monotonic()
    for size in range(len(binders) + 1):
        for combination in itertools.combinations(range(len(binders)), size):
            # The cap is on assignments looked at, not solutions kept: 2^17 of
            # them at a settle each is minutes of work, and the answer is not
            # usually far into the enumeration.
            if evaluated >= limit:
                break
            evaluated += 1
            pinned = {node: bound.types.get(node, bound.dynamic)
                      for node in always}
            pinned.update({binders[i]: bound.types.get(binders[i], bound.dynamic)
                           for i in combination})
            state = pinned_settle(graph, bound, erased, pinned)
            total, plans, ok = 0, {}, True
            chosen_ids = {id(binders[i]) for i in combination}
            for candidate in candidates:
                interface = derived_interface(candidate, state, bound, reads)
                # A statement that binds a pinned variable has to deliver the
                # typedness that pin claims; one binding an unpinned variable
                # has to deliver dynamic.
                # Only the pinned direction is checked. Requiring an unpinned
                # binder to *deliver* dynamic is not satisfiable: a statement
                # probed alone narrows - `i: Any = 0` reads back as int - while
                # in the whole function, with `i` assigned repeatedly, readers
                # see dynamic. Claiming typed still has to be earned.
                want = None
                own = binder_of.get(candidate.key)
                if own is not None and id(own) in chosen_ids:
                    want = True
                before = len(cache)
                hit = cheapest_plan(probe_base, candidate, interface,
                                    max_wraps, cache, want)
                probes += len(cache) - before
                if hit is None:
                    ok = False
                    break
                total += hit[0]
                plans[candidate.key] = hit[1]
            if ok:
                scored.append((total, dict(pinned), plans))
    scored.sort(key=lambda item: item[0])
    elapsed = time.monotonic() - started
    if verbose:
        print(f"evaluated {evaluated}, feasible {len(scored)}, cheapest "
              f"{scored[0][0] if scored else 'none'}, {len(cache)} probed "
              f"interfaces, {elapsed:.1f}s", file=sys.stderr)
    return scored, base, 0, len(cache), elapsed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--mask", type=parse_mask, default=0)
    parser.add_argument("--max-wraps", type=int, default=2)
    parser.add_argument("--granularity", choices=("annotation", "benchmark"),
                        default="annotation")
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--verify", type=int, default=32)
    parser.add_argument("--limit", type=int, default=20000)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()

    source = args.source.read_text()
    found, base, mediator_count, probes, elapsed = solve(
        source, args.mask, args.granularity, args.max_wraps, args.limit)
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

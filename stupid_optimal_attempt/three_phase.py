"""Optimal coercion placement in three phases, factored on the local types.

Every search in this folder so far has walked the space of whole-program
wrapper sets, and every one of them failed the same way: the space is huge, it
is not monotone, and a wrapper can create errors as easily as it removes them.
None of them beat the mediator.

This takes the problem apart instead. The observation it rests on is that two
expressions only interact through the variables they share. Hold the typing of
the locals fixed and every expression becomes independent, so each one can be
solved exactly and on its own; what is left is choosing the typing, which is a
much smaller space than the wrapper sets were.

  phase 1   For each candidate expression, and each way its locals and its
            root could be typed, the cheapest set of wraps that typechecks.
            A table, not a search.
  phase 2   Where an annotation fixes the root type, the table rows that
            disagree are dropped rather than searched.
  phase 3   Choose one typing per local, scoring each choice by summing the
            phase-1 tables. The wrappers follow from the choice.

Phase 1 needs to typecheck an expression without the rest of the program, so it
builds a probe: the module, plus a function whose parameters are the locals the
expression reads, annotated as the candidate typing says, returning the
candidate root type, with the expression as its body. If CinderX binds that
module, the expression works under that interface. The whole module comes along
so classes, imports and callees still resolve.

What this does NOT model, and where it will be wrong before anything else is:
Static Python narrowing is flow sensitive. `local_types` is keyed by program
point, so one variable can hold different types at different lines, and the
interface here is one typing per local per function. Where a program narrows,
phase 3 will propose an assignment the binder does not honour - which the final
verification catches, but as a failure rather than as a better answer.
"""
from __future__ import annotations

import argparse
import ast
import copy
import itertools
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(HERE)]

from detyper import detype
from get_ast_data import get_ast_data
from import_adder import add_imports
from brute_force_prune import check, mark_generated_wrappers
from global_search import baseline_tree
from print_instances import parse_mask, wrappable

sys.path.insert(0, str(ROOT / "_cinderx/cinderx/PythonLib"))
from cinderx.compiler.errors import CollectingErrorSink
from cinderx.compiler.static import StaticCodeGenerator
from cinderx.compiler.static.compiler import Compiler

PROBE = "__probe__"
DYNAMIC = "dynamic"

# The casts worth trying at a position. `cast` needs a target type and is added
# per candidate type; these are the ones that stand alone.
CAST_KINDS = ("box", "int64", "cbool", "double")


# ----------------------------------------------------------------- oracle

PROBE_LINE = 100_000


def probe_binds_clean(tree: ast.AST) -> bool:
    """Does the probe function typecheck - ignoring the rest of the module?

    The probe carries the whole module with it so that classes, imports and
    callees still resolve, and that module is the *erased* one, which has
    errors of its own: nine of them on fannkuch. Judging the probe on
    `sink.errors` being empty therefore failed every row of every table. The
    probe is parked at a line number nothing else can occupy, and only errors
    reported there count against it.
    """
    sink = CollectingErrorSink()
    compiler = Compiler(StaticCodeGenerator, error_sink=sink)
    scratch = copy.deepcopy(tree)
    try:
        compiler.bind("", "", scratch, scratch, optimize=0)
    except Exception as error:
        line = getattr(error, "lineno", None)
        return line is not None and line < PROBE_LINE
    return not any(getattr(e, "lineno", 0) >= PROBE_LINE for e in sink.errors)


def probe_root_type(tree: ast.AST, dynamic_marker=None):
    """Bind the probe and report the type CinderX *inferred* for its body.

    Annotating the probe's return type and asking whether it binds was the
    first design, and it conflated two different things: `-> Any` accepts an
    `Array[int64]` happily, so every row looked satisfiable with zero wraps and
    phase 3 duly concluded that making all thirteen locals dynamic costs
    nothing. Acceptable-as-T is not the same as inferred-T. Reading the
    inferred type back answers the question the table meant to ask, and drops
    the whole root loop from phase 1 as a side effect.

    Returns (ok, type name). `ok` is False when the probe itself errored.
    """
    sink = CollectingErrorSink()
    compiler = Compiler(StaticCodeGenerator, error_sink=sink)
    scratch = copy.deepcopy(tree)
    try:
        compiler.bind("", "", scratch, scratch, optimize=0)
    except Exception as error:
        if getattr(error, "lineno", None) is None or error.lineno >= PROBE_LINE:
            return False, None
    if any(getattr(e, "lineno", 0) >= PROBE_LINE for e in sink.errors):
        return False, None

    bound_tree = compiler.ast_cache.get(scratch)
    module = compiler.modules.get("")
    if bound_tree is None or module is None:
        return False, None
    for node in ast.walk(bound_tree):
        if (isinstance(node, ast.FunctionDef) and node.name == PROBE
                and node.body and isinstance(node.body[0], ast.Return)):
            value = node.body[0].value
            return True, type_name(module.expr_types.get(value), dynamic_marker)
    return False, None


def binds_clean(tree: ast.AST) -> bool:
    sink = CollectingErrorSink()
    compiler = Compiler(StaticCodeGenerator, error_sink=sink)
    scratch = copy.deepcopy(tree)
    try:
        compiler.bind("", "", scratch, scratch, optimize=0)
    except Exception:
        return False
    return not sink.errors


def type_name(value, dynamic) -> str:
    """How to spell a bound type in an annotation, or DYNAMIC if we cannot."""
    if value is None or value is dynamic:
        return DYNAMIC
    try:
        name = value.klass.type_name.readable_name
    except Exception:
        return DYNAMIC
    # A literal type is not a thing you can write down; its base is.
    if name.startswith("Literal["):
        return "int"
    if "[" in name and not name.startswith(("Array[", "Optional[", "list[",
                                            "dict[", "tuple[")):
        return DYNAMIC
    return name


def annotation_for(name: str) -> str:
    return "Any" if name == DYNAMIC else name


def annotation_ast(name: str) -> ast.expr:
    """Parse a spelling into an annotation node.

    `Array[int64]` is a Subscript, not an identifier. Building it as
    `Name(id="Array[int64]")` made CinderX report `Name \`Array[int64]\` is not
    defined`, so every typed interface row failed and only the all-dynamic row
    ever solved.
    """
    try:
        return ast.parse(annotation_for(name), mode="eval").body
    except SyntaxError:
        return ast.Name(id="Any", ctx=ast.Load())


def local_type_pools(bound, dynamic) -> dict:
    """The types each local name could have: its own, or dynamic.

    Erasure does not offer a variable an arbitrary type - it offers to take
    the one it had away. So the pool is at most two deep per name, which is
    what keeps phase 3 finite. Drawing from every type in the program instead
    turned thirteen interface variables into 2.5 trillion assignments.
    """
    pools: dict[str, set] = {}
    for node, value in bound.types.items():
        if isinstance(node, ast.Name):
            pools.setdefault(node.id, set()).add(type_name(value, dynamic))
    for node in bound.types:
        if isinstance(node, ast.arg):
            pools.setdefault(node.arg, set()).add(
                type_name(bound.types.get(node), dynamic))
    return {name: tuple(sorted(values | {DYNAMIC}))
            for name, values in pools.items()}


def pool_for(pools, name):
    return pools.get(name, (DYNAMIC,))


# ------------------------------------------------------- candidate finding

@dataclass
class Candidate:
    """One expression we may place wraps inside, and its interface."""
    key: tuple
    function: ast.AST
    statement: ast.AST
    expression: ast.AST
    slot: tuple            # (parent, field, index) so it can be substituted
    reads: tuple           # locals the expression reads, sorted
    root_name: str | None  # the local this expression's value is assigned to
    forced_root: str | None    # set by phase 2 when an annotation fixes it
    positions: list = field(default_factory=list)


def function_locals(function: ast.AST) -> set[str]:
    """Names assigned in this function: its parameters and its targets."""
    names = {arg.arg for arg in (
        list(getattr(function.args, "posonlyargs", []))
        + list(function.args.args) + list(function.args.kwonlyargs))}
    for node in ast.walk(function):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, (ast.AnnAssign,)) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def find_candidates(tree: ast.AST) -> list[Candidate]:
    """Every statement-level expression that has somewhere to put a wrap.

    The expression slot of a statement is the unit: it is the largest thing
    whose value the rest of the function sees only through what it produces,
    which is exactly the interface phase 3 gets to choose.
    """
    candidates = []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        scope = function_locals(function)
        for statement in ast.walk(function):
            if not isinstance(statement, ast.stmt):
                continue
            # "returns" is an annotation slot, not a value slot; treating
            # it as one built probes whose body was the annotation itself.
            for field_name in ("value", "test", "iter"):
                expression = getattr(statement, field_name, None)
                if not isinstance(expression, ast.expr):
                    continue
                positions = [n for n in ast.walk(expression) if wrappable(n)]
                if not positions:
                    continue
                for slot, node in enumerate(positions):
                    node._probe_slot = (getattr(statement, "lineno", 0),
                                        field_name, slot)
                reads = tuple(sorted({
                    n.id for n in ast.walk(expression)
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                    and n.id in scope}))
                forced = None
                if isinstance(statement, ast.AnnAssign) and statement.annotation:
                    forced = ast.unparse(statement.annotation)
                # An assignment's root type IS the type the target local takes,
                # so the two are one interface variable, not two. This is the
                # join that makes phase 3 a choice over locals rather than a
                # choice over expressions that happens to mention locals.
                root_name = None
                targets = getattr(statement, "targets", None)
                if targets and isinstance(targets[0], ast.Name):
                    root_name = targets[0].id
                elif (isinstance(statement, ast.AnnAssign)
                      and isinstance(statement.target, ast.Name)):
                    root_name = statement.target.id
                candidates.append(Candidate(
                    key=(function.name, getattr(statement, "lineno", 0), field_name),
                    function=function, statement=statement, expression=expression,
                    slot=(statement, field_name, None), reads=reads,
                    root_name=root_name, forced_root=forced,
                    positions=positions))
    return candidates


# ------------------------------------------------------ wrap enumeration

class WrapAt(ast.NodeTransformer):
    """Apply a plan of casts, keyed on a tag rather than on object identity.

    The plan used to be keyed by `id(node)` of nodes in the base tree, but the
    probe wraps a *deepcopy*, whose nodes have different ids - so no wrap was
    ever applied and every table row measured the unwrapped expression.
    """

    def __init__(self, plan):
        self.plan = plan          # slot tag -> cast spelling

    def visit(self, node):
        self.generic_visit(node)
        kind = self.plan.get(getattr(node, "_probe_slot", None))
        if kind is None:
            return node
        if kind.startswith("cast:"):
            call = ast.Call(
                func=ast.Name(id="cast", ctx=ast.Load()),
                args=[ast.Name(id=kind[5:], ctx=ast.Load()), node], keywords=[])
        else:
            call = ast.Call(func=ast.Name(id=kind, ctx=ast.Load()),
                            args=[node], keywords=[])
        return ast.copy_location(call, node)


def wrap_plans(candidate: Candidate, max_wraps: int):
    """Every way to put up to `max_wraps` casts inside one expression.

    Cheapest first, so the first plan that typechecks for a row is that row's
    minimum and the rest of the enumeration for it can stop.
    """
    slots = [node._probe_slot for node in candidate.positions]
    for count in range(max_wraps + 1):
        for chosen in itertools.combinations(slots, count):
            for kinds in itertools.product(CAST_KINDS, repeat=count):
                yield dict(zip(chosen, kinds))


# --------------------------------------------------------------- phase 1

def probe_module(base: ast.AST, candidate: Candidate, plan, interface,
                 root: str | None = None) -> ast.AST:
    """The module, plus one function that states the interface explicitly.

    Parameters are the locals the expression reads, annotated as this row of
    the table says; the return annotation is the root type. Binding this asks
    exactly the question the table row is about, and nothing else.
    """
    body = WrapAt(plan).visit(copy.deepcopy(candidate.expression))
    args = [ast.arg(arg=name, annotation=annotation_ast(interface[name]))
            for name in candidate.reads]
    probe = ast.FunctionDef(
        name=PROBE,
        args=ast.arguments(posonlyargs=[], args=args, vararg=None,
                           kwonlyargs=[], kw_defaults=[], kwarg=None,
                           defaults=[]),
        body=[ast.Return(value=body)],
        decorator_list=[],
        returns=annotation_ast(root) if root is not None else None,
        type_params=[])
    module = copy.deepcopy(base)
    module.body.append(probe)
    module = ast.fix_missing_locations(add_imports(module))
    for node in ast.walk(probe):
        node.lineno = PROBE_LINE
        node.end_lineno = PROBE_LINE
    return module


def phase_one(base, candidates, pools, max_wraps, budget, verbose,
              dynamic_marker=None):
    """Cheapest wrap plan per (interface, root) row, per candidate expression.

    This is where the factoring pays: each row is a question about one
    expression, so the table is the size of one expression's options rather
    than the product across the program.
    """
    tables = {}
    probes = 0
    started = time.monotonic()
    for candidate in candidates:
        table = {}
        rows = list(itertools.product(*[pool_for(pools, name)
                                        for name in candidate.reads]))
        for row in rows:
            interface = dict(zip(candidate.reads, row))
            # One sweep over the plans fills every root this interface can
            # reach, because the root is read off the bind rather than asserted.
            for plan in wrap_plans(candidate, max_wraps):
                if probes >= budget:
                    break
                probes += 1
                ok, root = probe_root_type(
                    probe_module(base, candidate, plan, interface),
                    dynamic_marker)
                if not ok:
                    continue
                key = (row, root)
                if key not in table or len(plan) < table[key][0]:
                    table[key] = (len(plan), plan)
            if probes >= budget:
                break
        tables[candidate.key] = table
        if verbose:
            print(f"  {candidate.key[0]}:{candidate.key[1]} "
                  f"reads={len(candidate.reads)} rows={len(rows)} "
                  f"solved={len(table)} probes={probes}", file=sys.stderr)
        if probes >= budget:
            print(f"  probe budget {budget} exhausted", file=sys.stderr)
            break
    return tables, probes, time.monotonic() - started


# --------------------------------------------------------------- phase 2

def phase_two(candidates, tables):
    """Drop the rows an annotation has already decided.

    Where the source fixes a root type, a row proposing a different one is not
    a candidate to be scored against the others - it is not available at all.
    """
    dropped = 0
    for candidate in candidates:
        if candidate.forced_root is None:
            continue
        table = tables.get(candidate.key, {})
        for key in list(table):
            _, root = key
            if annotation_for(root) != candidate.forced_root:
                del table[key]
                dropped += 1
    return dropped


# --------------------------------------------------------------- phase 3

def phase_three(candidates, tables, pools, verbose):
    """Pick one typing per local, scoring by the phase-1 tables.

    Each candidate expression contributes the cheapest row consistent with the
    assignment. A local that no table can satisfy under an assignment makes
    that assignment infeasible, which is what keeps this from proposing
    something no expression can actually deliver.
    """
    names = sorted({name for candidate in candidates
                    for name in candidate.reads + ((candidate.root_name,)
                                                   if candidate.root_name else ())})
    spaces = [pool_for(pools, name) for name in names]
    size = 1
    for space in spaces:
        size *= len(space)
    if verbose:
        print(f"  interface variables: {len(names)} -> {size} assignments",
              file=sys.stderr)

    best_total, best_assignment, best_plans = None, None, None
    for combination in itertools.product(*spaces):
        assignment = dict(zip(names, combination))
        total, plans, feasible = 0, {}, True
        for candidate in candidates:
            table = tables.get(candidate.key, {})
            row = tuple(assignment[name] for name in candidate.reads)
            want = assignment.get(candidate.root_name) if candidate.root_name else None
            options = [(cost, plan, key[1])
                       for (key, (cost, plan)) in table.items()
                       if key[0] == row and (want is None or key[1] == want)]
            if not options:
                feasible = False
                break
            cost, plan, _ = min(options, key=lambda option: option[0])
            total += cost
            plans[candidate.key] = plan
        if not feasible:
            continue
        if best_total is None or total < best_total:
            best_total, best_assignment, best_plans = total, assignment, plans
    return best_total, best_assignment, best_plans, len(names)


# ------------------------------------------------------------------ main

def run(source, mask, max_wraps, budget, verbose=True):
    bound = get_ast_data(ast.parse(source))
    mediator = detype(source, mask=mask, bench=False)
    generated = mark_generated_wrappers(source, mediator)
    base = baseline_tree(mediator)

    dynamic = bound.dynamic
    # Erasure gives each local exactly one choice: the type it was inferred to
    # have, or dynamic. A pool of every type in the program instead made phase
    # 3 enumerate 2.5 trillion assignments over thirteen variables.
    pools = local_type_pools(bound, dynamic)
    if verbose:
        print(f"mediator wrappers: {len(generated)}", file=sys.stderr)
        sized = {name: pool for name, pool in pools.items() if len(pool) > 1}
        print(f"locals with a real choice: {len(sized)}", file=sys.stderr)

    candidates = find_candidates(base)
    if verbose:
        print(f"candidate expressions: {len(candidates)}", file=sys.stderr)

    print("phase 1: per-expression tables", file=sys.stderr)
    tables, probes, elapsed = phase_one(base, candidates, pools, max_wraps,
                                        budget, verbose, dynamic)
    print(f"  {probes} probes in {elapsed:.1f}s", file=sys.stderr)

    print("phase 2: annotation-forced roots", file=sys.stderr)
    dropped = phase_two(candidates, tables)
    print(f"  dropped {dropped} rows", file=sys.stderr)

    print("phase 3: choose the interface", file=sys.stderr)
    total, assignment, plans, variables = phase_three(candidates, tables,
                                                      pools, verbose)
    if total is None:
        print("  no feasible assignment", file=sys.stderr)
        return None
    print(f"  best total: {total} wraps over {variables} interface variables",
          file=sys.stderr)
    return total, assignment, plans, candidates, base, len(generated)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--mask", type=parse_mask, default=0)
    parser.add_argument("--max-wraps", type=int, default=2,
                        help="most casts to try inside one expression")
    parser.add_argument("--budget", type=int, default=20000,
                        help="probe binds allowed in phase 1")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    started = time.monotonic()
    result = run(args.source.read_text(), args.mask, args.max_wraps,
                 args.budget, not args.quiet)
    print(f"total elapsed: {time.monotonic() - started:.1f}s", file=sys.stderr)
    if result is None:
        raise SystemExit(2)
    total, assignment, plans, candidates, base, mediator_count = result
    print(f"\nphase-3 optimum: {total} wraps (mediator used {mediator_count})")
    for name, value in sorted(assignment.items()):
        print(f"  {name}: {value}")


if __name__ == "__main__":
    main()

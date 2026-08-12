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
            for field_name in ("value", "test", "iter", "returns"):
                expression = getattr(statement, field_name, None)
                if not isinstance(expression, ast.expr):
                    continue
                positions = [n for n in ast.walk(expression) if wrappable(n)]
                if not positions:
                    continue
                reads = tuple(sorted({
                    n.id for n in ast.walk(expression)
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                    and n.id in scope}))
                forced = None
                if isinstance(statement, ast.AnnAssign) and statement.annotation:
                    forced = ast.unparse(statement.annotation)
                candidates.append(Candidate(
                    key=(function.name, getattr(statement, "lineno", 0), field_name),
                    function=function, statement=statement, expression=expression,
                    slot=(statement, field_name, None), reads=reads,
                    forced_root=forced, positions=positions))
    return candidates


# ------------------------------------------------------ wrap enumeration

class WrapAt(ast.NodeTransformer):
    def __init__(self, plan):
        self.plan = plan          # id(node) -> cast spelling

    def visit(self, node):
        self.generic_visit(node)
        kind = self.plan.get(id(node))
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
    slots = [id(node) for node in candidate.positions]
    for count in range(max_wraps + 1):
        for chosen in itertools.combinations(slots, count):
            for kinds in itertools.product(CAST_KINDS, repeat=count):
                yield dict(zip(chosen, kinds))


# --------------------------------------------------------------- phase 1

def probe_module(base: ast.AST, candidate: Candidate, plan, interface,
                 root: str) -> ast.AST:
    """The module, plus one function that states the interface explicitly.

    Parameters are the locals the expression reads, annotated as this row of
    the table says; the return annotation is the root type. Binding this asks
    exactly the question the table row is about, and nothing else.
    """
    body = WrapAt(plan).visit(copy.deepcopy(candidate.expression))
    args = [ast.arg(arg=name,
                    annotation=ast.Name(id=annotation_for(interface[name]),
                                        ctx=ast.Load()))
            for name in candidate.reads]
    probe = ast.FunctionDef(
        name=PROBE,
        args=ast.arguments(posonlyargs=[], args=args, vararg=None,
                           kwonlyargs=[], kw_defaults=[], kwarg=None,
                           defaults=[]),
        body=[ast.Return(value=body)],
        decorator_list=[],
        returns=ast.Name(id=annotation_for(root), ctx=ast.Load()),
        type_params=[])
    module = copy.deepcopy(base)
    module.body.append(probe)
    return ast.fix_missing_locations(add_imports(module))


def phase_one(base, candidates, type_pool, max_wraps, budget, verbose):
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
        rows = list(itertools.product(type_pool, repeat=len(candidate.reads)))
        for row in rows:
            interface = dict(zip(candidate.reads, row))
            for root in type_pool:
                best = None
                for plan in wrap_plans(candidate, max_wraps):
                    if probes >= budget:
                        break
                    probes += 1
                    if binds_clean(probe_module(base, candidate, plan,
                                                interface, root)):
                        best = (len(plan), plan)
                        break
                if best is not None:
                    table[(row, root)] = best
            if probes >= budget:
                break
        tables[candidate.key] = table
        if verbose:
            print(f"  {candidate.key[0]}:{candidate.key[1]} "
                  f"reads={len(candidate.reads)} rows={len(rows) * len(type_pool)} "
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

def phase_three(candidates, tables, type_pool, verbose):
    """Pick one typing per local, scoring by the phase-1 tables.

    Each candidate expression contributes the cheapest row consistent with the
    assignment. A local that no table can satisfy under an assignment makes
    that assignment infeasible, which is what keeps this from proposing
    something no expression can actually deliver.
    """
    names = sorted({name for candidate in candidates for name in candidate.reads})
    if verbose:
        print(f"  interface variables: {len(names)} -> "
              f"{len(type_pool) ** len(names)} assignments", file=sys.stderr)

    best_total, best_assignment, best_plans = None, None, None
    for combination in itertools.product(type_pool, repeat=len(names)):
        assignment = dict(zip(names, combination))
        total, plans, feasible = 0, {}, True
        for candidate in candidates:
            table = tables.get(candidate.key, {})
            row = tuple(assignment[name] for name in candidate.reads)
            options = [(cost, plan, root) for (key, (cost, plan)) in table.items()
                       if key[0] == row for root in (key[1],)]
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
    observed = {type_name(value, dynamic) for value in bound.types.values()}
    type_pool = tuple(sorted(observed | {DYNAMIC}))
    if verbose:
        print(f"mediator wrappers: {len(generated)}", file=sys.stderr)
        print(f"type pool: {type_pool}", file=sys.stderr)

    candidates = find_candidates(base)
    if verbose:
        print(f"candidate expressions: {len(candidates)}", file=sys.stderr)

    print("phase 1: per-expression tables", file=sys.stderr)
    tables, probes, elapsed = phase_one(base, candidates, type_pool, max_wraps,
                                        budget, verbose)
    print(f"  {probes} probes in {elapsed:.1f}s", file=sys.stderr)

    print("phase 2: annotation-forced roots", file=sys.stderr)
    dropped = phase_two(candidates, tables)
    print(f"  dropped {dropped} rows", file=sys.stderr)

    print("phase 3: choose the interface", file=sys.stderr)
    total, assignment, plans, variables = phase_three(candidates, tables,
                                                      type_pool, verbose)
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

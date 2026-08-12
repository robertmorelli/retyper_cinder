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

STATUS: phases 1 and 2 work. Phase 3 does not, and the reason is structural
rather than a bug.

Phase 1 is sound and fast - on fannkuch it reaches a fixpoint in two rounds and
6325 probes, about 35 seconds, and every interface row resolves. Phase 2 is a
few lines. Phase 3 finds no feasible assignment, and a per-candidate audit
shows why it is not a missing row: every candidate on its own has rows, and
roots compatible with its target's pool. It is the *conjunction* that has no
solution.

That is the flow sensitivity, and it is fatal to this interface rather than
awkward for it. Static Python types are per program point: fannkuch assigns `i`
thirteen times, so one definition wants `Literal[0]`, a use downstream wants
`int64`, and there is no single type for `i` that satisfies both. Widening
literals to `int` only moves the conflict. One type per local cannot express
the program, so no amount of repair to phase 3 will make this version work.

The fix is to key the interface on the definition site rather than the name -
SSA, in effect. fannkuch has 13 locals but 41 definition sites, so phase 3 can
no longer enumerate its assignment space and needs variable elimination over
the factor graph instead: each candidate expression constrains only the names
it reads plus the one it defines, which should keep the treewidth small, though
that is unmeasured.
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
from cinderx_binding import get_ast_data
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
    # `Literal[0]` is writable after all - Static Python binds it fine - and
    # widening it to `int` was not a harmless simplification. The table then
    # promised that `i: int` typechecks while the program actually produced
    # `Literal[0]`, and fannkuch's verification died on exactly that:
    # `cannot add Literal[0] and Literal[1]`.
    if name.startswith("Literal["):
        return name
    if "[" in name and not name.startswith(("Array[", "Optional[", "list[",
                                            "dict[", "tuple[")):
        return DYNAMIC
    return name


def annotation_for(name: str) -> str:
    return "Any" if name == DYNAMIC else name


def annotation_ast(name: str) -> ast.expr:
    """Parse a spelling into an annotation node.

    `Array[int64]` is a Subscript, not an identifier. Building it as
    `Name(id="Array[int64]")` made CinderX report an undefined name, so every typed interface row failed and only the all-dynamic row
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


def compatible(root: str, want: str) -> bool:
    """Can an expression inferred as `root` supply a local declared `want`?

    Equality was the first rule and it made every assignment infeasible once
    literal types stopped being widened: a variable assigned `0` here and `1`
    there has two different inferred roots, and no single choice equals both.
    A literal is an int, so this is the least widening that lets those two
    definitions agree without going all the way back to discarding literals.
    """
    if root == want:
        return True
    if want == "int" and root.startswith("Literal["):
        return True
    return False


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


def strip_module(base: ast.AST) -> ast.AST:
    """The module reduced to what a probe needs: declarations, no bodies.

    A probe only needs classes, imports, globals and callee *signatures* to
    resolve; the bodies contribute nothing but bind time and errors of their
    own. Replacing each body with a raise keeps every signature legal whatever
    its return type, and cuts the per-probe bind to a fraction of the whole
    module - which is what makes a fixpoint over several phase-1 passes
    affordable at all.
    """
    module = copy.deepcopy(base)

    def gut(node):
        for child in ast.walk(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                child.body = [ast.Raise(
                    exc=ast.Call(func=ast.Name(id="NotImplementedError",
                                               ctx=ast.Load()),
                                 args=[], keywords=[]), cause=None)]

    kept = []
    for statement in module.body:
        if isinstance(statement, ast.If):
            # `if __name__ == "__main__":` is a body, not a declaration.
            continue
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            gut(statement)
        elif isinstance(statement, ast.ClassDef):
            for member in statement.body:
                gut(member)
        kept.append(statement)
    module.body = kept
    return ast.fix_missing_locations(module)


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


def wrap_plans_at(candidate: Candidate, count: int):
    """Every way to place exactly `count` casts inside one expression."""
    slots = [node._probe_slot for node in candidate.positions]
    for chosen in itertools.combinations(slots, count):
        for kinds in itertools.product(CAST_KINDS, repeat=count):
            yield dict(zip(chosen, kinds))


def wrap_plans(candidate: Candidate, max_wraps: int):
    slots = [node._probe_slot for node in candidate.positions]
    for count in range(max_wraps + 1):
        for chosen in itertools.combinations(slots, count):
            for kinds in itertools.product(CAST_KINDS, repeat=count):
                yield dict(zip(chosen, kinds))


# --------------------------------------------------------------- phase 1

def _probe_import_point(module: ast.AST) -> int:
    """After `import __static__`, which must precede every other import."""
    index = 0
    for position, statement in enumerate(module.body):
        if isinstance(statement, ast.Import) and any(
                a.name == "__static__" for a in statement.names):
            index = position + 1
        elif (isinstance(statement, ast.ImportFrom)
              and statement.module == "__future__"):
            index = max(index, position + 1)
    return index


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
    # add_imports only knows to emit `Any`. Injecting `Literal` here is enough:
    # its pruner keeps whatever typing name the tree reads, and an annotation
    # spelled `Literal[0]` reads it.
    module.body.insert(_probe_import_point(module),
                       ast.ImportFrom(module="typing",
                                      names=[ast.alias(name="Literal"),
                                             ast.alias(name="Any")], level=0))
    module = ast.fix_missing_locations(add_imports(module))
    for node in ast.walk(probe):
        node.lineno = PROBE_LINE
        node.end_lineno = PROBE_LINE
    return module


def phase_one(probe_base, candidates, pools, max_wraps, budget, verbose,
              dynamic_marker=None, cache=None, tables=None):
    """Cheapest wrap plan per (interface, inferred root), per expression.

    Costs are kept down three ways, all of which matter once this is run
    repeatedly to a fixpoint: the module is stripped to declarations, wrap
    depth escalates only when the shallower depth found nothing, and every
    probe is cached on (expression, interface, plan) so a later pass pays only
    for the interface rows the pools have newly opened.
    """
    cache = {} if cache is None else cache
    tables = {} if tables is None else tables
    probes = 0
    started = time.monotonic()
    for candidate in candidates:
        table = tables.setdefault(candidate.key, {})
        rows = list(itertools.product(*[pool_for(pools, name)
                                        for name in candidate.reads]))
        fresh = 0
        for row in rows:
            interface = dict(zip(candidate.reads, row))
            found_any = False
            for count in range(max_wraps + 1):
                if found_any:
                    # A shallower plan already worked for this interface; a
                    # deeper one cannot be cheaper, and enumerating it was most
                    # of the old runtime.
                    break
                for plan in wrap_plans_at(candidate, count):
                    if probes >= budget:
                        break
                    signature = (candidate.key, row,
                                 tuple(sorted(plan.items())))
                    if signature in cache:
                        ok, root = cache[signature]
                    else:
                        probes += 1
                        fresh += 1
                        ok, root = probe_root_type(
                            probe_module(probe_base, candidate, plan, interface),
                            dynamic_marker)
                        cache[signature] = (ok, root)
                    if not ok:
                        continue
                    found_any = True
                    key = (row, root)
                    if key not in table or count < table[key][0]:
                        table[key] = (count, plan)
                if probes >= budget:
                    break
            if probes >= budget:
                break
        if verbose:
            print(f"  {candidate.key[0]}:{candidate.key[1]} "
                  f"rows={len(rows)} solved={len(table)} new_probes={fresh}",
                  file=sys.stderr)
        if probes >= budget:
            print(f"  probe budget {budget} exhausted", file=sys.stderr)
            break
    return tables, probes, time.monotonic() - started


def observed_roots(candidates, tables):
    """The root types phase 1 actually saw, per assigned local.

    The seed pools come from the *original* program, but erased code infers
    types that program never had - `object` above all - so an assignment
    mentioning one could never match a table row. Feeding what was observed
    back into the pools is what closes that gap.
    """
    found: dict[str, set] = {}
    for candidate in candidates:
        if not candidate.root_name:
            continue
        for (_, root) in tables.get(candidate.key, {}):
            found.setdefault(candidate.root_name, set()).add(root)
    return found


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
        # `x: Any = Array[int64](nb)` fixes nothing - Any accepts every root.
        # Treating it as a constraint that forces the root to be dynamic
        # emptied the tables of all twelve annotated assignments in fannkuch,
        # and phase 3 then had nothing to choose from.
        if candidate.forced_root in ("Any", "object"):
            continue
        table = tables.get(candidate.key, {})
        for key in list(table):
            _, root = key
            if annotation_for(root) != candidate.forced_root:
                del table[key]
                dropped += 1
    return dropped


# --------------------------------------------------------------- phase 3

def phase_three(candidates, tables, pools, verbose, limit=200000):
    """Every interface assignment that the tables admit, cheapest first.

    This yields rather than returns. The interface is one type per local, but
    Static Python types are per program point - fannkuch assigns `i` thirteen
    times - so an assignment the tables call consistent can still be one the
    binder will not produce. Ranking by cost and letting CinderX reject them in
    order keeps the model as a generator, which it is good at, and stops it
    being the judge, which it is not.
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

    scored = []
    for count, combination in enumerate(itertools.product(*spaces)):
        if count >= limit:
            break
        assignment = dict(zip(names, combination))
        total, plans, feasible = 0, {}, True
        for candidate in candidates:
            table = tables.get(candidate.key, {})
            row = tuple(assignment[name] for name in candidate.reads)
            want = assignment.get(candidate.root_name) if candidate.root_name else None
            options = [(cost, plan) for (key, (cost, plan)) in table.items()
                       if key[0] == row
                       and (want is None or compatible(key[1], want))]
            if not options:
                feasible = False
                break
            cost, plan = min(options, key=lambda option: option[0])
            total += cost
            plans[candidate.key] = plan
        if feasible:
            scored.append((total, assignment, plans))
    scored.sort(key=lambda item: item[0])
    if verbose:
        print(f"  feasible assignments: {len(scored)}", file=sys.stderr)
    return scored, len(names)


def materialize(base: ast.AST, plans) -> str:
    """Apply every chosen plan to the real tree and print it.

    Phase 1 judged each expression inside a probe, with its interface asserted
    by parameter annotations. The real program has to *earn* those types from
    the assignments around it, so this is where the factoring is actually
    tested rather than assumed.
    """
    merged = {}
    for plan in plans.values():
        merged.update(plan)
    tree = WrapAt(merged).visit(copy.deepcopy(base))
    return ast.unparse(ast.fix_missing_locations(add_imports(tree)))


# ------------------------------------------------------------------ main

def run(source, mask, max_wraps, budget, rounds, verbose=True):
    bound = get_ast_data(ast.parse(source))
    mediator = detype(source, mask=mask, bench=False)
    generated = mark_generated_wrappers(source, mediator)
    base = baseline_tree(mediator)
    probe_base = strip_module(base)

    dynamic = bound.dynamic
    pools = local_type_pools(bound, dynamic)
    candidates = find_candidates(base)
    if verbose:
        print(f"mediator wrappers: {len(generated)}", file=sys.stderr)
        print(f"candidate expressions: {len(candidates)}", file=sys.stderr)

    cache, tables = {}, {}
    total_probes = 0
    for round_number in range(1, rounds + 1):
        print(f"phase 1, round {round_number}", file=sys.stderr)
        tables, probes, elapsed = phase_one(
            probe_base, candidates, pools, max_wraps, budget, False,
            dynamic, cache, tables)
        total_probes += probes
        solved = sum(len(t) for t in tables.values())
        print(f"  {probes} new probes ({total_probes} total) in {elapsed:.1f}s, "
              f"{solved} table rows", file=sys.stderr)

        # Feed the observed roots back into the pools and go round again; the
        # cache means the next pass only pays for genuinely new interface rows.
        found = observed_roots(candidates, tables)
        grown = dict(pools)
        changed = 0
        for name, roots in found.items():
            before = set(pool_for(pools, name))
            after = before | roots
            if after != before:
                grown[name] = tuple(sorted(after))
                changed += 1
        print(f"  pools grown for {changed} locals", file=sys.stderr)
        if not changed:
            print("  fixpoint reached", file=sys.stderr)
            break
        pools = grown

    print("phase 2: annotation-forced roots", file=sys.stderr)
    dropped = phase_two(candidates, tables)
    print(f"  dropped {dropped} rows", file=sys.stderr)

    print("phase 3: choose the interface", file=sys.stderr)
    scored, variables = phase_three(candidates, tables, pools, verbose)
    if not scored:
        print("  no feasible assignment", file=sys.stderr)
        return None
    print(f"  cheapest proposal: {scored[0][0]} wraps over {variables} "
          f"interface variables", file=sys.stderr)
    return scored, candidates, base, len(generated)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--mask", type=parse_mask, default=0)
    parser.add_argument("--max-wraps", type=int, default=2,
                        help="most casts to try inside one expression")
    parser.add_argument("--budget", type=int, default=20000,
                        help="probe binds allowed in phase 1")
    parser.add_argument("--rounds", type=int, default=6,
                        help="phase-1 passes allowed while pools grow")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--verify", type=int, default=400,
                        help="proposals to check, cheapest first")
    args = parser.parse_args()

    started = time.monotonic()
    result = run(args.source.read_text(), args.mask, args.max_wraps,
                 args.budget, args.rounds, not args.quiet)
    print(f"total elapsed: {time.monotonic() - started:.1f}s", file=sys.stderr)
    if result is None:
        raise SystemExit(2)
    scored, candidates, base, mediator_count = result

    # The cheap bind filters; the loader confirms. Proposals are already in
    # cost order, so the first that survives both is the cheapest realisable
    # one the tables can express.
    print(f"\nverifying proposals in cost order "
          f"(mediator used {mediator_count})", file=sys.stderr)
    tried = 0
    for total, assignment, plans in scored[:args.verify]:
        tried += 1
        source_out = materialize(base, plans)
        if not binds_clean(ast.parse(source_out)):
            continue
        verdict = check(source_out, False, 180)
        if verdict.returncode != 0:
            continue
        print(f"\nVERIFIED: {total} wraps after {tried} proposals "
              f"(mediator used {mediator_count})")
        for name, value in sorted(assignment.items()):
            print(f"  {name}: {value}")
        if args.output:
            args.output.write_text(source_out + "\n")
            print(f"wrote {args.output}")
        return
    print(f"\nno proposal verified (tried {tried} of {len(scored)})")


if __name__ == "__main__":
    main()

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

STATUS: works on small programs; over-constrained on fannkuch.

All eight cases in toy_cases.py verify *behaviourally* - the proposal compiles
and computes what the mediator computes - and two of them beat the mediator
(array_read 2 against 3, index_wrap 2 against 3). On fannkuch an earlier
revision produced a correct 9-wrapper program against the mediator's 10.

The current revision finds only two solutions on fannkuch, both costing 9, and
neither assembles. Brute-forcing the whole 622080-assignment space confirms the
branch-and-bound is right and the model itself admits only those two, so this
is a modelling fault rather than a search fault. It is this:

    ('fannkuch', 25, 'value')  `i: Any = 0`   allowed={('int',): 0}

The probe replants that statement on its own and reads `i` back, so `i` is
still narrowed to int. In the real function `i` is assigned several times,
CinderX joins those definitions, and every read of `i` is dynamic. Narrowing is
a property of *all* of a variable's definitions; a probe holding one statement
measures the narrowing that would apply if it were the only one.

That is the decomposition's real cost, and it is not the flow-sensitivity story
an earlier version of this file told: one type per local is still enough to
express the answer. What is not enough is one *statement* to determine that
type.

Two ways out, neither tried yet. Fix the declared type instead of solving for
it - after erasure every local's declaration is already determined (Any, or a
container the remover rebuilt), so the only real freedom is wrap placement,
which would collapse phase 3 almost entirely. Or probe a variable's definitions
together rather than singly, which keeps the freedom and gives up some of the
decomposition.

Also outstanding: twelve candidates have no interface variables at all
(`while 1:`, bare `1`), so build_factors neither checks them nor counts their
cost. Their wraps can never be selected.
"""
from __future__ import annotations

import argparse
import ast
import copy
import itertools
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
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

# The casts worth trying at a position. The one-argument conversions stand
# alone; `cast` needs a target type, so those forms are generated per program
# from the types its own locals actually take.
CAST_KINDS = ("box", "unbox", "cbool", "double",
              "int8", "int16", "int32", "int64",
              "uint8", "uint16", "uint32", "uint64")


NARROW_KINDS = ("box", "int64", "cbool", "double")

PRIMITIVE_NAMES = frozenset({
    "cbool", "double",
    "int8", "int16", "int32", "int64",
    "uint8", "uint16", "uint32", "uint64",
})


def determined_kind(produced: str, demanded: str) -> str | None:
    """The one coercion that takes `produced` to `demanded`, if any.

    A wrap is not a choice among twelve conversions - between two
    representations there is exactly one, which is what patch_picker._choose
    computes for the mediator. Enumerating kinds meant eleven of every twelve
    probes existed only to prove that `uint8` on an `Array[int64]` does not
    bind. With the kind determined, a position carries one bit: wrap or not.
    """
    if produced is None or demanded is None or produced == demanded:
        return None
    produced_prim = produced in PRIMITIVE_NAMES
    demanded_prim = demanded in PRIMITIVE_NAMES
    if produced_prim and not demanded_prim:
        return "box"
    if demanded_prim and not produced_prim:
        return demanded          # the constructor is named for its target
    return None


def cast_kinds_for(pools, narrow=False) -> tuple:
    """The conversions worth trying: representation changes only.

    `cast(T, x)` is deliberately absent. It changes no representation - it
    asserts a static type and checks it at runtime - so a generator that emits
    it anywhere emits lies. Including it did not find a cheaper program, it
    found a way to defeat narrowing: `count: Any = cast(bool, Array[int64](nb))`
    typechecks, needs no wraps anywhere downstream because the int64 demand is
    gone, and dies with `TypeError: expected bool, got staticarray` the moment
    it runs. The mediator emits cast only where the value really is a T, which
    is knowledge this generator does not have.
    """
    return CAST_KINDS


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


def probe_slot_types(tree: ast.AST):
    """Bind once; report the root plus every position's produced and demanded.

    Both halves come from the same bind - `expr_types` and `expr_ctx_types` -
    so one probe determines what wrap each position would need, instead of a
    dozen probes discovering it by trial.
    """
    sink = CollectingErrorSink()
    compiler = Compiler(StaticCodeGenerator, error_sink=sink)
    scratch = copy.deepcopy(tree)
    raised = None
    try:
        compiler.bind("", "", scratch, scratch, optimize=0)
    except Exception as error:
        raised = error

    # Read the types even when the bind reported errors. The unwrapped
    # statement fails precisely when a wrap is needed, so bailing out here left
    # every such position with no determined kind - and wave two never tried
    # the one repair that mattered. The binder still fills expr_types.
    line = getattr(raised, "lineno", None) if raised is not None else None
    ok = (raised is None or (line is not None and line < PROBE_LINE)) and not any(
        getattr(e, "lineno", 0) >= PROBE_LINE for e in sink.errors)

    bound_tree = compiler.ast_cache.get(scratch)
    module = compiler.modules.get("")
    if bound_tree is None or module is None:
        return False, None, {}
    dynamic_marker = compiler.type_env.DYNAMIC
    slots = {}
    root = DYNAMIC
    for node in ast.walk(bound_tree):
        slot = getattr(node, "_probe_slot", None)
        if slot is not None:
            slots[slot] = (
                type_name(module.expr_types.get(node), dynamic_marker),
                type_name(module.expr_ctx_types.get(node), dynamic_marker))
        if (isinstance(node, ast.FunctionDef) and node.name == PROBE
                and node.body):
            value = getattr(node.body[-1], "value", None)
            if value is not None:
                root = type_name(module.expr_types.get(value), dynamic_marker)
    return ok, root, slots


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

    # Each Compiler builds its own DYNAMIC, and its readable_name is plain
    # "object", so a marker borrowed from another compiler matches nothing:
    # every dynamic root came back spelled "object" while the pools spelled it
    # "dynamic", and the five expressions reading `nb` could never be given a
    # row for the type it actually has. Ask this compiler for its own.
    dynamic_marker = compiler.type_env.DYNAMIC
    bound_tree = compiler.ast_cache.get(scratch)
    module = compiler.modules.get("")
    if bound_tree is None or module is None:
        return False, None
    for node in ast.walk(bound_tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == PROBE
                and node.body):
            continue
        # The read-back, when there is one, is the answer; otherwise the
        # statement's own value.
        last = node.body[-1]
        value = getattr(last, "value", None)
        if value is None:
            return True, DYNAMIC
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
    """Each local gets one bit: the concrete type it infers, or dynamic.

    Erasure does not offer a variable a type from a catalogue, it offers to
    take away the one it had, so the alternatives are exactly two. Treating
    this as a free pick over every type ever observed gave `i` the pool
    ('bool', 'cbool', 'dynamic', 'int', 'int64') - `bool` and `cbool` are not
    alternatives for a loop counter, they are debris - and since pools
    multiply, that debris inflated the row count of every statement reading i.
    """
    concrete: dict[str, str] = {}
    for node, value in bound.types.items():
        name = None
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.arg):
            name = node.arg
        if name is None or name in concrete:
            continue
        spelled = type_name(value, dynamic)
        if spelled != DYNAMIC:
            concrete[name] = spelled
    return {name: (value, DYNAMIC) for name, value in concrete.items()}


def revise_pools(pools, candidates, tables):
    """Replace each local's concrete candidate; never accumulate a catalogue.

    Which concrete type a variable settles on depends on the bits its
    neighbours took, so the fixpoint still has to run - but it revises one
    candidate rather than growing a pool, which is what keeps every domain at
    size two however many rounds it takes.
    """
    grown = dict(pools)
    changed = 0
    for candidate in candidates:
        if not candidate.root_name:
            continue
        roots = {root for (_, root) in tables.get(candidate.key, {})
                 if root != DYNAMIC}
        if not roots:
            continue
        current = pool_for(pools, candidate.root_name)
        concrete = sorted(roots)[0]
        if concrete not in current:
            grown[candidate.root_name] = (concrete, DYNAMIC)
            changed += 1
    return grown, changed


def compatible(root: str, want: str) -> bool:
    """Does a definition yielding `root` give readers exactly `want`?

    Equality, because `root` is the type read back *after* the statement - what
    readers genuinely see, narrowing included - not the type of the value
    assigned. Anything looser has been tried and is unsound in one direction or
    the other.
    """
    return root == want


def pool_for(pools, name):
    return pools.get(name, (DYNAMIC,))


def plans_from_kinds(kinds_at: dict, max_wraps: int):
    """Subsets of the positions that have a determined wrap, cheapest first.

    All subsets would be 2^n, which is fine at five positions and worse than
    what it replaces at eleven, so the depth cap stays. The saving is that the
    kind is no longer a dimension: a position is in the subset or it is not.
    """
    slots = sorted(kinds_at)
    for count in range(max_wraps + 1):
        for chosen in itertools.combinations(slots, count):
            yield {slot: kinds_at[slot] for slot in chosen}


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
    value_slot: tuple | None = None   # slot tag of the whole value expression
    declared: str | None = None       # what the target's annotation demands
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
                # Positions must cover everything the probe replants, or the
                # search space excludes repairs that are available in reality:
                # the index in `count[i]` lives in the target, so with only the
                # value walked, `count[int64(i)]` could never be proposed.
                span = (statement
                        if field_name == "value"
                        and isinstance(statement, (ast.Assign, ast.AnnAssign))
                        else expression)
                # An annotation is a type, not a value: `i: Any = ...` offered
                # `Any` itself as somewhere to put a cast.
                annotated = set()
                if isinstance(statement, ast.AnnAssign) and statement.annotation:
                    annotated = {id(n) for n in ast.walk(statement.annotation)}
                positions = [n for n in ast.walk(span)
                             if wrappable(n) and id(n) not in annotated]
                if not positions:
                    continue
                for slot, node in enumerate(positions):
                    node._probe_slot = (getattr(statement, "lineno", 0),
                                        field_name, slot)
                if not hasattr(expression, "_probe_slot"):
                    expression._probe_slot = (getattr(statement, "lineno", 0),
                                              field_name, -1)
                # Reads have to cover whatever the probe will contain. An
                # assignment is replanted whole, so `count[i] = i + 1` reads
                # `count` as well - it appears only in the target, and leaving
                # it out gave that statement an empty table and made every
                # array store infeasible. A test or an iterable is replanted
                # alone, so for those the expression is the right scope.
                reads = tuple(sorted({
                    n.id for n in ast.walk(span)
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
                declared = None
                if isinstance(statement, ast.AnnAssign) and statement.annotation:
                    spelled = ast.unparse(statement.annotation)
                    declared = DYNAMIC if spelled in ("Any", "object") else spelled
                candidates.append(Candidate(
                    key=(function.name, getattr(statement, "lineno", 0), field_name),
                    function=function, statement=statement, expression=expression,
                    slot=(statement, field_name, None), reads=reads,
                    root_name=root_name, forced_root=forced,
                    positions=positions,
                    value_slot=getattr(expression, "_probe_slot", None),
                    declared=declared))
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


def wrap_plans_at(candidate: Candidate, count: int, kinds, viable=None):
    """Every way to place exactly `count` casts inside one statement.

    At depth two `viable` restricts the (position, kind) pairs to those that
    survived on their own at depth one. A pair that errors alone is a poor bet
    in combination, and without the restriction a twelve-kind vocabulary makes
    depth two cost hundreds of times depth one.
    """
    slots = [node._probe_slot for node in candidate.positions]
    if count <= 1 or viable is None:
        for chosen in itertools.combinations(slots, count):
            for combo in itertools.product(kinds, repeat=count):
                yield dict(zip(chosen, combo))
        return
    for chosen in itertools.combinations(slots, count):
        options = [[k for k in kinds if (slot, k) in viable] for slot in chosen]
        for combo in itertools.product(*options):
            yield dict(zip(chosen, combo))


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
    # The probe is the whole statement, not the expression on its own. An
    # expression handed back from a function is under no demand at all, so
    # `count[i] = i + 1` typechecked bare and the table never learned that the
    # container asks for int64 - which is exactly the wrap the mediator puts
    # there. Rebuilding the statement puts the expression back under whatever
    # its context actually demands.
    statement = WrapAt(plan).visit(copy.deepcopy(candidate.statement))
    body = [statement]
    if isinstance(statement, (ast.AnnAssign, ast.Assign)):
        # Keep the annotation exactly as the erased program has it. Rewriting
        # `k: Any = perm[0]` into `k = perm[0]` to make the root easier to read
        # back also deleted the constraint that an int64 cannot be stored in an
        # Any slot, so that assignment looked free and the box() the mediator
        # puts there was proposed one statement too late.
        pass
    elif isinstance(statement, ast.Return):
        # A returned value is under the function's return type. Evaluating it
        # as a bare expression put it under nothing, so `return k` with k an
        # int64 and the function returning Any looked free - and the box() the
        # mediator needs there never appeared in the table.
        body = [ast.Return(value=WrapAt(plan).visit(
            copy.deepcopy(candidate.expression)))]
    elif isinstance(statement, (ast.While, ast.If)):
        # A test is under a condition's demand, which is where cbool is asked
        # for. `if <expr>: pass` is the smallest thing that reproduces it.
        body = [ast.If(test=WrapAt(plan).visit(
            copy.deepcopy(candidate.expression)),
            body=[ast.Pass()], orelse=[])]
    else:
        body = [ast.Expr(value=WrapAt(plan).visit(
            copy.deepcopy(candidate.expression)))]

    # Every read is a parameter, including one the statement also assigns.
    # Excluding the target broke exactly the self-referential case - `i = i + 1`
    # probed with `i` undefined, so its table came back empty and every loop in
    # every program was infeasible.
    # After the statement, read the target back. Its inferred type there is
    # what every later reader of this local actually sees, which is not the
    # same as the type of the value assigned: `i: Any = 0` leaves readers with
    # dynamic, while `perm: Any = Array[int64](nb)` narrows readers to
    # Array[int64]. Guessing that from the value's type got `perm` treated as
    # dynamic, so `perm[i] = i` looked free when it really demands int64.
    # CinderX does the narrowing, so the probe asks it rather than modelling it.
    if candidate.root_name and isinstance(statement, (ast.Assign, ast.AnnAssign)):
        body = body + [ast.Expr(value=ast.Name(id=candidate.root_name,
                                               ctx=ast.Load()))]

    args = [ast.arg(arg=name, annotation=annotation_ast(interface[name]))
            for name in candidate.reads]
    probe = ast.FunctionDef(
        name=PROBE,
        args=ast.arguments(posonlyargs=[], args=args, vararg=None,
                           kwonlyargs=[], kw_defaults=[], kwarg=None,
                           defaults=[]),
        body=body,
        decorator_list=[],
        # Carry the real return annotation whenever the probe returns, so the
        # demand on a returned value is the one the program actually makes.
        returns=(copy.deepcopy(candidate.function.returns)
                 if isinstance(candidate.statement, ast.Return) else None),
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


_WORKER = {}


def _worker_init(probe_base, candidates):
    """Each process keeps its own copy of the stripped module and candidates.

    A probe is a pure function of (statement, plan, interface) and costs a
    CinderX bind, so the only thing standing between this and a linear speedup
    is that the tree has to exist in the worker. It is sent once, not per task.
    """
    _WORKER["base"] = probe_base
    _WORKER["candidates"] = {c.key: c for c in candidates}


def _kinds_from(slots):
    kinds = {}
    for slot, (produced, demanded) in slots.items():
        kind = determined_kind(produced, demanded)
        if kind is not None:
            kinds[slot] = kind
    return kinds


def _worker_repair(task):
    """Bind, wrap what the types say needs wrapping, bind again.

    One analysis pass is not enough: applying a wrap changes the types around
    it and reveals the next one. `i: Any = j - int64(1)` needs `int64(j)`
    before the outer subtraction has a type at all, so with a single pass the
    outer `box` was never even a candidate and the statement's table came out
    empty. Each step's coercions are still determined rather than searched -
    what is explored is which subset of them to take.
    """
    key, row, interface_items, max_wraps = task
    candidate = _WORKER["candidates"][key]
    base = _WORKER["base"]
    interface = dict(interface_items)
    results = []
    seen = set()
    frontier = [{}]
    for _ in range(max_wraps + 1):
        nxt = []
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
            results.append((items, ok, root))
            if len(plan) >= max_wraps:
                continue
            options = _kinds_from(slots)
            # The whole value expression often does not survive binding as a
            # node we can find - a primitive BinOp gets rewritten - so its
            # demand is taken from the target's declaration instead. Without
            # this the outer `box` in `i: Any = box(int64(j) - int64(1))` is
            # never even a candidate, and that statement has no repair at all.
            if candidate.value_slot is not None and candidate.declared:
                outer = determined_kind(root, candidate.declared)
                if outer is not None:
                    options.setdefault(candidate.value_slot, outer)
            for slot, kind in options.items():
                if slot in plan:
                    continue
                grown = dict(plan)
                grown[slot] = kind
                nxt.append(grown)
        frontier = nxt
        if not frontier:
            break
    return key, row, results


def _worker_probe(task):
    key, row, plan_items, interface_items = task
    candidate = _WORKER["candidates"][key]
    plan = dict(plan_items)
    interface = dict(interface_items)
    try:
        ok, root = probe_root_type(
            probe_module(_WORKER["base"], candidate, plan, interface))
    except Exception:
        ok, root = False, None
    return key, row, plan_items, ok, root


def phase_one_parallel(probe_base, candidates, pools, max_wraps, budget,
                       verbose, cache=None, tables=None, kinds=None,
                       workers=None):
    """Two waves: find out what each position needs, then try the subsets.

    Wave one binds each (statement, typing) once and reads back both the type
    every position produces and the type its context demands. That fixes the
    coercion at each position, so wave two only has to decide which positions
    get one. The old shape searched the kind as well, which is why one line -
    `perm0[i] = perm[k - i]` - cost 12160 binds on its own.
    """
    cache = {} if cache is None else cache
    tables = {} if tables is None else tables
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    started = time.monotonic()
    probes = 0

    rows_of = {c.key: list(itertools.product(
        *[pool_for(pools, name) for name in c.reads])) for c in candidates}
    by_key = {c.key: c for c in candidates}

    with ProcessPoolExecutor(max_workers=workers, initializer=_worker_init,
                             initargs=(probe_base, candidates)) as pool:
        tasks = []
        for candidate in candidates:
            for row in rows_of[candidate.key]:
                if (candidate.key, row) in cache:
                    continue
                tasks.append((candidate.key, row,
                              tuple(zip(candidate.reads, row)), max_wraps))
        if verbose and tasks:
            print(f"    {len(tasks)} statement x typing repairs",
                  file=sys.stderr)
        if tasks:
            for key, row, results in pool.map(_worker_repair, tasks,
                                              chunksize=8):
                probes += len(results)
                cache[(key, row)] = results
                for items, ok, root in results:
                    if not ok:
                        continue
                    table = tables.setdefault(key, {})
                    slot = (row, root)
                    if slot not in table or len(items) < table[slot][0]:
                        table[slot] = (len(items), dict(items))

    for (key, row), results in cache.items():
        if key not in by_key:
            continue
        for items, ok, root in results:
            if not ok:
                continue
            table = tables.setdefault(key, {})
            slot = (row, root)
            if slot not in table or len(items) < table[slot][0]:
                table[slot] = (len(items), dict(items))

    return tables, probes, time.monotonic() - started


def phase_one(probe_base, candidates, pools, max_wraps, budget, verbose,
              dynamic_marker=None, cache=None, tables=None, kinds=None):
    """Cheapest wrap plan per (interface, inferred root), per expression.

    Costs are kept down three ways, all of which matter once this is run
    repeatedly to a fixpoint: the module is stripped to declarations, wrap
    depth escalates only when the shallower depth found nothing, and every
    probe is cached on (expression, interface, plan) so a later pass pays only
    for the interface rows the pools have newly opened.
    """
    cache = {} if cache is None else cache
    tables = {} if tables is None else tables
    kinds = CAST_KINDS if kinds is None else kinds
    probes = 0
    started = time.monotonic()
    for candidate in candidates:
        table = tables.setdefault(candidate.key, {})
        rows = list(itertools.product(*[pool_for(pools, name)
                                        for name in candidate.reads]))
        fresh = 0
        for row in rows:
            interface = dict(zip(candidate.reads, row))
            # Do NOT stop at the first depth that works. A deeper plan cannot
            # be cheaper for a root already found, but it can reach a root that
            # no shallower plan reaches: `perm[0]` yields int64 bare and int
            # only once boxed, and stopping early left fannkuch without the row
            # its answer needs. Cheapest-first ordering still means the first
            # plan to reach a given root is that root's minimum.
            viable = set()
            for count in range(max_wraps + 1):
                for plan in wrap_plans_at(candidate, count, kinds, viable):
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
                    if count == 1:
                        viable |= set(plan.items())
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

def build_factors(candidates, tables):
    """One factor per candidate: which typings it allows, and what they cost.

    A candidate constrains the locals it reads and the one it defines, and
    nothing else. That is what makes this a sparse problem rather than a
    345-million-way product - the old enumeration walked that product and, with
    a cap of 200000, examined 0.06% of it, which is why a wider cast vocabulary
    appeared to make the answer worse instead of better.
    """
    factors = []
    for candidate in candidates:
        variables = list(candidate.reads)
        if candidate.root_name and candidate.root_name not in variables:
            variables.append(candidate.root_name)
        allowed: dict[tuple, tuple] = {}
        for (row, root), (cost, plan) in tables.get(candidate.key, {}).items():
            assignment = dict(zip(candidate.reads, row))
            if candidate.root_name:
                # An assignment that both reads and defines a local - `i = i+1`
                # - has one variable, not two, so its incoming and outgoing
                # types have to agree. That is the loop's fixpoint condition.
                seen = assignment.get(candidate.root_name)
                if seen is not None and not compatible(root, seen):
                    continue
                assignment[candidate.root_name] = (
                    seen if seen is not None else root)
                if seen is None and not compatible(root, assignment[
                        candidate.root_name]):
                    continue
            key = tuple(assignment[name] for name in variables)
            if key not in allowed or cost < allowed[key][0]:
                allowed[key] = (cost, plan)
        factors.append((candidate.key, tuple(variables), allowed))
    return factors


def phase_three(candidates, tables, pools, verbose, keep=64):
    """Minimum-cost typing, by branch and bound over the factors.

    Variables are ordered most-constrained-first and the partial cost is
    bounded against the best complete solution so far, so the search touches a
    tiny fraction of the product. The result is the true minimum of the model
    rather than the best thing in an arbitrary prefix of it.
    """
    factors = build_factors(candidates, tables)
    names = sorted({name for _, variables, _ in factors for name in variables})
    domains = {name: [value for value in pool_for(pools, name)] for name in names}

    touching: dict[str, list] = {name: [] for name in names}
    for index, (_, variables, _) in enumerate(factors):
        for name in variables:
            touching[name].append(index)
    order = sorted(names, key=lambda n: (-len(touching[n]), len(domains[n])))
    position = {name: index for index, name in enumerate(order)}

    # A factor can be scored as soon as its last variable is assigned.
    ready_at: dict[int, int] = {}
    for index, (_, variables, _) in enumerate(factors):
        ready_at[index] = max((position[v] for v in variables), default=-1)
    ready_by_depth: dict[int, list] = {}
    for index, depth in ready_at.items():
        ready_by_depth.setdefault(depth, []).append(index)

    # The k cheapest solutions, not just the cheapest. The model decomposes a
    # program into independent statements, and that decomposition is lossy, so
    # its optimum sometimes will not assemble. Keeping a ranked shortlist lets
    # verification fall through to the next candidate instead of giving up -
    # the first one that compiles and behaves is then the cheapest that does.
    found: list = []
    assignment: dict[str, str] = {}

    def ceiling():
        return found[-1][0] if len(found) >= keep else None

    def descend(depth, cost, plans):
        limit = ceiling()
        if limit is not None and cost > limit:
            return
        if depth == len(order):
            found.append((cost, dict(assignment), dict(plans)))
            found.sort(key=lambda item: item[0])
            del found[keep:]
            return
        name = order[depth]
        for value in domains[name]:
            assignment[name] = value
            extra, chosen, ok = 0, [], True
            for index in ready_by_depth.get(depth, ()):
                key_name, variables, allowed = factors[index]
                key = tuple(assignment[v] for v in variables)
                hit = allowed.get(key)
                if hit is None:
                    ok = False
                    break
                extra += hit[0]
                chosen.append((key_name, hit[1]))
            if ok:
                for key_name, plan in chosen:
                    plans[key_name] = plan
                descend(depth + 1, cost + extra, plans)
                for key_name, _ in chosen:
                    plans.pop(key_name, None)
            del assignment[name]

    descend(0, 0, {})
    if verbose:
        size = 1
        for name in order:
            size *= len(domains[name])
        costs = sorted({item[0] for item in found})
        print(f"  interface variables: {len(order)} (space {size}), "
              f"{len(found)} kept, costs {costs[:6]}", file=sys.stderr)
    return found, len(order)


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

def run(source, mask, max_wraps, budget, rounds, verbose=True,
        workers=None, narrow=False):
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
        tables, probes, elapsed = phase_one_parallel(
            probe_base, candidates, pools, max_wraps, budget, verbose,
            cache, tables, None, workers)
        total_probes += probes
        solved = sum(len(t) for t in tables.values())
        print(f"  {probes} new probes ({total_probes} total) in {elapsed:.1f}s, "
              f"{solved} table rows", file=sys.stderr)

        # Feed the observed roots back into the pools and go round again; the
        # cache means the next pass only pays for genuinely new interface rows.
        grown, changed = revise_pools(pools, candidates, tables)
        print(f"  pools revised for {changed} locals", file=sys.stderr)
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
    parser.add_argument("--narrow", action="store_true",
                        help="only box/int64/cbool/double")
    parser.add_argument("--workers", type=int, default=None,
                        help="probe processes (default: cores - 1)")
    parser.add_argument("--verify", type=int, default=400,
                        help="proposals to check, cheapest first")
    args = parser.parse_args()

    started = time.monotonic()
    result = run(args.source.read_text(), args.mask, args.max_wraps,
                 args.budget, args.rounds, not args.quiet, args.workers,
                 args.narrow)
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

"""Probe machinery: put a statement under a stated interface and ask CinderX.

Support code, not a solver. `bool_solver.py` is the solver; everything here
exists so it can ask "if these reads arrive typed like this, what does this
statement cost and what does it yield".

`pinned_settle` is the graph half - Graph.settle with some binders held typed -
and `check_reference` asserts on every run that with no pins it still
reproduces Graph.settle exactly, so this cannot drift from the rule the
mediator uses.
"""
from __future__ import annotations

import ast
import copy
import itertools
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(HERE)]

from import_adder import add_imports
from print_instances import wrappable
from typedness_graph import CONTEXT, TYPE

sys.path.insert(0, str(ROOT / "_cinderx/cinderx/PythonLib"))
from cinderx.compiler.errors import CollectingErrorSink
from cinderx.compiler.static import StaticCodeGenerator
from cinderx.compiler.static.compiler import Compiler

PROBE = "__probe__"
DYNAMIC = "dynamic"
PROBE_LINE = 100_000

PRIMITIVE_NAMES = frozenset({
    "cbool", "double",
    "int8", "int16", "int32", "int64",
    "uint8", "uint16", "uint32", "uint64",
})

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


def pinned_settle(graph, bound, erased=(), pinned=None):
    """Graph.settle, with positions whose type a wrapper has fixed.

    With `pinned` empty this reproduces Graph.settle exactly; `check_reference`
    below asserts that on every program it is run against, which is what keeps
    this copy honest as the real rule changes.
    """
    pinned = pinned or {}
    by_type, by_context = graph.sources()
    _, recovers, seeds = graph.classify(erased, bound)
    decided_nodes = list(bound.types) + list(recovers)

    # A pinned node yields its wrapper's type, so erasure cannot kill it and
    # the fixpoint must not reconsider it.
    dead = {node for node in seeds if node not in pinned}
    pending = True
    while pending:
        pending = False
        for node in decided_nodes:
            if node in dead or node in pinned:
                continue
            if graph.decide_type(node, by_type.get(node, ()), dead,
                                 bound) is bound.dynamic:
                dead.add(node)
                pending = True

    types = {node: (bound.dynamic if node in dead else value)
             for node, value in bound.types.items()}
    for node in decided_nodes:
        if node in dead or node in pinned:
            continue
        decided = graph.decide_type(node, by_type.get(node, ()), dead, bound)
        if decided is not None:
            types[node] = decided
    types.update(pinned)

    contexts = dict(bound.type_contexts)
    for node in seeds:
        if node not in pinned:
            contexts[node] = bound.dynamic
    for node, feeds in by_context.items():
        if node in contexts and node not in seeds:
            contexts[node] = graph.decide_context(node, feeds, dead, bound)

    return SimpleNamespace(types=types, contexts=contexts, dead=dead)


def check_reference(graph, bound, erased):
    """pinned_settle with no pins must be Graph.settle. Differential test."""
    reference = graph.settle(bound, erased)
    ours = pinned_settle(graph, bound, erased)
    for name in ("types", "contexts"):
        mine, theirs = getattr(ours, name), getattr(reference, name)
        if mine.keys() != theirs.keys():
            return f"{name}: key sets differ"
        for node in theirs:
            if mine[node] is not theirs[node]:
                line = getattr(node, "lineno", "?")
                return f"{name}: line {line} {type(node).__name__} differs"
    return None

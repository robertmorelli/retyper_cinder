"""Settle the type/context tables over the dependency graph.

Detyping deletes rows from the source table; nothing injects dynamic. A vertex
is typed iff it is a surviving source, or every vertex feeding it is typed --
AND, because a combining expression is only as static as its weakest input.

AND is not reachability, so this iterates to a fixpoint rather than doing one
closure. Typedness only ever grows from the sources, so the iteration is
monotone and terminates; a dependency cycle with no source inside it settles
dynamic, which is the conservative answer.
"""
from ast import (AST, AnnAssign, arg, BinOp, BoolOp, UnaryOp, Call, ClassDef, Compare, Constant, Dict, DictComp,
                 FunctionDef, AsyncFunctionDef, GeneratorExp, Is, IsNot, List,
                 ListComp, Load, Name, Not, Set, SetComp, Slice, Store, Tuple,
                 UnaryOp, And, BoolOp, If, Raise, Return, Continue, Break, walk)
from collections import defaultdict

from cinderx.compiler.static.types import CType

from graph_construction import (TYPE, CTX, COERCE, COERCIONS, UNARY,
                                callee_params, class_defs, resolve_callee)

# roles whose destination is decided by its operands, even when the node would
# otherwise count as a source
RECOMPUTE = (COERCE, UNARY)
from parent_pointers import build_parents
from patch_picker import boxed_instance
from type_transfer import transfer, element_type
from dunder_table import load_table

CONTAINERS = (List, Tuple, Set, Dict, ListComp, SetComp, GeneratorExp, DictComp)
from patch_picker import boxed_instance



def external_contexts(tree, types, classes):
    """Nodes whose context is imposed from outside this module.

    `box(x)` wants a primitive, `Array[int64](n)` wants an int, and no
    annotation in this file can change that -- the demand comes from a
    signature erasure cannot reach. So their recorded context survives, where
    a context supplied by a local annotation does not.
    """
    out = set()
    for n in walk(tree):
        if isinstance(n, Call) and resolve_callee(n.func, types, classes) is None:
            out.update(n.args)
            out.update(kw.value for kw in n.keywords)
    return out


def _guarded_name(test):
    """The name a test narrows, if it is a guard we can read syntactically."""
    if isinstance(test, Call) and isinstance(test.func, Name) \
            and test.func.id == "isinstance" and test.args \
            and isinstance(test.args[0], Name):
        return test.args[0].id
    if isinstance(test, Compare) and isinstance(test.left, Name) \
            and len(test.ops) == 1 and isinstance(test.ops[0], IsNot) \
            and isinstance(test.comparators[0], Constant) \
            and test.comparators[0].value is None:
        return test.left.id                       # `x is not None`
    if isinstance(test, BoolOp) and isinstance(test.op, And):
        for v in test.values:
            if (g := _guarded_name(v)) is not None:
                return g
    return None


def _reads_of(nodes, name, out):
    for stmt in nodes:
        for sub in walk(stmt):
            if isinstance(sub, Name) and isinstance(sub.ctx, Load) and sub.id == name:
                out.add(sub)


def _exits(stmt):
    """Does this branch leave the block, so what follows is guarded?"""
    return isinstance(stmt, (Raise, Return, Continue, Break))


def narrowed_reads(tree):
    """Reads made static by a guard the source spells out.

    Two shapes, both visible in the tree rather than in the binder's state:

        if isinstance(x, T):  x.foo          # guarded inside the body
        if not isinstance(x, T): raise ...   # guarded *after* the statement
        x.foo

    The second is the common one -- an argument check at the top of a function
    that bails out -- and it narrows the whole rest of the block.
    """
    out = set()
    for n in walk(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(n, field, None)
            if not isinstance(block, list):
                continue
            for i, stmt in enumerate(block):
                if not isinstance(stmt, If):
                    continue
                if (name := _guarded_name(stmt.test)) is not None:
                    _reads_of(stmt.body, name, out)
                elif isinstance(stmt.test, UnaryOp) and isinstance(stmt.test.op, Not) \
                        and (name := _guarded_name(stmt.test.operand)) is not None \
                        and stmt.body and _exits(stmt.body[-1]):
                    _reads_of(block[i + 1:], name, out)
                    _reads_of(stmt.orelse, name, out)
    return out


def declaration_nodes(tree):
    """Nodes whose annotation, if it survives, supplies their own context."""
    out = set()
    for n in walk(tree):
        if isinstance(n, (arg, FunctionDef, AsyncFunctionDef)):
            out.add(n)
        elif isinstance(n, AnnAssign):
            out.add(n.target)
    return out


def known_type_source(tree, types, constructors, dyn, erased=(), graph=None, parents=None):
    """AST nodes where a type enters the program, minus whatever was erased.

    Annotations are the erasable ones. Constructors, casts and literals are
    not: their type is written into the expression itself.
    """
    src = set()
    classes = class_defs(tree)
    defined = {n.name for n in walk(tree)
               if isinstance(n, (ClassDef, FunctionDef, AsyncFunctionDef))}
    has_in = {w for outs in (graph or {}).values() for w in outs}
    narrowed = narrowed_reads(tree)
    for n in walk(tree):
        if n in erased:
            continue
        if isinstance(n, arg):
            if n.annotation is not None or n.arg in ("self", "cls"):
                src.add(n)                    # self is never erased
        elif isinstance(n, (FunctionDef, AsyncFunctionDef)) and n.returns is not None:
            src.add(n)
        elif isinstance(n, AnnAssign) and n.annotation is not None:
            src.add(n.target)
        elif isinstance(n, Constant):
            src.add(n)
        elif isinstance(n, CONTAINERS):
            src.add(n)                    # a list is a list whatever is in it
        elif isinstance(n, UnaryOp) and isinstance(n.op, Not):
            src.add(n)                    # `not x` is bool whatever x is
        elif isinstance(n, Compare) and all(isinstance(o, (Is, IsNot)) for o in n.ops):
            src.add(n)                    # identity comparison is bool regardless
        elif isinstance(n, Slice):
            src.add(n)                    # a slice is a slice

        elif isinstance(n, Name) and isinstance(n.ctx, Load):
            if n in narrowed:
                src.add(n)                # a guard makes this read static
            elif n.id in defined:
                # a class or function name. Not in expr_types at all, so this
                # cannot be looked up -- it has to be recognised by name.
                src.add(n)
            elif (n, False) not in has_in and types.get(n) not in (None, dyn):
                src.add(n)                # builtin or import: nothing feeds it
        elif isinstance(n, Call):
            if n.func in constructors or (isinstance(n.func, Name) and n.func.id in COERCIONS):
                src.add(n)
            elif callee_params(n, types, classes, parents)[1] is None and types.get(n) not in (None, dyn):
                src.add(n)                    # builtin: return type is a lookup
    return src


def _incoming(g):
    inc = defaultdict(list)
    for v, outs in g.items():
        for w in outs:
            inc[w].append(v)
    return inc


def settle(g, sources, declarations=(), ctx_seeds=(), or_nodes=()):
    """vertex -> is typed. AND over inflow, sources override.

    A surviving declaration is its own context -- cinder records ctx == type for
    an assignment target, because the slot being written is what demands the
    value. Erase the annotation and that context is gone, which is what makes
    the value box. So this is seeded per declaration, not carried by an edge.
    """
    incoming = defaultdict(list)
    for v, outs in g.items():
        for w in outs:
            incoming[w].append(v)

    seeded = ({(n, TYPE) for n in sources}
              | {(n, CTX) for n in declarations if n in sources}
              | {(n, CTX) for n in ctx_seeds})

    # Descend from above rather than ascend from below. A loop-carried
    # definition makes a read depend on itself, and a least fixpoint never
    # lets such a cycle become typed even when it is stably typed in reality
    # -- cinder starts from the pre-loop type and iterates, so a cycle with no
    # dynamic input stays typed. Starting everything typed and removing what
    # an untyped input forces dynamic reproduces that, and still terminates:
    # typedness only ever decreases.
    typed = set(seeded)
    typed.update(v for v in incoming)
    typed.update(v for outs in g.values() for v in outs)
    typed.update((n, TYPE) for n in sources)

    changed = True
    while changed:
        changed = False
        for v in list(typed):
            if v in seeded:
                continue
            ins = incoming.get(v)
            if not ins:
                typed.discard(v)          # nothing supplies it
                changed = True
                continue
            rule = any if (v[1] is CTX or v[0] in or_nodes) else all
            if not rule(u in typed for u in ins):
                typed.discard(v)
                changed = True
    return typed


NATURAL = {bool: "bool", int: "int", float: "float", str: "str", bytes: "bytes"}


def natural_type(node, env):
    """What a literal is worth with no context telling it otherwise.

    `2` is int64 only because something asked for int64; erase that and it is
    an int again. Likewise `[]` is a chklist only while an annotation says so.
    """
    if isinstance(node, Constant):
        name = NATURAL.get(type(node.value))
        return getattr(env, name).instance if name else None
    if isinstance(node, (List, ListComp)):
        return env.list.instance
    if isinstance(node, (Dict, DictComp)):
        return env.dict.instance
    if isinstance(node, (Set, SetComp)):
        return env.set.instance
    if isinstance(node, Tuple):
        return env.tuple.instance
    return None


def settled_type(node, t, typed, dyn, declarations=()):
    """The type this node ends up with after erasure."""
    if (node, TYPE) not in typed:
        return dyn
    if (node, CTX) not in typed:                    # context gone: revert
        nat = natural_type(node, dyn.klass.type_env)
        if nat is not None:
            return nat
        if node in declarations and t is not None and isinstance(t.klass, CType):
            # a declaration that no longer declares a primitive holds the boxed
            # value instead
            return boxed_instance(t)
    return t


OPERAND_FIELDS = ("left", "right", "operand", "comparators", "values", "elt",
                  "key", "elts", "body", "orelse")


def operands_of(node):
    out = []
    for f in OPERAND_FIELDS:
        v = getattr(node, f, None)
        if isinstance(v, list):
            out.extend(x for x in v if isinstance(x, AST))
        elif isinstance(v, AST):
            out.append(v)
    return out


def retype(tree, out_types, dyn):
    """Recompute derived types from the types their operands actually settled to.

    Settling typedness is not enough: a node that stays typed still holds the
    type it was recorded with before erasure. `i < n` is cbool because i and n
    were int64 -- erase those and the comparison is a plain bool, so a coercion
    chosen from the recorded type boxes something unboxable.
    """
    changed = True
    while changed:
        changed = False
        for node in walk(tree):
            t = out_types.get(node)
            if t is None or t is dyn or not isinstance(t.klass, CType):
                continue
            ops = [o for o in operands_of(node) if o in out_types]
            if not ops:
                continue
            # a primitive result needs primitive inputs; otherwise it boxes
            if any(not isinstance(getattr(out_types[o], "klass", None), CType)
                   for o in ops):
                boxed = boxed_instance(t)
                if out_types[node] is not boxed:
                    out_types[node] = boxed
                    changed = True


def settle_tables(tree, g, types, type_ctxs, constructors, dyn, erased=(),
                  deps=None, dyn_ctx=(), elem_ctx=None):
    """Rewrite the type and context tables for the post-erasure program."""
    sources = known_type_source(tree, types, constructors, dyn, erased, g, build_parents(tree))
    declarations = declaration_nodes(tree)
    external = external_contexts(tree, types, class_defs(tree))
    reads = {n for n in walk(tree) if isinstance(n, Name) and isinstance(n.ctx, Load)}
    typed = settle(g, sources, declarations, external, reads)
    # Carry the type through the graph, not just the typedness. A node that
    # stays typed still holds whatever it was recorded as before erasure, so
    # every derived type has to be recomputed from what its inputs actually
    # settled to -- otherwise a target keeps its old type, every read of it
    # does too, and the type/context mismatch that drives coercion never
    # appears.
    inc0 = _incoming(g)
    out_types = {}
    for n, t in types.items():
        out_types[n] = settled_type(n, t, typed, dyn, declarations)

    # An erased declaration does not disappear, it becomes `: Any` -- and a
    # dynamic slot still *demands* dynamic. cinder narrows the local to
    # whatever was assigned, so the type flows on as before, but the assignment
    # itself has to fit a dynamic slot: `n: Any = int64(nb)` is rejected unless
    # the primitive boxes. So an erased declaration keeps its inferred type and
    # supplies no context.
    erased_decls = set()
    for n in erased:
        if isinstance(n, AnnAssign):
            erased_decls.add(n.target)
        elif isinstance(n, (arg, FunctionDef, AsyncFunctionDef)):
            erased_decls.add(n)

    if deps is not None:
        # Role-labelled propagation: each node's type comes from a transfer
        # function over its inputs' *settled* types, so a derived type is
        # recomputed rather than inherited from the pre-erasure bind.
        type_env = dyn.klass.type_env
        table = load_table()
        changed = True
        while changed:
            changed = False
            for node in list(out_types):
                node_deps = deps.get(node, [])
                if node in sources and not any(r in RECOMPUTE for r, _, _ in node_deps):
                    # a source is its own answer -- except where the operands
                    # decide it. A coercion depends on what it is coercing, and
                    # `not x` is cbool or bool depending on x, so neither can be
                    # frozen at the type it had before erasure.
                    continue
                new = transfer(node, node_deps, out_types, types,
                               type_env, dyn, table)
                if node in erased_decls and new is not None \
                        and new is not dyn and isinstance(new.klass, CType):
                    # the slot is `Any` now, so the primitive on the right can
                    # only get in by boxing -- and what the slot then holds,
                    # and every read of it sees, is the boxed type
                    new = boxed_instance(new)
                if new is not None and out_types[node] is not new:
                    out_types[node] = new
                    changed = True
        return out_types, _settle_ctxs(out_types, type_ctxs, typed, inc0,
                                       external, sources, declarations, dyn,
                                       erased_decls, dyn_ctx, elem_ctx,
                                       tree), typed

    def combine(node, srcs):
        seen = [out_types[s] for s in srcs if s in out_types]
        if not seen:
            return None
        if any(t is dyn for t in seen):
            return dyn
        first = seen[0]
        if all(t is first for t in seen):
            # a read or a target is exactly what reached it
            return first if not isinstance(node, (BinOp, Compare, UnaryOp, BoolOp)) \
                else out_types.get(node)
        return None

    def demote(node):
        """A primitive result needs primitive inputs; otherwise it boxes."""
        t = out_types.get(node)
        if t is None or t is dyn or not isinstance(t.klass, CType):
            return None
        ops = [o for o in operands_of(node) if o in out_types]
        if ops and any(not isinstance(getattr(out_types[o], "klass", None), CType)
                       for o in ops):
            return boxed_instance(t)
        return None

    changed = True
    while changed:
        changed = False
        for node in out_types:
            if node in sources:
                continue                      # a source is its own answer
            srcs = [x for x, b in inc0.get((node, TYPE), []) if b is TYPE]
            new = combine(node, [s.target if isinstance(s, AnnAssign) else s
                                 for s in srcs])
            if new is None:
                new = demote(node)
            if new is not None and out_types[node] is not new:
                out_types[node] = new
                changed = True

    return out_types, _settle_ctxs(out_types, type_ctxs, typed, inc0, external,
                                   sources, declarations, dyn, erased_decls,
                                   dyn_ctx, elem_ctx, tree), typed


def _settle_ctxs(out_types, type_ctxs, typed, incoming, external, sources,
                 declarations, dyn, erased_decls=(), dyn_ctx=(), elem_ctx=None,
                 tree=None):
    """A context is the settled type of whatever demands it.

    Taking the recorded context instead leaves stale expectations behind:
    `n: int64 = int64(nb)` with the annotation erased still reads int64, so
    nothing boxes and the primitive lands in a dynamic slot.
    """
    # A union cannot contain a primitive, so the arms of a boolean operator
    # may only be primitives if *all* of them still are. Which is true depends
    # on what they settled to, not on what they were recorded as -- in the
    # original program every arm of `a == b or c.stay or cbool(d)` is a cbool,
    # and only erasure makes two of them dynamic.
    mixed_arms = set()
    for n in walk(tree) if tree is not None else ():
        if isinstance(n, BoolOp) and not all(
                isinstance(getattr(out_types.get(v), "klass", None), CType)
                for v in n.values):
            mixed_arms.update(n.values)

    def ctx_of(node, recorded):
        if node in mixed_arms:
            return dyn
        if node in dyn_ctx:
            return dyn            # the language demands a non-primitive here
        if elem_ctx and (container := elem_ctx.get(node)) is not None:
            t = out_types.get(container)
            return dyn if t is None or t is dyn else element_type(t, None, dyn)
        if node in external:
            return recorded                           # fixed by a foreign signature
        if node in sources and node in declarations:
            return out_types.get(node, recorded)      # its own declaration
        demanders = incoming.get((node, CTX), [])
        for src, slot in demanders:
            if src in erased_decls:
                return dyn                # the slot is declared `Any` now
            if slot is TYPE and (src, TYPE) in typed:
                return out_types.get(src, recorded)
        if demanders:
            # something demanded a type here and no longer has one: the slot
            # went dynamic, which is what makes the value it holds box
            return dyn
        # nothing demands this position at all. cinder records the expression's
        # own type as its context in that case, not dynamic -- an expression
        # nobody constrains satisfies itself, and calling it dynamic invents a
        # mismatch that makes patch_adder coerce something already correct.
        return out_types.get(node, recorded)

    return {n: ctx_of(n, c) for n, c in type_ctxs.items()}

"""Transfer functions: the settled type of a node from its inputs' types.

One function per *destination node*, dispatched on the roles of its inbound
edges. Not one closure per edge: `a + b` cannot be resolved from either operand
alone, and a closure captured at build time would bake in a pre-erasure type,
which is the exact failure this replaces.

Everything here defers to cinderx where cinderx has an answer. Joins call the
same union the binder's `LocalsBranch._join` calls, operators come out of the
generated table in `dunder_table`, and element types are read off the live
`Value`. The graph decides *which* rule applies; it does not restate the rule.
"""
from ast import Not, Subscript

from cinderx.compiler.static.types import CType

from patch_picker import boxed_instance

from graph_construction import (VALUE, ELEM, RETURN, RECEIVER, FIELD, BINOP,
                                CMPOP, UNARY, BRANCH, COERCE, ELTS)
from dunder_table import result_of, descr_of


def join(values, type_env, dyn):
    """The type of a value reaching a merge point from several branches.

    This is `TypeBinder._join`: widen each arm to its inexact type and union
    them. Reimplementing the widening would be a second copy of the type rules,
    and a second copy is a second thing to be wrong.
    """
    if not values:
        return None
    if any(v is dyn or v is None for v in values):
        return dyn
    first = values[0]
    if all(v is first for v in values):
        return first
    if any(isinstance(getattr(v, "klass", None), CType) for v in values):
        # a union cannot contain a primitive -- cinder rejects the type
        # outright -- so two arms that disagree about primitiveness meet at
        # dynamic instead
        return dyn
    try:
        return type_env.get_union(
            tuple(v.klass.inexact_type() for v in values)).instance
    except Exception:
        return dyn


def element_type(container, index_node, dyn):
    """What comes out of `c[i]` or `for x in c`.

    The container's own type is the wrong answer -- an Array[int64] indexes to
    int64 -- and it is the answer the old table gave, which is why every
    primitive element store went wrong.
    """
    args = getattr(getattr(container, "klass", None), "type_args", None)
    if not args:
        return dyn
    if len(args) > 1 and isinstance(index_node, Subscript):
        return args[-1].instance          # a mapping indexes to its value type
    return args[0].instance


def transfer(node, inputs, out_types, recorded, type_env, dyn, table):
    """The type `node` settles to, or None to leave it as it is.

    `inputs` is the labelled inflow: [(role, src node, op)].
    """
    if not inputs:
        return None

    def typ(src):
        return out_types.get(src, recorded.get(src))

    # A dynamic receiver poisons whatever is read off it, whatever the
    # declaration says: a field of an untyped object is untyped.
    for role, src, _ in inputs:
        if role is RECEIVER and typ(src) in (dyn, None):
            return dyn

    by_role = {}
    for role, src, op in inputs:
        by_role.setdefault(role, []).append((src, op))

    if BINOP in by_role or CMPOP in by_role:
        role = BINOP if BINOP in by_role else CMPOP
        ops = by_role[role]
        if len(ops) < 2:
            return dyn
        (left, op), (right, _) = ops[0], ops[1]
        lt, rt = typ(left), typ(right)
        if lt is dyn or rt is dyn or lt is None or rt is None:
            return dyn
        got = result_of(table, op, descr_of(lt), descr_of(rt), type_env)
        # no row means cinder rejects the combination; nothing survives it
        return got if got is not None else dyn

    if COERCE in by_role:
        src, name = by_role[COERCE][0]
        t = typ(src)
        if name != "box":
            # int64(x), cbool(x), cast(T, x): the result is written in the
            # expression itself and no operand can change it
            return None
        if t is None or t is dyn:
            return dyn
        if isinstance(getattr(t, "klass", None), CType):
            return boxed_instance(t)
        # boxing something already boxed. The coercion is redundant rather than
        # type-changing, and saying so is what lets a consumer drop it -- the
        # author-written `box(size)` whose operand stopped being primitive.
        return t

    if UNARY in by_role:
        src, op = by_role[UNARY][0]
        t = typ(src)
        if isinstance(op, Not):
            # `not x` is a bool -- but a *primitive* bool when x is primitive,
            # which is what `bind_unaryop` does. Keeping the recorded cbool
            # after the operand went dynamic is what makes the pass emit
            # `box(not ...)` on something that is no longer primitive.
            prim = t is not None and isinstance(getattr(t, "klass", None), CType)
            return type_env.cbool.instance if prim else type_env.bool.instance
        return dyn if t is None else t

    if ELEM in by_role:
        src, detail = by_role[ELEM][0]
        index, recorded_item = (detail if isinstance(detail, tuple)
                                else (detail, None))
        t = typ(src)
        if t is None or t is dyn:
            return dyn
        item = element_type(t, node, dyn)
        if index is not None:
            args = getattr(getattr(item, "klass", None), "type_args", None)
            if args and index < len(args):
                return args[index].instance
        return recorded_item or item

    if FIELD in by_role:
        src, _ = by_role[FIELD][0]
        return typ(src)

    if RETURN in by_role:
        src, _ = by_role[RETURN][0]
        t = typ(src)
        # the callee's vertex holds its return type; erased, the call is dynamic
        return dyn if t is None else t

    if ELTS in by_role:
        # `visitList` widens the element types into one item type. The
        # container is still a container whatever that turns out to be, so a
        # dynamic element does not make the literal dynamic -- it makes the
        # item type dynamic, which is what the recorded container type already
        # says once its elements are known.
        return None

    if BRANCH in by_role:
        return join([typ(s) for s, _ in by_role[BRANCH]], type_env, dyn)

    if VALUE in by_role:
        vals = [typ(s) for s, _ in by_role[VALUE]]
        return join(vals, type_env, dyn)

    return None

from ast import If, IfExp, Name, Load, Attribute, Call, copy_location, walk
from ast import NodeTransformer, Not, Slice, Subscript, UnaryOp
from ast import While, unparse

from .cinderx_binding import get_ctx, is_primative, is_const

def _type_expr(name):
    parts = name.split(".")
    node = Name(id=parts[0], ctx=Load())
    for attr in parts[1:]:
        node = Attribute(value=node, attr=attr, ctx=Load())
    return node

# cbool is a CIntType, but its boxed representation is bool.
def boxed_instance(t):
    env = t.klass.type_env
    return env.bool.instance if t.klass is env.cbool else t.klass.boxed.instance

# Only these primitive types have writable constructor names.
PRIMITIVE_NAMES = {"double", "cbool", "int8", "int16", "int32", "int64",
                   "uint8", "uint16", "uint32", "uint64"}

def readable_name(t):
    if (inner := option_of(t)) is not None:
        # cinder rejects `Optional[X]`, accepts `X | None`
        return f"{readable_name(inner)} | None"
    name: str = t.klass.type_name.readable_name
    for k,v in {"chklist": "CheckedList", "chkdict": "CheckedDict", "chkset": "CheckedSet"}.items():
        name = name.replace(k, v)
    return name


def option_of(t):
    """Return CinderX's canonical Optional member type, if present."""
    inner = getattr(t.klass, "opt_type", None) if t is not None else None
    return None if inner is None else inner.instance


def is_option_of(maybe, inner):
    """Is `maybe` exactly `inner`, or None?"""
    assert maybe is not None and inner is not None
    return (held := option_of(maybe)) is not None and held.klass is inner.klass

class Edit:
    T = None
    def __init__(self, node):
        self.node = self.next_root = node
    def run(self):
        return self.next_root

class BoxEdit(Edit):
    def __init__(self, node, t, exact=False):
        self.node = node
        self.T = boxed_instance(t)
        value = (copy_location(Call(_type_expr(readable_name(t)), [node], []), node)
                 if exact else node)
        self.next_root = copy_location(Call(Name("box", Load()), [value], []), node)

class ConstrEdit(Edit):
    def __init__(self, T, node):
        self.node, self.T = node, T
        self.next_root = copy_location(Call(_type_expr(readable_name(T)), [node], []), node)

DONT_CAST = ('int',)
# These boxed scalars convert values; cast only asserts a type.
CONVERTIBLE = ('float', 'int', 'bool')
# containers whose slice comes back as a plain list
CHECKED_NAMES = ('chklist', 'CheckedList', 'chkdict', 'CheckedDict')
class CastEdit(Edit):
    def __init__(self, T, node):
        if readable_name(T) in DONT_CAST:
            # An inexact int is accepted directly, so this records no edit.
            return super().__init__(node)
        self.node, self.T = node, T
        self.next_root = copy_location(
            Call(Name("cast", Load()), [_type_expr(readable_name(T)), node], []), node)


class LenEdit(Edit):
    def __init__(self, edit, call, types, dyn):
        self.edit, self.call = edit, call
        self.types, self.dyn = types, dyn

    def run(self):
        self.call.func.id = "len"
        self.types[self.call] = self.dyn
        return self.edit.run()


class ClenEdit(Edit):
    def __init__(self, edit, call, types, dyn):
        self.edit, self.call = edit, call
        self.types, self.dyn = types, dyn

    def run(self):
        self.call.func.id = "clen"
        self.types[self.call] = self.dyn.klass.type_env.int64.instance
        return self.edit.run()

def _still_required(produced, held, type_ctx, readers, dyn):
    """Return the type that must be restored after stripping a coercion tower.

    A missing reader type denotes a consumer not yet reached in postorder.
    """
    if produced is None or produced is held or produced is dyn:
        return None
    if is_primative(produced) and type_ctx is dyn:
        return None
    if is_option_of(held, produced):
        return produced
    return produced if any(read is not dyn for read in readers) else None


def _primitive_len(call, asked_for, is_test, types, dyn):
    measured = types.get(call.args[0])
    return (measured is not None and measured is not dyn
            and measured.get_fast_len_type() is not None
            and (is_test or asked_for in PRIMITIVE_NAMES))


def _choose(node, type, type_ctx, valid_pair, is_inline_arg, dyn=None,
            payload=False, produced=None, readers=(), sits_on_len=False,
            asked_for=None, is_test=False, types=None):
    if sits_on_len:
        primitive_len = _primitive_len(node, asked_for, is_test, types, dyn)
        produced_len = (dyn.klass.type_env.int64.instance
                        if primitive_len else dyn)
        edit = _choose(node, produced_len, type_ctx, valid_pair,
                       is_inline_arg, dyn, payload, produced, readers)
        return (ClenEdit(edit, node, types, dyn) if primitive_len
                else LenEdit(edit, node, types, dyn))
    if (required := _still_required(produced, type, type_ctx, readers,
                                    dyn)) is not None:
        # Restore a required representation after stripping the original tower.
        if is_primative(type) and not is_primative(required):
            return BoxEdit(node, type, is_inline_arg)
        if is_primative(required) or readable_name(required) in CONVERTIBLE:
            return ConstrEdit(required, node)
        return CastEdit(required, node)
    if payload and type is dyn and type_ctx is not dyn:
        # A dynamic comprehension payload cannot satisfy a typed container.
        return CastEdit(type_ctx, node)
    if is_option_of(type, type_ctx):
        # the cast is what narrows: drop it and the None reaches the use site.
        return CastEdit(type_ctx, node)
    if dyn is not None and type_ctx is dyn and is_primative(type) and not is_const(node):
        # Machine values must box in dynamic slots; literals box implicitly.
        return BoxEdit(node, type, is_inline_arg)
    if (node.__class__ is Subscript and node.slice.__class__ is Slice
            and readable_name(type_ctx).startswith(CHECKED_NAMES)):
        # Checked-container slices return lists; reconstruct before valid_pair.
        return ConstrEdit(type_ctx, node)
    if not is_inline_arg and valid_pair(type, type_ctx, node):
        return Edit(node)
    elif type == type_ctx:
        return Edit(node)
    elif is_primative(type):
        if is_const(node):
            return Edit(node)
        else:
            return BoxEdit(node, type)
    elif is_primative(type_ctx):
        return ConstrEdit(type_ctx, node)
    else:
        # Inline substitution requires disagreeing arguments to meet as dynamic.
        return CastEdit(type_ctx, node)

# Single-argument conversions Tower may peel; cast is handled separately.
UNWRAPPABLE = {"box"} | PRIMITIVE_NAMES


class Tower(NodeTransformer):
    """Strip coercion calls without descending into unrelated positions."""

    def __init__(self, types=None, env=None):
        self.types, self.env = types, env

    def peel(self, node):
        """What a single coercion holds, or None if it is not a coercion."""
        if not (isinstance(node, Call) and isinstance(node.func, Name)):
            return None
        if node.func.id == "cast" and len(node.args) == 2:
            return node.args[1]
        if node.func.id in UNWRAPPABLE and len(node.args) == 1:
            return node.args[0]
        return None

    def visit(self, node):
        if inner := self.peel(node):
            return self.visit(inner)
        return super().visit(node)

    def ends(self, node):
        """Return the stripped expression, bare operand, and len status."""
        top = self.visit(node)
        bottom = top
        while isinstance(bottom, UnaryOp):
            bottom = bottom.operand
        sits_on_len = (isinstance(top, Call) and isinstance(top.func, Name)
                       and top.func.id in ("clen", "len")
                       and len(top.args) == 1)
        return top, bottom, sits_on_len

    def visit_UnaryOp(self, node):
        """Preserve unary operators; identity signals that nothing was stripped."""
        operand = self.visit(node.operand)
        if operand is node.operand:
            return node
        result = copy_location(UnaryOp(op=node.op, operand=operand), node)
        produced = self.types.get(operand)
        if isinstance(node.op, Not):
            self.types[result] = (self.env.cbool.instance
                                  if produced is not None
                                  and is_primative(produced)
                                  else self.env.bool.instance)
        elif produced is not None:
            self.types[result] = produced
        return result

    def generic_visit(self, node):
        return node


def mediate(node, types, type_ctxs, dyn, valid_pair, graph, payloads,
            is_test=False, is_index=False, is_inline_arg=False):
    """Choose and apply the edit required at one expression position.

    Towers are stripped before comparing their underlying type with the
    position's demand. Position flags come from the parent-aware walk.
    """
    ctx = get_ctx(node)
    if isinstance(node, Slice) or (ctx is not None
                                   and not isinstance(ctx, Load)):
        # Stores, deletions, and slice objects are not value positions.
        return node

    tower, value, sits_on_len = Tower(types, dyn.klass.type_env).ends(node)
    asked_for = (node.func.id if tower is not node
                 and isinstance(node, Call) and isinstance(node.func, Name)
                 else None)
    # Tower records the stripped type, including the effect of unary operators.
    target = tower
    t, tc = types.get(target), type_ctxs.get(node)
    result = target if not (t and tc) else _choose(
        target, t, tc, valid_pair, is_inline_arg, dyn,
        node in payloads, None if tower is node else types.get(node),
        [types.get(where) for where, slot in
         graph.outgoing().get(graph.cell(node, "type"), ())
         if slot == "type"], sits_on_len, asked_for, is_test, types).run()

    # Tests discard narrowing conversions; indices discard only int64 narrowing.
    if is_test:
        inner = Tower().peel(result)
        if inner is not None:
            result = inner
    elif (is_index and isinstance(result, Call)
            and isinstance(result.func, Name) and result.func.id == "int64"
            and len(result.args) == 1):
        result = result.args[0]
    return result

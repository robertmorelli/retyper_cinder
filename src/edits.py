"""Deferred, composable AST edits selected by :mod:`patch_picker`."""
from ast import (Attribute, BinOp, BoolOp, Call, Compare, Load, Name, UnaryOp,
                 copy_location, walk)
from copy import copy

from .cinderx_binding import is_primative

PRIMITIVE_WRAP_COST = 0.99


def _type_expr(name):
    parts = name.split(".")
    node = Name(id=parts[0], ctx=Load())
    for attr in parts[1:]:
        node = Attribute(value=node, attr=attr, ctx=Load())
    return node


def option_of(t):
    """Return CinderX's canonical Optional member type, if present."""
    inner = getattr(t.klass, "opt_type", None) if t is not None else None
    return None if inner is None else inner.instance


def is_option_of(maybe, inner):
    """Is `maybe` exactly `inner`, or None?"""
    assert maybe is not None and inner is not None
    return ((member := option_of(maybe)) is not None
            and member.klass is inner.klass)


def readable_name(t):
    if (inner := option_of(t)) is not None:
        return f"{readable_name(inner)} | None"
    name = t.klass.type_name.readable_name
    for old, new in {"chklist": "CheckedList",
                     "chkdict": "CheckedDict",
                     "chkset": "CheckedSet"}.items():
        name = name.replace(old, new)
    return name


def boxed_instance(t):
    """Return the boxed representation of a primitive instance."""
    env = t.klass.type_env
    return env.bool.instance if t.klass is env.cbool else t.klass.boxed.instance


def _tower_with_operand(tower, operand):
    for unary in reversed(tower[:-1]):
        operand = copy_location(UnaryOp(op=unary.op, operand=operand), unary)
    return operand


def _replace_expr(root, target, replacement):
    if root is target:
        return replacement
    if isinstance(root, UnaryOp):
        node = copy(root)
        node.operand = _replace_expr(root.operand, target, replacement)
        return node
    if isinstance(root, Call):
        node = copy(root)
        node.args = [_replace_expr(arg, target, replacement)
                     for arg in root.args]
        return node
    return root


def _rename_call(root, call, name, primitive_wrap=False):
    renamed = copy(call)
    renamed.func = Name(name, Load())
    renamed._primitive_wrap = primitive_wrap
    return _replace_expr(root, call, renamed)


def operands(node):
    """Return the value positions governed by a multi-operator node."""
    if isinstance(node, BinOp):
        return (node.left, node.right)
    if isinstance(node, BoolOp):
        return tuple(node.values)
    if isinstance(node, Compare):
        return (node.left, *node.comparators)
    return ()


class Edit:
    """A deferred AST rewrite with a recursively visible result and cost."""

    def __init__(self, request, inner=None, node=None, type=None):
        if inner is None:
            inner = request.inner
        self.request, self.inner = request, inner
        self.node = (node if node is not None else
                     inner.node if inner is not None else request.node)
        self.type = (type if type is not None else
                     inner.type if inner is not None else request.type)
        self.bindings = []

    @property
    def cost(self):
        return sum(PRIMITIVE_WRAP_COST
                   if getattr(node, "_primitive_wrap", False) else 1
                   for node in walk(self.node))

    def bind(self, node, type, context=None):
        self.bindings.append((node, type,
                              self.request.type_ctx if context is None
                              else context))
        return node

    def run(self):
        if self.inner is not None:
            self.inner.run()
        types = self.request.mediation.types
        contexts = self.request.mediation.type_contexts
        for node, type, context in self.bindings:
            types[node], contexts[node] = type, context
        return self.node


class NoEdit(Edit):
    """An explicit decision to leave this expression unchanged."""

    def __init__(self, request, type=None):
        super().__init__(request, type=type)


class BoxEdit(Edit):
    def __init__(self, request, exact=False, inner=None):
        inner = inner or NoEdit(request)
        node, type = inner.node, inner.type
        value = (copy_location(
                     Call(_type_expr(readable_name(type)), [node], []), node)
                 if exact else node)
        boxed_type = boxed_instance(type)
        boxed = copy_location(Call(Name("box", Load()), [value], []), node)
        super().__init__(request, inner, boxed, boxed_type)
        if exact:
            self.bind(value, type, type)
        self.bind(boxed, boxed_type)


class ConstrEdit(Edit):
    def __init__(self, request, type, inner=None):
        planned = inner is not None or request.inner is not None
        inner = inner or request.inner or NoEdit(request)
        operand = inner.node if planned else request.tower[-1]
        context = request.type_ctx if planned or len(request.tower) == 1 else type
        converted = copy_location(
            Call(_type_expr(readable_name(type)), [operand], []), operand)
        converted._primitive_wrap = is_primative(type)
        node = (converted if planned or len(request.tower) == 1
                else _tower_with_operand(request.tower, converted))
        super().__init__(request, inner, node, type)
        self.bind(converted, type, context)


class CastEdit(Edit):
    def __init__(self, request, type, inner=None):
        inner = inner or NoEdit(request)
        node = inner.node
        if readable_name(type) in ('int',):
            super().__init__(request, inner)
            return
        cast = copy_location(
            Call(Name("cast", Load()),
                 [_type_expr(readable_name(type)), node], []), node)
        super().__init__(request, inner, cast, type)
        self.bind(cast, type)


class LenEdit(Edit):
    def __init__(self, edit, dynamic):
        node = _rename_call(edit.node, edit.request.node, "len")
        super().__init__(edit.request, edit, node, edit.type)
        self.bind(node, dynamic)


class ClenEdit(Edit):
    def __init__(self, edit, dynamic):
        node = _rename_call(
            edit.node, edit.request.node, "clen", primitive_wrap=True)
        super().__init__(edit.request, edit, node, edit.type)
        self.bind(node, dynamic.klass.type_env.int64.instance)


class DiscardTestConversion(Edit):
    def __init__(self, edit):
        super().__init__(edit.request, edit.inner or NoEdit(edit.request))


class DiscardIndexNarrowing(Edit):
    def __init__(self, edit):
        if isinstance(edit, ConstrEdit) and readable_name(edit.type) == "int64":
            edit = edit.inner or NoEdit(edit.request)
        super().__init__(edit.request, edit)


class MultiOpEdit(Edit):
    """A deferred operator whose operands are themselves deferred edits."""

    def __init__(self, request, node, operands, type):
        preview = copy(node)
        if isinstance(preview, BinOp):
            preview.left, preview.right = (edit.node for edit in operands)
        elif isinstance(preview, BoolOp):
            preview.values = [edit.node for edit in operands]
        elif isinstance(preview, Compare):
            preview.left = operands[0].node
            preview.comparators = [edit.node for edit in operands[1:]]
        super().__init__(request, node=preview, type=type)
        self.operands = operands
        self.bind(preview, type)

    def run(self):
        for operand in self.operands:
            operand.run()
        return super().run()

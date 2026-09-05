"""Implement deferred, composable mediation edits."""
from ast import BinOp, BoolOp, Call, Compare, Load, Name, copy_location, walk
from copy import copy

from utilities.ast_nodes import dotted_name_expression, is_constant, rename_call

from .type_rules import boxed_instance, is_primitive, readable_type_name

PRIMITIVE_WRAP_COST = 0.99


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

    def bind(self, node, type, constraint=None):
        self.bindings.append((node, type,
                              self.request.type_constraint
                              if constraint is None else constraint))
        return node

    def run(self):
        if self.inner is not None:
            self.inner.run()
        types = self.request.mediation.types
        constraints = self.request.mediation.type_constraints
        for node, type, constraint in self.bindings:
            types[node], constraints[node] = type, constraint
        return self.node


class NoEdit(Edit):
    """An explicit decision to leave this expression unchanged."""

    def __init__(self, request, type=None):
        super().__init__(request, type=type)


class BoxEdit(Edit):
    @classmethod
    def create(cls, request, implicit_type=None):
        if is_constant(request.node):
            return NoEdit(request, implicit_type)
        return cls(request, exact=request.inline_arg)

    def __init__(self, request, exact=False, inner=None):
        inner = inner or NoEdit(request)
        node, type = inner.node, inner.type
        value = (
            copy_location(
                Call(
                    dotted_name_expression(readable_type_name(type)),
                    [node],
                    [],
                ),
                node,
            )
            if exact
            else node
        )
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
        operand = inner.node if planned else request.operand
        constraint = (
            request.type_constraint
            if planned or request.is_zero_depth
            else type
        )
        converted = copy_location(
            Call(
                dotted_name_expression(readable_type_name(type)),
                [operand],
                [],
            ),
            operand,
        )
        converted._primitive_wrap = is_primitive(type)
        node = (
            converted
            if planned or request.is_zero_depth
            else request.with_operand(converted)
        )
        super().__init__(request, inner, node, type)
        self.bind(converted, type, constraint)


class CastEdit(Edit):
    @classmethod
    def create(cls, request, type):
        if readable_type_name(type) == "int":
            return NoEdit(request)
        return cls(request, type)

    def __init__(self, request, type, inner=None):
        inner = inner or NoEdit(request)
        node = inner.node
        cast = copy_location(
            Call(Name("cast", Load()),
                 [dotted_name_expression(readable_type_name(type)), node], []),
            node,
        )
        super().__init__(request, inner, cast, type)
        self.bind(cast, type)


class LenEdit(Edit):
    def __init__(self, edit, primitive):
        dynamic = edit.request.dynamic
        node = rename_call(
            edit.node,
            edit.request.node,
            "clen" if primitive else "len",
            _primitive_wrap=primitive,
        )
        super().__init__(edit.request, edit, node, edit.type)
        result_type = (
            dynamic.klass.type_env.int64.instance if primitive else dynamic
        )
        self.bind(node, result_type)


class DiscardTestConversion(Edit):
    def __init__(self, edit):
        super().__init__(edit.request, edit.inner or NoEdit(edit.request))


class DiscardIndexNarrowing(Edit):
    def __init__(self, edit):
        super().__init__(edit.request, edit)


class MultiOpEdit(Edit):
    """A deferred operator whose operands are themselves deferred edits."""

    def __init__(self, request, operands):
        preview = copy(request.node)
        if isinstance(preview, BinOp):
            preview.left, preview.right = (edit.node for edit in operands)
        elif isinstance(preview, BoolOp):
            preview.values = [edit.node for edit in operands]
        elif isinstance(preview, Compare):
            preview.left = operands[0].node
            preview.comparators = [edit.node for edit in operands[1:]]
        super().__init__(request, node=preview, type=request.type)
        self.operands = operands
        self.bind(preview, request.type)

    def run(self):
        for operand in self.operands:
            operand.run()
        return super().run()

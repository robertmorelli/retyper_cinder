"""Analyze coercion calls and unary operators surrounding an expression."""

from ast import NodeTransformer, UnaryOp, copy_location

from utilities.ast_nodes import is_simple_call, what_cast

from .type_rules import REMOVABLE_CALLS, unop_result_type


class UnopSpine(NodeTransformer):
    """A possibly zero-depth unary spine with removable coercions omitted."""

    def __init__(
        self,
        original,
        types,
        environment=None,
        remove_coercions=True,
        analyze=True,
    ):
        self.original = original
        self.types = types
        self.environment = environment
        self.remove_coercions = remove_coercions
        self.derived_types = {}

        top = self.visit(original) if analyze else original
        nodes = [top]
        if analyze:
            while isinstance(nodes[-1], UnaryOp):
                nodes.append(nodes[-1].operand)
        self.nodes = tuple(nodes)
        self.sits_on_len = (
            is_simple_call("clen", top) or is_simple_call("len", top)
        )

    @property
    def top(self):
        return self.nodes[0]

    @property
    def operand(self):
        return self.nodes[-1]

    @property
    def coercion_removed(self):
        return self.top is not self.original

    @property
    def removed_call_name(self):
        if not self.coercion_removed:
            return None
        if cast_type := what_cast(self.original):
            return cast_type
        for name in REMOVABLE_CALLS:
            if is_simple_call(name, self.original):
                return name
        return None

    def type_of(self, node):
        return self.derived_types.get(node, self.types.get(node))

    def with_operand(self, operand):
        for unary in reversed(self.nodes[:-1]):
            operand = copy_location(
                UnaryOp(op=unary.op, operand=operand), unary
            )
        return operand

    def held_expression(self, node):
        if not self.remove_coercions:
            return None
        if what_cast(node):
            return node.args[1]
        for name in REMOVABLE_CALLS:
            if is_simple_call(name, node):
                return node.args[0]
        return None

    def visit(self, node):
        if inner := self.held_expression(node):
            return self.visit(inner)
        return super().visit(node)

    def visit_UnaryOp(self, node):
        operand = self.visit(node.operand)
        if operand is node.operand:
            return node
        result = copy_location(UnaryOp(op=node.op, operand=operand), node)
        produced_type = unop_result_type(
            node.op,
            self.type_of(operand),
            self.environment,
        )
        if produced_type is not None:
            self.derived_types[result] = produced_type
        return result

    def generic_visit(self, node):
        return node

    @classmethod
    def zero_depth(cls, node, types):
        return cls(node, types, remove_coercions=False, analyze=False)

    @classmethod
    def analyze(cls, node, types, environment, remove_coercions=True):
        return cls(node, types, environment, remove_coercions)

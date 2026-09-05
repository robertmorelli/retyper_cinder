"""General helpers for inspecting Python expression trees."""

from ast import (
    Attribute,
    BinOp,
    BoolOp,
    Call,
    Compare,
    Constant,
    List,
    Load,
    Name,
    Starred,
    Subscript,
    Tuple,
    UnaryOp,
    unparse,
)
from copy import copy


def ast_position(node):
    """Return the load/store position carried by an expression, if any."""
    if isinstance(node, (Name, Attribute, Subscript, Starred, List, Tuple)):
        return node.ctx
    return None


def is_constant(node):
    return isinstance(node, Constant)


def dotted_name_expression(name):
    """Build an expression for a dotted name without parsing source."""
    parts = name.split(".")
    node = Name(id=parts[0], ctx=Load())
    for attribute in parts[1:]:
        node = Attribute(value=node, attr=attribute, ctx=Load())
    return node


def replace_expression(root, target, replacement):
    """Replace an expression within a call/unary wrapper spine."""
    if root is target:
        return replacement
    if isinstance(root, UnaryOp):
        node = copy(root)
        node.operand = replace_expression(root.operand, target, replacement)
        return node
    if isinstance(root, Call):
        node = copy(root)
        node.args = [
            replace_expression(argument, target, replacement)
            for argument in root.args
        ]
        return node
    return root


def rename_call(root, call, name, **metadata):
    """Rename one call while preserving its surrounding expression."""
    renamed = copy(call)
    renamed.func = Name(name, Load())
    for attribute, value in metadata.items():
        setattr(renamed, attribute, value)
    return replace_expression(root, call, renamed)


def is_simple_call(name, node):
    """Whether node is a unary call to a bare name."""
    return (
        isinstance(node, Call)
        and isinstance(node.func, Name)
        and node.func.id == name
        and len(node.args) == 1
    )


def what_cast(node):
    """Return the target spelling of a bare cast call, if this is one."""
    if (
        isinstance(node, Call)
        and isinstance(node.func, Name)
        and node.func.id == "cast"
        and len(node.args) == 2
    ):
        return unparse(node.args[0])
    return None


def operands(node):
    """Return the value positions governed by a multi-operator node."""
    if isinstance(node, BinOp):
        return (node.left, node.right)
    if isinstance(node, BoolOp):
        return tuple(node.values)
    if isinstance(node, Compare):
        return (node.left, *node.comparators)
    return ()

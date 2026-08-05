"""Operator resolution: what does `a op b` produce, and what does it demand?

Two kinds of operator, handled separately on purpose.

**User-defined.** `GVector + GVector` is a real `__add__` in a real ClassDef.
Its declared return type and parameter annotation are ordinary erasable
annotations, so they belong in the graph like any other call.

**Builtin.** `str + str`, `[0] * n`, `int64 // int64` have no AST to point at.
Their result depends on the *types* of both operands rather than on any
annotation -- `list * int` is a list, `int * int` is an int, and neither is
recoverable from the operator alone. That dependence is why they are a table
here rather than edges in the graph: an edge carries typedness, and typedness
is exactly what a rule like "list * int -> list, but only while the int has a
type" cannot be reduced to.

Nothing here is a heuristic; each row is a rule Static Python already
implements. The table is the auditable part -- if a row is wrong, the fix is
visible and local.
"""
from ast import (Add, Sub, Mult, Div, FloorDiv, Mod, Pow, LShift, RShift,
                 BitOr, BitAnd, BitXor, MatMult, Eq, NotEq, Lt, LtE, Gt, GtE,
                 FunctionDef, AsyncFunctionDef)

DUNDER = {Add: "__add__", Sub: "__sub__", Mult: "__mul__", Div: "__truediv__",
          FloorDiv: "__floordiv__", Mod: "__mod__", Pow: "__pow__",
          LShift: "__lshift__", RShift: "__rshift__",
          BitOr: "__or__", BitAnd: "__and__", BitXor: "__xor__",
          MatMult: "__matmul__",
          # comparisons dispatch the same way
          Eq: "__eq__", NotEq: "__ne__", Lt: "__lt__", LtE: "__le__",
          Gt: "__gt__", GtE: "__ge__"}

REFLECTED = {"__add__": "__radd__", "__sub__": "__rsub__", "__mul__": "__rmul__",
             "__truediv__": "__rtruediv__", "__floordiv__": "__rfloordiv__",
             "__mod__": "__rmod__", "__pow__": "__rpow__"}

SEQUENCES = {"str", "bytes", "list", "tuple"}

# (left, op) -> whether the result keeps the left type regardless of the right.
# `str % x` formats whatever x is; `seq * n` and `seq + seq` need the right
# operand to still be typed, so they are not unconditional.
UNCONDITIONAL = {("str", Mod), ("bytes", Mod)}


def type_name(t):
    return None if t is None else t.klass.type_name.readable_name


def user_method(owner, name, classes):
    """The ClassDef method `owner.name`, if this module defines it."""
    cls = classes.get(owner)
    if cls is None:
        return None
    for stmt in cls.body:
        if isinstance(stmt, (FunctionDef, AsyncFunctionDef)) and stmt.name == name:
            return stmt
    return None


def resolve_user(op, owner, classes, right_owner=None):
    """The FunctionDef implementing `op` for a class defined in this module.

    Falls back to the right operand's reflected method, which is what Python
    does when the left operand does not implement the operator.
    """
    name = DUNDER.get(type(op))
    if name is None:
        return None
    m = user_method(owner, name, classes)
    if m is None and right_owner is not None:
        m = user_method(right_owner, REFLECTED.get(name, ""), classes)
    return m


def is_builtin_sequence_op(op, left_type):
    """Does a builtin sequence rule fix this result's type?

    True only for the unconditional rows. `[0] * n` deliberately is not one:
    the result is a list only while `n` has a type, so it has to stay an
    ordinary AND over both operands rather than becoming a source.
    """
    name = type_name(left_type)
    return name in SEQUENCES and (name, type(op)) in UNCONDITIONAL

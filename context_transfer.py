"""Evaluation of one explicit graph demand.

A demand either produces one expected type or is inactive. The solver does not
inspect syntax or reinterpret demand roles.
"""
from ast import Constant

from cinderx.compiler.static.types import CType

from graph_construction import (TYPE, CONTEXT, ELEM_CONTEXT, DYNAMIC_CONTEXT,
                                SELF_CONTEXT, NUMERIC_CONTEXT,
                                COMPARE_CONTEXT, BOOL_CONTEXT,
                                RECEIVER_CONTEXT, FixedSource)
from type_transfer import element_type

INACTIVE = object()

PRIORITY = {
    DYNAMIC_CONTEXT: 0,
    RECEIVER_CONTEXT: 1,
    COMPARE_CONTEXT: 2,
    BOOL_CONTEXT: 2,
    NUMERIC_CONTEXT: 3,
    ELEM_CONTEXT: 4,
    CONTEXT: 5,
    SELF_CONTEXT: 9,
}


def evaluate(demand, values, context_of, source_values, erased_decls, dyn):
    """Return the demanded type, or INACTIVE when this edge imposes none."""
    role = demand.role
    source, slot = demand.source
    current = values.get(demand.value)
    peer = values.get(source, source_values.get(source, dyn))

    if role == SELF_CONTEXT:
        return INACTIVE
    if role == DYNAMIC_CONTEXT:
        return dyn
    if role == RECEIVER_CONTEXT:
        return dyn if peer is dyn else INACTIVE
    if role in (COMPARE_CONTEXT, BOOL_CONTEXT):
        return (dyn if isinstance(getattr(current, "klass", None), CType)
                and not isinstance(getattr(peer, "klass", None), CType)
                else INACTIVE)
    if role == NUMERIC_CONTEXT:
        return (peer if not isinstance(getattr(current, "klass", None), CType)
                and isinstance(getattr(peer, "klass", None), CType)
                and not isinstance(demand.value, Constant)
                else INACTIVE)
    if role == ELEM_CONTEXT:
        return dyn if peer is dyn else element_type(peer, None, dyn)
    if role == CONTEXT:
        if source in erased_decls:
            return dyn
        return peer if slot is TYPE else context_of(source)
    raise ValueError(f"unknown demand role: {role}")

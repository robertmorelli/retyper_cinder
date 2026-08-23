"""Specialize the graph for one annotation-erasure choice."""
import ast
from types import SimpleNamespace

from ..annotation_remover import _checked_ctor
from ..cinderx_binding import can_narrow
from .graph_topology import CONTEXT, TYPE, Edge, machine


def def_is_typed(location):
    """Whether rewriting preserves this definition independently of flow."""
    node = location.annotation
    return (type(node) is ast.AnnAssign and location.rhs is not None
            and _checked_ctor(node.annotation, location.rhs) is not None)


def rhs_narrows(location, bound):
    """Whether CinderX can infer this erased definition from its RHS."""
    if (type(location.annotation) is not ast.AnnAssign
            or location.rhs is None
            or type(location.target) is not ast.Name
            or location.owner is None):
        return False
    value = bound.types.get(location.rhs)
    return (can_narrow(value, value, bound.dynamic)
            and not machine(value)
            and not machine(bound.types.get(location.target)))


def clip(graph, erased, bound):
    """Return active edges and cells made dynamic directly by erasure."""
    edges = set(graph.edges)
    dynamic = set()
    for location in graph.annotation_locations:
        if location.annotation not in erased:
            continue
        if def_is_typed(location):
            continue
        if rhs_narrows(location, bound):
            edges.add(Edge(graph.cell(location.rhs, TYPE),
                           graph.cell(location.annotation, TYPE)))
            continue
        dynamic.update((graph.cell(location.annotation, TYPE),
                        graph.cell(location.annotation, CONTEXT)))
        if location.target is not None:
            dynamic.update((graph.cell(location.target, TYPE),
                            graph.cell(location.target, CONTEXT)))
    return SimpleNamespace(edges=edges, dynamic=dynamic)

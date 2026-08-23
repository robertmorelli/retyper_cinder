"""Classify the initial state left by annotation erasure."""
import ast

from ..annotation_remover import _checked_ctor
from .graph_topology import machine


def survives_erasure(graph, node, bound):
    """Does this annotation's target keep a type once the annotation goes?

    Mirrors type_binder.py: narrowing lives in type_state.local_types,
    which is per scope, so it only reaches a read in the scope that
    assigned it.
    """
    if type(node) is not ast.AnnAssign or node.value is None:
        return False   # a parameter, a return, a bare `x: int64`
    if type(node.target) is not ast.Name:
        return False   # visitAttribute goes through the class slot
    if graph.owners.get(node) is None:
        return False   # a global read in a function uses the declaration
    if _checked_ctor(node.annotation, node.value) is not None:
        return True
    if not narrows(bound.types.get(node.value), bound):
        return False   # maybe_set_local_type: a DYNAMIC value never narrows
    # can_be_narrowed is False on CType: a primitive must box, not recover
    return (not machine(bound.types.get(node.value))
            and not machine(bound.types.get(node.target)))

def narrows(value, bound):
    """A value narrows the name it is assigned to unless it is dynamic.

    `maybe_set_local_type` falls back to the declared type when the value
    is DYNAMIC, and the declared type is what erasure just removed.
    """
    if value is None or value is bound.dynamic:
        return False
    try:
        return value.klass.can_be_narrowed
    except Exception:
        return True

def classify(graph, erased, bound):
    """Split the erased annotations by what erasure leaves them with.

    rebuilt   annotation_remover puts the type back, whatever the mask did --
              a checked container built from a literal. Never dynamic.
    recovers  cinderx re-infers it from its initializer, but only while
              that initializer still has a type, so it joins the fixpoint
              and the #65 edge can still kill it.
    seeds     nothing to infer from: a parameter, a return, a bare
              `x: int64`. Dynamic outright.
    """
    rebuilt, recovers, seeds = set(), set(), set()
    for node in erased:
        if not (type(node) in (ast.AnnAssign, ast.arg)
                or getattr(node, "returns", None) is not None):
            # A unit holds everything an annotation is linked to, not only
            # the annotations. Erasing it takes the annotations away and
            # leaves the rest to be decided from what still feeds them: a
            # loop target reads its container whether or not it shares a
            # unit with something erased.
            continue
        if (type(node) is ast.AnnAssign
                and _checked_ctor(node.annotation, node.value) is not None):
            rebuilt.add(node)
        elif survives_erasure(graph, node, bound):
            recovers.add(node)
        else:
            seeds.add(node)
    # Reads hang off the target, not the declaration, so a seeded
    # declaration has to take its target down with it.
    for node in list(seeds):
        if type(node) is ast.AnnAssign and type(node.target) in (
                ast.Name, ast.Attribute):
            seeds.add(node.target)
    return rebuilt, recovers, seeds

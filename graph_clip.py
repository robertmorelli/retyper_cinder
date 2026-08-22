"""Which edges a mask leaves standing, and what it kills outright.

The only module that reads an erasure against the graph. It adds nothing and
decides nothing: `clip` builds the adjacency the mask leaves, and `classify`
sorts the erased annotations by what erasure leaves them with.
"""
import ast

from annotation_remover import _checked_ctor
from graph_topology import CONTEXT, TYPE, machine


def clip(graph, erased):
    """The adjacency a mask leaves, built rather than filtered.

    The base is every ungated edge and is built once. A mask adds back the
    gated edges its erasure turns on -- 15% of the graph, and only the
    part of that the mask actually touches. Nothing is tested per feed.
    """
    if graph._base is None:
        base_type, base_context = {}, {}
        for edge in graph.edges:
            if edge.gate is None:
                node, slot = edge.target
                table = base_context if slot == CONTEXT else base_type
                table.setdefault(node, []).append(edge.source)
        graph._base = (base_type, base_context)
    by_type = {node: list(feeds) for node, feeds in graph._base[0].items()}
    by_context = {node: list(feeds)
                  for node, feeds in graph._base[1].items()}
    for gate in erased:
        for edge in graph.by_gate.get(gate, ()):
            node, slot = edge.target
            table = by_context if slot == CONTEXT else by_type
            table.setdefault(node, []).append(edge.source)
    return by_type, by_context


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
        # annotation_remover rewrites `todo: CheckedList[C] = [...]` into an
        # explicit CheckedList[C]([...]) constructor, so the container
        # keeps its type through erasure and everything read out of it
        # stays typed. valid_links #71
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

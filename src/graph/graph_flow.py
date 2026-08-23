"""Activate the topology for a mask, then decide every position over it."""
import ast

from types import SimpleNamespace

from .graph_topology import CONTEXT, TYPE



def decide_type(graph, node, feeds, dead, bound):
    """Return the expression type unless one of its feeds is dead."""
    if any(part in dead for part in feeds):
        return bound.dynamic
    return bound.types.get(node)

def decide_context(graph, node, feeds, dead, bound):
    """Return the expected type unless one of its feeds is dead."""
    if any(feed in dead for feed in feeds):
        return bound.dynamic
    return bound.type_contexts.get(node)


def active_feeds(graph, erased):
    active_edges = set(graph.edges)
    for annotation in erased:
        active_edges.update(graph.predicated_edges.get(annotation, ()))
    by_type, by_context = {}, {}
    for edge in active_edges:
        node, slot = edge.target
        feeds = by_context if slot == CONTEXT else by_type
        feeds.setdefault(node, []).append(edge.source)
    return by_type, by_context


def flow(graph, erased, seeds, recovers, bound):
    by_type, by_context = active_feeds(graph, erased)
    # Every typed node is decided, not only the ones an edge points at: a
    # comparison has no incoming type edge, so restricting this to edge
    # targets meant its rule never ran and it kept a cbool it no longer had.
    # Recoverable declarations join them so a dead initializer can still take
    # the recovery back.
    decided_nodes = list(bound.types) + list(recovers)

    # One fixpoint over both slots: a context can now depend on another
    # context, so the two cannot be decided in sequence.
    dead = {graph.cell(node, TYPE) for node in seeds}
    dead.update(graph.cell(node, CONTEXT) for node in seeds)
    # Only a position something demands of is decided.
    context_nodes = [node for node in by_context
                     if node in bound.type_contexts and node not in seeds]
    pending = True
    while pending:
        pending = False
        for node in decided_nodes:
            cell = graph.cell(node, TYPE)
            if cell in dead:
                continue
            if decide_type(graph, node, by_type.get(node, ()), dead,
                           bound) is bound.dynamic:
                dead.add(cell)
                pending = True
        for node in context_nodes:
            cell = graph.cell(node, CONTEXT)
            if cell in dead:
                continue
            if decide_context(graph, node, by_context.get(node, ()), dead,
                              bound) is bound.dynamic:
                dead.add(cell)
                pending = True

    types = {node: (bound.dynamic if graph.cell(node, TYPE) in dead
                    else value)
             for node, value in bound.types.items()}
    for node in decided_nodes:
        if graph.cell(node, TYPE) not in dead:
            decided = decide_type(graph, node, by_type.get(node, ()), dead,
                                  bound)
            if decided is not None:
                types[node] = decided

    # Only the erased annotations themselves lose their demand. A node
    # whose type went dynamic still has whatever its consumers ask of it.
    contexts = dict(bound.type_contexts)
    for node in seeds:
        contexts[node] = bound.dynamic
    for node in context_nodes:
        contexts[node] = decide_context(graph, node, by_context[node], dead,
                                        bound)
    return SimpleNamespace(types=types, contexts=contexts)

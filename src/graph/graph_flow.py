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


def active_feeds(edges):
    by_type, by_context = {}, {}
    for edge in edges:
        node, slot = edge.target
        feeds = by_context if slot == CONTEXT else by_type
        feeds.setdefault(node, []).append(edge.source)
    return by_type, by_context


def flow(graph, edges, initial_dynamic, bound):
    by_type, by_context = active_feeds(edges)
    # Every typed node is decided, not only the ones an edge points at: a
    # comparison has no incoming type edge, so restricting this to edge
    # targets meant its rule never ran and it kept a cbool it no longer had.
    # Edge targets join the original type table so recovered declarations can
    # still be decided when the binder did not retain a type-table entry.
    decided_nodes = list(dict.fromkeys([*bound.types, *by_type]))

    # One fixpoint over both slots: a context can now depend on another
    # context, so the two cannot be decided in sequence.
    dead = set(initial_dynamic)
    # Only a position something demands of is decided.
    context_nodes = [node for node in by_context
                     if (node in bound.type_contexts
                         and graph.cell(node, CONTEXT) not in dead)]
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
    for node, slot in initial_dynamic:
        if slot == CONTEXT:
            contexts[node] = bound.dynamic
    for node in context_nodes:
        contexts[node] = decide_context(graph, node, by_context[node], dead,
                                        bound)
    return SimpleNamespace(types=types, contexts=contexts)

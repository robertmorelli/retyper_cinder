"""Deciding what every position is, over the edges the clip left.

Reads the topology and writes nothing to it. No edge is added, dropped or
retargeted here; the mask is not visible at all, only the adjacency and the
seeds the clip produced from it.
"""
import ast

from types import SimpleNamespace

from graph_topology import CONTEXT, TYPE



def decide_type(graph, node, feeds, dead, bound):
    """What this expression yields.

    An AND over its feeds, and nothing else. What a boolean operator or a
    comparison depends on is its operands' contexts rather than their types,
    drawn as edges in the topology; and the intrinsics no longer need an
    exception here, because #20 no longer draws the edges it existed to undo.
    No rule for a machine constant either -- struck #70, see the notes.
    """
    if any(part in dead for part in feeds):
        return bound.dynamic
    return bound.types.get(node)

def decide_context(graph, node, feeds, dead, bound):
    """What is expected here. One dead feed is enough.

    An OR over live demands is the honest reading and costs 38 failures;
    see valid_bindings_notes.md, "Rules that were tried and measured wrong".

    There was a second clause, for a condition whose type died while its
    demand was still a machine boolean (valid_links #35 and #36). Nothing
    demands anything of a test, so a test has no incoming context edge, so a
    test is never in `context_nodes` and never reaches this function at all --
    0 arrivals over the 3184 masks of test.py's plan. What the clause was for
    is `is_test` in the mediator, which takes the `cbool` off a test at the
    position that knows it is one.
    """
    if any(feed in dead for feed in feeds):
        return bound.dynamic
    return bound.type_contexts.get(node)


def flow(graph, by_type, by_context, seeds, recovers, bound):
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


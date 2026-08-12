"""A graph-only error oracle: settle with pins, propagate transitively, score.

The search in global_search.py asks CinderX for every branch. A bind costs
~15ms and answers with one error; the graph answers in under 1ms and knows all
of them. This module is the graph side of that trade.

Four pieces, in dependency order:

`pinned_settle`   Graph.settle, plus a `pinned` map saying "a wrapper here now
                  yields T". Settle propagates *deadness*, not type identity -
                  decide_type reads only whether a part is dead - so a pin is
                  exactly a node held out of `dead`. The fixpoint still only
                  ever adds to `dead`, so it still terminates.

`full_propagate`  Graph.propagate chases one hop. This chases the whole result
                  chain from a work set. Type identity, unlike deadness, is
                  what a wrapper actually changes downstream.

`reach_index`     For each mismatch, the wrappable positions that can reach it;
                  inverted, the count of mismatches each position reaches.

`lower_bound`     Mismatches whose candidate sets are pairwise disjoint need
                  one wrapper each. Read off the same index.

Nothing here edits typedness_graph.py; every rule is called through the
Graph's own methods so the two cannot drift apart silently.
"""
from __future__ import annotations

import sys
from collections import deque
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(HERE)]

from typedness_graph import CONTEXT, TYPE
from print_instances import wrappable


# --------------------------------------------------------------- 1. settle

def pinned_settle(graph, bound, erased=(), pinned=None):
    """Graph.settle, with positions whose type a wrapper has fixed.

    With `pinned` empty this reproduces Graph.settle exactly; `check_reference`
    below asserts that on every program it is run against, which is what keeps
    this copy honest as the real rule changes.
    """
    pinned = pinned or {}
    by_type, by_context = graph.sources()
    _, recovers, seeds = graph.classify(erased, bound)
    decided_nodes = list(bound.types) + list(recovers)

    # A pinned node yields its wrapper's type, so erasure cannot kill it and
    # the fixpoint must not reconsider it.
    dead = {node for node in seeds if node not in pinned}
    pending = True
    while pending:
        pending = False
        for node in decided_nodes:
            if node in dead or node in pinned:
                continue
            if graph.decide_type(node, by_type.get(node, ()), dead,
                                 bound) is bound.dynamic:
                dead.add(node)
                pending = True

    types = {node: (bound.dynamic if node in dead else value)
             for node, value in bound.types.items()}
    for node in decided_nodes:
        if node in dead or node in pinned:
            continue
        decided = graph.decide_type(node, by_type.get(node, ()), dead, bound)
        if decided is not None:
            types[node] = decided
    types.update(pinned)

    contexts = dict(bound.type_contexts)
    for node in seeds:
        if node not in pinned:
            contexts[node] = bound.dynamic
    for node, feeds in by_context.items():
        if node in contexts and node not in seeds:
            contexts[node] = graph.decide_context(node, feeds, dead, bound)

    return SimpleNamespace(types=types, contexts=contexts, dead=dead)


def check_reference(graph, bound, erased):
    """pinned_settle with no pins must be Graph.settle. Differential test."""
    reference = graph.settle(bound, erased)
    ours = pinned_settle(graph, bound, erased)
    for name in ("types", "contexts"):
        mine, theirs = getattr(ours, name), getattr(reference, name)
        if mine.keys() != theirs.keys():
            return f"{name}: key sets differ"
        for node in theirs:
            if mine[node] is not theirs[node]:
                line = getattr(node, "lineno", "?")
                return f"{name}: line {line} {type(node).__name__} differs"
    return None


# ------------------------------------------------------------ 2. propagate

def full_propagate(graph, seeds, types, contexts, max_visits=200_000):
    """Chase a patched position's new type down the whole result chain.

    Graph.propagate stops after one hop, which is all the type mediator needs: it
    walks the tree itself and will reach the next position on its own. A search
    that asks "what does this one wrapper change" has no such walk, so the
    consequences have to be chased here.

    Only result edges recur. A context edge states a demand, and a demand does
    not itself yield a value to carry further. Re-queueing only on an actual
    change is what stops a `must_agree` pair from trading updates forever; the
    visit cap is the backstop, and a caller that hits it is told so.
    """
    result_edges = graph.tagged["result"]
    outgoing = graph.outgoing()
    work = deque(seeds.items())
    visits = 0
    changed = set()
    while work:
        if visits >= max_visits:
            return SimpleNamespace(visits=visits, changed=changed,
                                   exhausted=True)
        node, produced = work.popleft()
        visits += 1
        source = graph.cell(node, TYPE)
        for target in outgoing.get(source, ()):
            where, slot = target
            if slot == CONTEXT:
                if contexts.get(where) is not produced:
                    contexts[where] = produced
                    changed.add(where)
            elif (source, target) in result_edges:
                if types.get(where) is not produced:
                    types[where] = produced
                    changed.add(where)
                    work.append((where, produced))
    return SimpleNamespace(visits=visits, changed=changed, exhausted=False)


def settle_with_patches(graph, bound, erased, patches):
    """The settled state after a set of wrappers: pins, then consequences.

    Deadness and type identity travel differently, so both passes are needed.
    `pinned_settle` says which positions erasure no longer kills; the propagate
    pass carries the wrappers' actual types down the result chains.
    """
    state = pinned_settle(graph, bound, erased, patches)
    flow = full_propagate(graph, dict(patches), state.types, state.contexts)
    state.propagation = flow
    return state


# ---------------------------------------------------------------- 3. index

class ReachIndex:
    """Which positions can reach which mismatches, and how many each reaches.

    `reaches` is optimistic on purpose: an edge path means a position can
    *influence* a mismatch, not that any wrapper available there repairs it.
    That is the right bias for ordering - it never hides a real repair - and
    the reason nothing here may be used to prune. The lower bound below reads
    the same table in the sound direction: two mismatches sharing no position
    at all cannot be fixed by one wrapper.
    """

    def __init__(self, candidates_of):
        self.candidates_of = candidates_of              # mismatch -> positions
        self.reaches = {}                               # position -> mismatches
        for mismatch, positions in candidates_of.items():
            for position in positions:
                self.reaches.setdefault(position, set()).add(mismatch)

    def score(self, position, uncovered=None):
        """How many still-open mismatches this position could influence."""
        hit = self.reaches.get(position, ())
        if uncovered is None:
            return len(hit)
        return sum(1 for mismatch in hit if mismatch in uncovered)

    def unreachable(self):
        """Mismatches no wrappable position reaches: the graph offers no fix."""
        return {m for m, positions in self.candidates_of.items() if not positions}

    def lower_bound(self, uncovered=None):
        """Pairwise-disjoint mismatches, each needing a wrapper of its own.

        Greedy over the smallest candidate sets first, which is the ordering
        that lets the most later mismatches still be disjoint from what has
        been taken. Mismatches with no candidate at all are left out: they are
        a modelling gap, not a unit of cost, and counting them would inflate a
        bound the search is allowed to prune on.
        """
        pool = self.candidates_of if uncovered is None else {
            m: c for m, c in self.candidates_of.items() if m in uncovered}
        order = sorted((m for m, c in pool.items() if c),
                       key=lambda m: len(pool[m]))
        taken, used = [], set()
        for mismatch in order:
            if not (pool[mismatch] & used):
                taken.append(mismatch)
                used |= pool[mismatch]
        return len(taken), taken


def reverse_slices(graph, mismatches):
    """Every wrappable position on a directed path into each mismatch.

    Same walk candidate_sets does, kept here so the index can be rebuilt at
    each search node against a changed mismatch set without re-deriving the
    reverse edge map every time.
    """
    reverse = {}
    for edge in graph.edges:
        reverse.setdefault(edge.target, set()).add(edge.source)

    result = {}
    for node in mismatches:
        pending = [(node, TYPE), (node, CONTEXT)]
        seen = set(pending)
        positions = {node} if wrappable(node) else set()
        while pending:
            cell = pending.pop()
            for predecessor in reverse.get(cell, ()):
                if wrappable(predecessor[0]):
                    positions.add(predecessor[0])
                if predecessor not in seen:
                    seen.add(predecessor)
                    pending.append(predecessor)
        result[node] = positions
    return result


def build_index(graph, mismatches):
    return ReachIndex(reverse_slices(graph, mismatches))

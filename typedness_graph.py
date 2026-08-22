"""The typedness graph, in three phases that may not do each other's work.

  graph_topology   what links to what, and which links a mask can turn on.
                   Mask-unaware from top to bottom, and the only module that
                   creates an edge.
  graph_clip       the adjacency a mask leaves, and which annotations erasure
                   kills outright. The only module that reads an erasure.
  graph_flow       what every position becomes over that adjacency. Reads the
                   topology and writes nothing to it; it never sees the mask,
                   only the edges and seeds the clip handed it.

`settle` is the composition, and is all a caller needs.
"""
from graph_clip import classify, clip
from graph_flow import flow
from graph_topology import CONTEXT, TYPE, Edge, Topology, UnionFind, machine


class Graph(Topology):
    def settle(self, bound, erased=()):
        erased = set(erased)
        by_type, by_context = clip(self, erased)
        _, recovers, seeds = classify(self, erased, bound)
        return flow(self, by_type, by_context, seeds, recovers, bound)


def build_binding_graph(bound):
    return Graph(bound)

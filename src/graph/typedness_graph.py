"""Build, clip, and flow typedness through the binding graph."""
from .graph_clip import classify
from .graph_flow import flow as propagate
from .graph_topology import CONTEXT, TYPE, Edge, Topology, UnionFind, machine


class Graph(Topology):
    def flow(self, bound, erased=()):
        erased = set(erased)
        _, recovers, seeds = classify(self, erased, bound)
        return propagate(self, erased, seeds, recovers, bound)


def build_binding_graph(bound):
    return Graph(bound)

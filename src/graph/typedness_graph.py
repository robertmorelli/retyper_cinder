"""Build, clip, and flow typedness through the binding graph."""
from .graph_clip import clip
from .graph_flow import flow as propagate
from .graph_topology import CONTEXT, TYPE, Edge, Topology, UnionFind, machine


class Graph(Topology):
    def flow(self, bound, erased=()):
        erased = set(erased)
        clipped = clip(self, erased, bound)
        selected = set()
        while True:
            predicted = propagate(self, clipped.edges | selected,
                                  clipped.dynamic, bound)
            changed = {
                (choice.narrowing_edge
                 if (predicted.types.get(choice.value) is not bound.dynamic
                     or choice.annotation in erased)
                 else choice.declared_edge)
                for choice in self.narrowing_choices
            }
            if changed == selected:
                self.edges = clipped.edges | selected
                self._index = None
                predicted.active_edges = self.edges
                predicted.selected_edges = selected
                return predicted
            selected = changed


def build_binding_graph(bound):
    return Graph(bound)

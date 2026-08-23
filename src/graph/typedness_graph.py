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
                Edge(self.cell(value if (predicted.types.get(value)
                                         is not bound.dynamic
                                         or declaration in erased)
                               else fallback, TYPE),
                     self.cell(target, TYPE))
                for value, fallback, target, declaration
                in self.narrowing_choices
            }
            if changed == selected:
                predicted.active_edges = clipped.edges
                predicted.selected_edges = selected
                return predicted
            selected = changed


def build_binding_graph(bound):
    return Graph(bound)

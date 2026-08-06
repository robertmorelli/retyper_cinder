"""The one type/context graph.

This module is deliberately blind: it stores labelled vertices and edges and
builds indexes over them. Syntax-specific hooking lives in `graph_rules`; source
selection, settlement, and coercion choice live in their consumers.
"""
from collections import defaultdict
from dataclasses import dataclass

TYPE, CTX = False, True

VALUE = "value"
ELEM = "elem"
RETURN = "return"
RECEIVER = "receiver"
FIELD = "field"
BINOP = "binop"
CMPOP = "cmpop"
UNARY = "unary"
BRANCH = "branch"
COERCE = "coerce"
ELTS = "elts"
CONTEXT = "context"
ELEM_CONTEXT = "elem_context"
DYNAMIC_CONTEXT = "dynamic_context"
SELF_CONTEXT = "self_context"
NUMERIC_CONTEXT = "numeric_context"
COMPARE_CONTEXT = "compare_context"
BOOL_CONTEXT = "bool_context"
RECEIVER_CONTEXT = "receiver_context"

COERCIONS = ("cast", "box", "int64", "cbool", "clen", "double")


@dataclass(frozen=True)
class FixedSource:
    kind: str
    owner: object = None
    detail: object = None


@dataclass(frozen=True)
class Edge:
    src: tuple
    dst: tuple
    role: str
    detail: object = None


@dataclass(frozen=True)
class Rule:
    output: object
    inputs: tuple


@dataclass(frozen=True)
class Demand:
    value: object
    source: tuple
    role: str
    detail: object = None


@dataclass(frozen=True)
class ErasureUnit:
    id: int
    nodes: frozenset


class BindingGraph:
    """One edge list and cheap indexes/views over it."""

    def __init__(self, edges, source_values=None):
        self.edges = list(dict.fromkeys(edges))
        self.source_values = dict(source_values or {})
        self.outgoing = defaultdict(list)
        type_inputs = defaultdict(list)
        demands = []
        for edge in self.edges:
            self.outgoing[edge.src].append(edge)
            if edge.src[1] is TYPE and edge.dst[1] is TYPE:
                type_inputs[edge.dst[0]].append(
                    (edge.role, edge.src[0], edge.detail))
            elif edge.dst[1] is CTX:
                demands.append(Demand(edge.dst[0], edge.src,
                                      edge.role, edge.detail))
        self.rules = [Rule(output, tuple(inputs))
                      for output, inputs in type_inputs.items()]
        self.rule_for = {rule.output: rule for rule in self.rules}
        self.demands = tuple(demands)
        self.demands_for = defaultdict(list)
        for demand in demands:
            self.demands_for[demand.value].append(demand)
        self.annotation_units = []
        self.benchmark_units = []
        self.source_nodes = set()
        self.declarations = set()
        self.natural_types = {}

    @property
    def g(self):
        return {src: [edge.dst for edge in edges]
                for src, edges in self.outgoing.items()}

    def attach_units(self, roots, components, benchmark_roots):
        def expand(group):
            return frozenset(
                node for root in group
                for node in ({root} | set(components.get(root) or ())))

        self.annotation_units = [
            ErasureUnit(i, expand((root,))) for i, root in enumerate(roots)]
        self.benchmark_units = [
            ErasureUnit(i, expand(group))
            for i, group in enumerate(benchmark_roots)]
        return self

    def units(self, granularity="annotation"):
        return (self.benchmark_units if granularity == "benchmark"
                else self.annotation_units)

    def nodes_for_mask(self, mask, granularity="annotation"):
        return {
            node for unit in self.units(granularity)
            if mask & (1 << unit.id) for node in unit.nodes
        }

    def settle(self, bound, erased=()):
        from graph_solve import settle_tables
        return settle_tables(bound, self, erased)

    def coercion(self, node):
        rule = self.rule_for.get(node)
        for role, source, kind in (() if rule is None else rule.inputs):
            if role == COERCE:
                return kind, source
        return None


def build_binding_graph(bound):
    """Hook the bound facts into a graph; make no settlement decisions."""
    from ast import walk
    from parent_pointers import build_parents
    from graph_rules import build_graph

    return build_graph(
        bound.tree, build_parents(bound.tree), bound.types,
        bound.reverse_outflow, tuple(walk(bound.tree)), bound.resolved_from,
        bound.type_contexts, bound.dynamic, bound.roots, bound.components,
        bound.benchmark_roots, bound.iteration_types,
    )

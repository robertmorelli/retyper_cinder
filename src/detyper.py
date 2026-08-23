"""Erase selected annotations and repair the resulting Static Python AST."""
from ast import fix_missing_locations, parse

from .annotation_remover import remove_annotations
from .cinderx_binding import get_ast_data
from .graph.typedness_graph import build_binding_graph
from .import_adder import add_imports
from .type_mediator import coerce_tree


def detype(source, mask=-1, bench=False):
    written = get_ast_data(parse(source))
    graph = build_binding_graph(written)
    granularity = "benchmark" if bench else "annotation"
    effective = ((1 << len(graph.units(granularity))) - 1
                 if mask == -1 else mask)
    erased = graph.nodes_for_mask(effective, granularity)
    predicted = graph.flow(written, erased)

    tree = remove_annotations(written.tree, erased, predicted.types,
                              predicted.contexts)
    tree = coerce_tree(tree, predicted.types, predicted.contexts,
                       written.dynamic, written.valid_pair,
                       written.reverse_outflow, graph)
    tree = add_imports(tree)
    return fix_missing_locations(tree)

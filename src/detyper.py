"""Erase selected annotations and repair the resulting Static Python AST."""
from ast import fix_missing_locations, parse

from .annotation_remover import remove_annotations
from .cinderx_binding import get_ast_data
from .import_adder import add_imports
from .mediate_ast import mediate_tree
from .type_graph import TypeGraph


def detype(source, mask=-1, bench=False):
    written = get_ast_data(parse(source))
    granularity = "benchmark" if bench else "annotation"
    graph = TypeGraph(written, mask, granularity)

    tree = remove_annotations(written.tree, graph.erased, graph.types,
                              graph.type_constraints)
    tree = mediate_tree(tree, graph.types, graph.type_constraints,
                       written.dynamic, written.valid_pair,
                       written.inline_calls, graph)
    tree = add_imports(tree)
    return fix_missing_locations(tree)

from ast import parse
from cinderx_binding import get_ast_data
from typedness_graph import build_binding_graph

def graph(source):
    return build_binding_graph(get_ast_data(parse(source)))

def count_roots(source):
    return len(graph(source).units())

def count_bench_roots(source):
    return len(graph(source).units("benchmark"))

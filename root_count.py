from ast import parse
from get_ast_data import get_ast_data

def count_roots(source):
    return len(get_ast_data(parse(source)).roots)

def count_bench_roots(source):
    return len(get_ast_data(parse(source)).benchmark_roots)

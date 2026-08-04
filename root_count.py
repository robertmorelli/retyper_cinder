from ast import parse
from get_ast_data import get_ast_data

def count_roots(source):
    roots, *_ = get_ast_data(parse(source))
    return len(roots)

def count_bench_roots(source):
    *_, bench_roots = get_ast_data(parse(source))
    return len(bench_roots)

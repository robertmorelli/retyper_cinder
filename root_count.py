from get_ast_data import get_ast_data

def count_roots(source):
    roots, *_ = get_ast_data(source)
    return len(roots)

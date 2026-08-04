from ast import get_source_segment, parse
from sys import argv
from get_ast_data import get_ast_data, is_const
from load_source import load_bench

source = load_bench(argv[1], argv[2])

roots, *_ = get_ast_data(parse(source))

print("\n".join([get_source_segment(source, root) for root in roots]))
# print("\n".join([str(root) for root in roots]))
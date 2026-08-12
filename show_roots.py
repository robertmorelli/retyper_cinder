from ast import get_source_segment, parse
from sys import argv
from cinderx_binding import get_ast_data
from load_source import load_bench
from typedness_graph import build_binding_graph

source = load_bench(argv[1], argv[2])

roots = build_binding_graph(get_ast_data(parse(source))).roots

print("\n".join([get_source_segment(source, root) for root in roots]))
# print("\n".join([str(root) for root in roots]))
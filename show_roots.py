from ast import get_source_segment
from json import load
from sys import argv
from get_ast_data import get_ast_data, is_const

with open("data/benchmark_locations.json") as f:
    sources = load(f)

path = sources[argv[1]][argv[2]]
with open(path) as f:
    source = f.read()

roots, *_ = get_ast_data(source)

print("\n".join([get_source_segment(source, root) for root in roots]))
# print("\n".join([str(root) for root in roots]))
import ast
import json
import sys
from get_ast_data import get_ast_data, is_const

with open("data/benchmark_locations.json") as f:
    sources = json.load(f)

path = sources[sys.argv[1]][sys.argv[2]]
with open(path) as f:
    source = f.read()

roots, *_ = get_ast_data(source)
print(len(roots))

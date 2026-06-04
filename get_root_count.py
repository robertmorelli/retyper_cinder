import ast
import json
import sys
from get_ast_data import get_ast_data
from load_source import load_bench

source = load_bench(sys.argv[1], sys.argv[2])
roots, *_ = get_ast_data(source)
print(len(roots))

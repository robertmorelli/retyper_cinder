import ast
import json
import sys

sys.path.insert(0, "cinderx/cinderx/PythonLib")

from cinderx.compiler.static.compiler import Compiler, Class
from cinderx.compiler.static import StaticCodeGenerator


with open("data/benchmark_locations.json") as f:
    sources = json.load(f)

path = sources[sys.argv[1]][sys.argv[2]]
with open(path) as f:
    source = f.read()
#     source = '''
# from __static__ import int64, cbool, cast

# class baz:
#     def __init__(self):
#         self.foobar: cbool = False

# def foo(y: int64):
#     x: int64 = 0
#     z: baz = baz()#cast(bar.baz, baz())

# def bar():
#     foo(1)
#     '''

tree = ast.parse(source)
compiler = Compiler(StaticCodeGenerator)

compiler.bind("", "", tree, source, optimize=0)
module = compiler.modules[""]

def node_repr(node):
    source_type = module.node_value[node]
    target_type = module.node_ctx_value[node]
    source_type_string = source_type.klass.type_name.readable_name
    target_type_string = target_type.klass.type_name.readable_name
    expr = " ".join(ast.get_source_segment(source, node).split())
    return (
        f"expr={expr:<60}"
        f"type={source_type_string if not isinstance(node, ast.Constant) else "const":<40} "
        f"ctx={target_type_string:<40}"
    )

out = ""
for key in module.node_value.keys():
    out += node_repr(key)

print(len(out))
import ast
import json
import sys

sys.path.insert(0, "cinderx/cinderx/PythonLib")

from cinderx.compiler.static.compiler import Compiler
from cinderx.compiler.static import StaticCodeGenerator
from cinderx.compiler.static.types import CType

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
types = module.node_value; type_ctxs = module.node_ctx_value

def is_const(node):
    return isinstance(node, ast.Constant)

def is_primative(node):
    return isinstance(node.klass, CType)

def valid_pair(t, tc):
    dest, src = tc.klass, t.klass
    can_assign_basic = dest.can_assign_from(src)
    dyn_and_dyn_ok = src is module.compiler.type_env.dynamic and not is_primative(tc)
    return can_assign_basic or dyn_and_dyn_ok

# clean contexts. not sure this is completely chill
for node in module.node_value.keys():
    if not valid_pair(types[node], type_ctxs[node]):
        type_ctxs[node] = types[node]

def node_repr(node):
    type = types[node]
    type_ctx = type_ctxs[node]
    type_string_proto = type.klass.type_name.readable_name
    type_string = type_string_proto if not is_const(node) else "const"
    type_ctx_string = type_ctx.klass.type_name.readable_name
    expr = " ".join(ast.get_source_segment(source, node).split())
    return (
        f"expr={expr:<40}"
        f"type={type_string:<30} "
        f"ctx={type_ctx_string:<30}"
    )

out = []
for key in module.node_value.keys():
    out.append(node_repr(key))

print("\n".join(out))
import ast
import json
import sys

sys.path.insert(0, "_cinderx/cinderx/PythonLib")

from cinderx.compiler.static.compiler import Compiler
from cinderx.compiler.static import StaticCodeGenerator
from cinderx.compiler.static.types import CType
from cinderx.compiler.symbols import SymbolVisitor
from cinderx.compiler.static.type_binder import TypeBinder

with open("data/benchmark_locations.json") as f:
    sources = json.load(f)

path = sources[sys.argv[1]][sys.argv[2]]
with open(path) as f:
    source = f.read()

tree = ast.parse(source)
compiler = Compiler(StaticCodeGenerator)

compiler.bind("", "", tree, source, optimize=0)
symbols = StaticCodeGenerator._SymbolVisitor(0)
symbols.visit(tree)
binder = TypeBinder(symbols, "", compiler, "", optimize=0)
module = compiler.modules[""]
types = module.expr_types; type_ctxs = module.expr_ctx_types; components = module.components

def is_const(node):
    return isinstance(node, ast.Constant)

def is_primative(node):
    return isinstance(node.klass, CType)

def valid_pair(t, tc, node):
    try:
        binder.check_can_assign_from(tc.klass, t.klass, node)
        return True
    except Exception:
        return False

# clean contexts. not sure this is completely chill
for node in module.expr_types.keys():
    if not valid_pair(types[node], type_ctxs[node], node):
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
for key in module.expr_types.keys():
    out.append(node_repr(key))

print("\n".join(out))

def print_links(kind, table):
    for decl, uses in table.items():
        for use in uses:
            if decl not in components: continue
            print()
            for node in list(components.get(decl) or set()) + [decl]:
                print(f"from: {" ".join(ast.get_source_segment(source, node).split())}")
            print(f"{kind}: {" ".join(ast.get_source_segment(source, use).split())}")


print_links("read", module.reads)
print_links("write", module.writes)

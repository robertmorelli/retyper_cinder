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

# for convenience
types = module.expr_types
type_ctxs = module.expr_ctx_types
components = module.components
reads = module.reads
writes = module.writes

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

def link_repr(kind, decl, uses):
    out = []
    for use in uses:
        for node in list(components.get(decl) or set()) + [decl]:
            out.append(f"from: {" ".join(ast.get_source_segment(source, node).split())}")
        out.append(f"{kind}: {" ".join(ast.get_source_segment(source, use).split())}")
        out.append("")
    return "\n".join(out)

# clean contexts. not sure this is completely chill
for node in types.keys():
    if not valid_pair(types[node], type_ctxs[node], node):
        type_ctxs[node] = types[node]

expr_types = [node_repr(key) for key in types.keys()]
linked_reads = [link_repr("read", decl, uses) for decl, uses in reads.items()]
linked_writes = [link_repr("write", decl, uses) for decl, uses in writes.items()]

print("\n-- expr type/ctx --")
print("\n".join(expr_types))
print("\n-- uses --")
print("\n".join(linked_reads))
print("\n".join(linked_writes))
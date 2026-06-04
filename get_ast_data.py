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


def is_const(node):
    return isinstance(node, ast.Constant)

def is_primative(node):
    return isinstance(node.klass, CType)

def get_ast_data(source):
    proto_tree = ast.parse(source)
    compiler = Compiler(StaticCodeGenerator)

    compiler.bind("", "", proto_tree, source, optimize=0)
    tree = compiler.ast_cache.get(source)
    symbols = StaticCodeGenerator._SymbolVisitor(0)
    symbols.visit(tree)
    module = compiler.modules[""]
    binder = TypeBinder(symbols, "", compiler, "", optimize=0)
    def valid_pair(t, tc, node):
        try:
            binder.check_can_assign_from(tc.klass, t.klass, node)
            return True
        except Exception:
            return False

    # for convenience
    types = module.expr_types
    type_ctxs = module.expr_ctx_types
    components = module.components
    reads = module.reads
    writes = module.writes


    # clean contexts. not sure this is completely chill
    for node in types.keys():
        if not valid_pair(types[node], type_ctxs[node], node):
            type_ctxs[node] = types[node]


    roots = set()
    all_seen = set()

    all_roots = sorted([*reads.keys(), *writes.keys(), *components.keys()], key=lambda e: str(e))

    for root in all_roots:
        if root in all_seen: continue
        all_seen |= (components.get(root) or set()) | set([root])
        roots.add(root)

    dyn = compiler.type_env.DYNAMIC
    return roots, types, type_ctxs, components, reads, writes, valid_pair, tree, dyn
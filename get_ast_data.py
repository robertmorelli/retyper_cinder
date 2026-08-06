from ast import Constant, parse, Attribute, Subscript, Name, Starred, List, Tuple, AnnAssign, arg, FunctionDef, AsyncFunctionDef
from sys import path
from parent_pointers import build_parents
from component_reflow import reflow
from fun_grouping import group_roots
from binding_data import BoundData

path.insert(0, "_cinderx/cinderx/PythonLib")

from cinderx.compiler.static.compiler import Compiler
from cinderx.compiler.static import StaticCodeGenerator
from cinderx.compiler.static.types import CType
from cinderx.compiler.symbols import SymbolVisitor
from cinderx.compiler.static.type_binder import TypeBinder


def get_ctx(node):
    if isinstance(node, (Name, Attribute, Subscript, Starred, List, Tuple)):
        return node.ctx
    return None

def is_const(node):
    return isinstance(node, Constant)

def is_primative(node):
    return isinstance(node.klass, CType)

def get_ast_data(proto_tree):
    compiler = Compiler(StaticCodeGenerator)

    # the tree doubles as its own cache key; ast_cache only needs something hashable
    compiler.bind("", "", proto_tree, proto_tree, optimize=0)
    tree = compiler.ast_cache.get(proto_tree)
    symbols = StaticCodeGenerator._SymbolVisitor(0)
    symbols.visit(tree)
    module = compiler.modules[""]
    binder = TypeBinder(symbols, "", compiler, "", optimize=0)
    dyn = compiler.type_env.DYNAMIC

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
    outflow = module.outflow
    inflow = module.inflow
    constructors = module.constructors
    reverse_outflow = module.reverse_outflow

    parents = build_parents(tree)

    components = reflow(tree, parents, components, reverse_outflow, inflow, outflow)

    # clean contexts. not sure this is completely chill
    for node in types.keys():
        if not valid_pair(types[node], type_ctxs[node], node):
            # type_ctxs[node] = types[node]
            type_ctxs[node] = dyn

    roots = []
    all_seen = set()

    all_roots = sorted([*outflow.keys(), *inflow.keys(), *components.keys()], key=lambda e: (e.lineno, e.col_offset))

    for root in all_roots:
        if root in all_seen: continue
        all_seen |= (components.get(root) or set()) | set([root])
        roots.append(root)

    anno_roots = [r for r in roots
                  if (isinstance(r, (AnnAssign, arg)) and r.annotation is not None)
                  or (isinstance(r, (FunctionDef, AsyncFunctionDef)) and r.returns is not None)]

    bench_roots = group_roots(roots, components, parents)

    return BoundData(
        roots=roots,
        types=types,
        type_contexts=type_ctxs,
        constructors=constructors,
        components=components,
        outflow=outflow,
        inflow=inflow,
        valid_pair=valid_pair,
        tree=tree,
        dynamic=dyn,
        declared_types=module.declared_types,
        declaration_types=module.declaration_types,
        iteration_types=module.iteration_types,
        reverse_outflow=reverse_outflow,
        annotation_roots=anno_roots,
        benchmark_roots=bench_roots,
        resolved_from=module.resolved_from,
    )

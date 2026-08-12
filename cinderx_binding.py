from ast import Constant, parse, Attribute, Subscript, Name, Starred, List, Tuple, AnnAssign, arg, FunctionDef, AsyncFunctionDef, walk
from sys import path
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

    # clean contexts. not sure this is completely chill
    for node in types.keys():
        if not valid_pair(types[node], type_ctxs[node], node):
            # type_ctxs[node] = types[node]
            type_ctxs[node] = dyn

    roots = sorted(
        {node for node in {*outflow, *inflow, *components} if node is not None},
        key=lambda node: (node.lineno, node.col_offset),
    )
    # Only annotations some link mentions. A procedure -- nothing returns a
    # value from it, nothing resolves a call to it -- reaches no link, so its
    # `-> None` is the one annotation no mask can erase.
    #
    # Widening this to every annotation in the tree is not the fix, though it
    # looks like one: six held_karp masks then fail with `Literal[2] received
    # for positional arg`, because an unlinked node is exactly the node whose
    # erasure the graph cannot propagate, so the coercion pass never hears that
    # the value went dynamic and never repairs it. Reaching them means giving
    # them real edges first -- linking a resolved call to its callee whatever
    # the callee returns -- not declaring them roots without any.
    anno_roots = [
        root for root in roots
        if (isinstance(root, (AnnAssign, arg)) and root.annotation is not None)
        or (isinstance(root, (FunctionDef, AsyncFunctionDef))
            and root.returns is not None)
    ]
    bench_roots = []

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

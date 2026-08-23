from ast import (AnnAssign, AsyncFunctionDef, Attribute, Constant, FunctionDef,
                 List, Name, Starred, Subscript, Tuple, arg)
from dataclasses import dataclass
from pathlib import Path
from sys import path as import_path
from typing import Any

PYTHON_LIB = str(Path(__file__).resolve().parents[1] /
                 "_cinderx" / "cinderx" / "PythonLib")
if PYTHON_LIB not in import_path:
    import_path.insert(0, PYTHON_LIB)

from cinderx.compiler.static.compiler import Compiler
from cinderx.compiler.static import StaticCodeGenerator
from cinderx.compiler.static.types import CType
from cinderx.compiler.static.type_binder import TypeBinder


@dataclass
class BoundData:
    """Named tables and metadata produced by one CinderX bind."""

    roots: list
    types: dict
    type_contexts: dict
    constructors: Any
    components: dict
    outflow: dict
    inflow: dict
    valid_pair: Any
    tree: Any
    dynamic: Any
    declared_types: dict
    declaration_types: dict
    iteration_types: dict
    reverse_outflow: dict
    annotation_roots: list
    benchmark_roots: list
    resolved_from: dict


def get_ctx(node):
    if isinstance(node, (Name, Attribute, Subscript, Starred, List, Tuple)):
        return node.ctx
    return None

def is_const(node):
    return isinstance(node, Constant)

def is_primative(node):
    return isinstance(node.klass, CType)

def can_narrow(declared, assigned, dynamic):
    """Whether CinderX keeps an assigned type instead of the declaration."""
    return (assigned is not None and assigned is not dynamic
            and declared is not None and declared.klass.can_be_narrowed)

def get_ast_data(proto_tree):
    compiler = Compiler(StaticCodeGenerator)

    # The tree is also the hashable cache key.
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

    types = module.expr_types
    type_ctxs = module.expr_ctx_types
    components = module.components
    outflow = module.outflow
    inflow = module.inflow
    constructors = module.constructors
    reverse_outflow = module.reverse_outflow

    # Replace contexts CinderX itself rejects with dynamic.
    for node in types.keys():
        if not valid_pair(types[node], type_ctxs[node], node):
            type_ctxs[node] = dyn

    roots = sorted(
        {node for node in {*outflow, *inflow, *components} if node is not None},
        key=lambda node: (node.lineno, node.col_offset),
    )
    # Only linked annotations are safe roots; unlinked erasure cannot propagate.
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

import ast
from ast import Call
from dataclasses import dataclass
from pathlib import Path
from sys import path as import_path
from typing import Any, Optional

PYTHON_LIB = str(Path(__file__).resolve().parents[1] /
                 "_cinderx" / "cinderx" / "PythonLib")
if PYTHON_LIB not in import_path:
    import_path.insert(0, PYTHON_LIB)

from cinderx.compiler.static.compiler import Compiler
from cinderx.compiler.static import StaticCodeGenerator
from cinderx.compiler.static.types import DecoratedMethod, Function, InlinedCall
from cinderx.compiler.static.type_binder import TypeBinder


@dataclass
class BoundData:
    """Named tables and metadata produced by one CinderX bind."""

    types: dict
    type_constraints: dict
    components: dict
    outflow: dict
    inflow: dict
    valid_pair: Any
    tree: Any
    dynamic: Any
    reverse_outflow: dict
    resolved_from: dict
    assignment_declarations: dict
    inline_functions: set
    inline_calls: set


def bind_tree(proto_tree):
    compiler = Compiler(StaticCodeGenerator)
    compiler.bind("", "", proto_tree, proto_tree, optimize=0)
    tree = compiler.ast_cache.get(proto_tree)
    symbols = StaticCodeGenerator._SymbolVisitor(0)
    symbols.visit(tree)
    module = compiler.modules[""]
    binder = TypeBinder(symbols, "", compiler, "", optimize=0)
    return tree, module, binder, compiler.type_env.DYNAMIC


def assignment_validator(binder):
    def valid_pair(value_type, type_constraint, node):
        try:
            binder.check_can_assign_from(
                type_constraint.klass, value_type.klass, node
            )
            return True
        except Exception:
            return False

    return valid_pair


def accepted_type_constraints(types, constraints, dynamic, valid_pair):
    for node, value_type in types.items():
        if not valid_pair(value_type, constraints[node], node):
            constraints[node] = dynamic
    return constraints


def find_inline_functions(module):
    found = set()
    for node, value in module.types.items():
        function = (
            value.real_function if isinstance(value, DecoratedMethod) else value
        )
        if isinstance(function, Function) and function.inline:
            found.add(node)
    return found


def find_inline_calls(tree, module):
    return {
        node
        for node in ast.walk(tree)
        if isinstance(node, Call)
        and module.get_opt_node_data(node, Optional[InlinedCall]) is not None
    }


def get_ast_data(proto_tree):
    tree, module, binder, dynamic = bind_tree(proto_tree)
    valid_pair = assignment_validator(binder)
    type_constraints = accepted_type_constraints(
        module.expr_types,
        module.expr_ctx_types,
        dynamic,
        valid_pair,
    )
    inline_functions = find_inline_functions(module)
    inline_calls = find_inline_calls(tree, module)

    return BoundData(
        types=module.expr_types,
        type_constraints=type_constraints,
        components=module.components,
        outflow=module.outflow,
        inflow=module.inflow,
        valid_pair=valid_pair,
        tree=tree,
        dynamic=dynamic,
        reverse_outflow=module.reverse_outflow,
        resolved_from=module.resolved_from,
        assignment_declarations=module.assignment_declarations,
        inline_functions=inline_functions,
        inline_calls=inline_calls,
    )

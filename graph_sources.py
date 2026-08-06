"""Source values for counterfactual graph replay.

This module answers only which recorded values enter the graph. It does not
propagate types or choose contexts/coercions.
"""
from ast import (AnnAssign, arg, BoolOp, Call, ClassDef, Compare, Constant,
                 Dict, DictComp, FunctionDef, AsyncFunctionDef, GeneratorExp,
                 If, Is, IsNot, List, ListComp, Load, Name, Not, Raise, Return,
                 Continue, Break, Set, SetComp, Slice, Tuple, UnaryOp, And,
                 walk)

from cinderx.compiler.static.types import CType

from graph_construction import (TYPE, COERCIONS, NUMERIC_CONTEXT,
                                DYNAMIC_CONTEXT, SELF_CONTEXT, FixedSource)
from graph_rules import callee_params, class_defs
from parent_pointers import build_parents

CONTAINERS = (List, Tuple, Set, Dict, ListComp, SetComp, GeneratorExp, DictComp)
NATURAL = {bool: "bool", int: "int", float: "float", str: "str", bytes: "bytes"}


def _guarded_name(test):
    if (isinstance(test, Call) and isinstance(test.func, Name)
            and test.func.id == "isinstance" and test.args
            and isinstance(test.args[0], Name)):
        return test.args[0].id
    if (isinstance(test, Compare) and isinstance(test.left, Name)
            and len(test.ops) == 1 and isinstance(test.ops[0], IsNot)
            and isinstance(test.comparators[0], Constant)
            and test.comparators[0].value is None):
        return test.left.id
    if isinstance(test, BoolOp) and isinstance(test.op, And):
        return next((name for value in test.values
                     if (name := _guarded_name(value)) is not None), None)
    return None


def _reads(nodes, name):
    return {node for stmt in nodes for node in walk(stmt)
            if isinstance(node, Name) and isinstance(node.ctx, Load)
            and node.id == name}


def narrowed_reads(tree):
    result = set()
    for owner in walk(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(owner, field, None)
            if not isinstance(block, list):
                continue
            for index, stmt in enumerate(block):
                if not isinstance(stmt, If):
                    continue
                if (name := _guarded_name(stmt.test)) is not None:
                    result.update(_reads(stmt.body, name))
                elif (isinstance(stmt.test, UnaryOp)
                      and isinstance(stmt.test.op, Not)
                      and (name := _guarded_name(stmt.test.operand)) is not None
                      and stmt.body and isinstance(
                          stmt.body[-1], (Raise, Return, Continue, Break))):
                    result.update(_reads(block[index + 1:], name))
                    result.update(_reads(stmt.orelse, name))
    return result


def declaration_nodes(tree):
    return {node if isinstance(node, (arg, FunctionDef, AsyncFunctionDef))
            else node.target for node in walk(tree)
            if isinstance(node, (arg, FunctionDef, AsyncFunctionDef, AnnAssign))}


def known_sources(bound, graph, parents):
    tree, types, dyn = bound.tree, bound.types, bound.dynamic
    classes = class_defs(tree)
    defined = {node.name for node in walk(tree)
               if isinstance(node, (ClassDef, FunctionDef, AsyncFunctionDef))}
    produced = {rule.output for rule in graph.rules}
    narrowed = narrowed_reads(tree)
    result = set()
    for node in walk(tree):
        if isinstance(node, arg):
            if node.annotation is not None or node.arg in ("self", "cls"):
                result.add(node)
        elif isinstance(node, (FunctionDef, AsyncFunctionDef)):
            if node.returns is not None:
                result.add(node)
        elif isinstance(node, AnnAssign):
            if node.annotation is not None:
                result.add(node.target)
        elif isinstance(node, (Constant, *CONTAINERS, Slice)):
            result.add(node)
        elif isinstance(node, UnaryOp) and isinstance(node.op, Not):
            result.add(node)
        elif isinstance(node, Compare) and all(
                isinstance(op, (Is, IsNot)) for op in node.ops):
            result.add(node)
        elif isinstance(node, Name) and isinstance(node.ctx, Load):
            if node in narrowed or node.id in defined:
                result.add(node)
            elif node not in produced and types.get(node) not in (None, dyn):
                result.add(node)
        elif isinstance(node, Call):
            local = callee_params(node, types, classes, parents)[1]
            if (node.func in bound.constructors
                    or isinstance(node.func, Name) and node.func.id in COERCIONS
                    or local is None and types.get(node) not in (None, dyn)):
                result.add(node)
    return result


def natural_type(node, env):
    if isinstance(node, Constant):
        name = NATURAL.get(type(node.value))
        return getattr(env, name).instance if name else None
    table = {List: env.list, ListComp: env.list, Dict: env.dict,
             DictComp: env.dict, Set: env.set, SetComp: env.set,
             Tuple: env.tuple}
    klass = next((value for kind, value in table.items()
                  if isinstance(node, kind)), None)
    return klass.instance if klass is not None else None


def erased_declarations(erased):
    return {node.target if isinstance(node, AnnAssign) else node
            for node in erased
            if isinstance(node, (AnnAssign, arg, FunctionDef, AsyncFunctionDef))}


def prepare(bound, graph, erased):
    parents = build_parents(bound.tree)
    if not graph.source_nodes:
        graph.source_nodes = known_sources(bound, graph, parents)
        graph.declarations = declaration_nodes(bound.tree)
        for source in graph.source_nodes:
            value = bound.declaration_types.get(source) or bound.types.get(source)
            if value is not None:
                graph.source_values[source] = value
        env = bound.dynamic.klass.type_env
        graph.natural_types = {node: value for node in bound.types
                               if (value := natural_type(node, env)) is not None}
    active = {source for source in graph.source_nodes
              if source not in erased and not (
                  isinstance(parents.get(source), AnnAssign)
                  and parents[source] in erased
                  and parents[source].target is source)}
    return active, erased_declarations(erased)


def literal_is_contextual(node, graph, active, erased_decls, recorded):
    for demand in graph.demands_for.get(node, ()):
        source, slot = demand.source
        if demand.role in (SELF_CONTEXT, DYNAMIC_CONTEXT) or source in erased_decls:
            continue
        if isinstance(source, FixedSource):
            value = graph.source_values.get(source)
        elif slot is TYPE and source in active:
            value = graph.source_values.get(source, recorded.get(source))
        elif demand.role == NUMERIC_CONTEXT:
            value = recorded.get(source)
        else:
            continue
        if isinstance(getattr(value, "klass", None), CType):
            return True
    return False

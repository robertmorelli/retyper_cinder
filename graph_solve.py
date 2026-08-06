"""Concrete replay of the one binding graph.

The solver has only three operations:

* enable or disable source values,
* evaluate type rules to a fixpoint,
* evaluate demands to obtain each use's expected type.

All syntax decisions are encoded as rules/demands before this module runs.
"""
from ast import (AnnAssign, arg, BinOp, BoolOp, Call, ClassDef, Compare,
                 Constant, Dict, DictComp, FunctionDef, AsyncFunctionDef,
                 GeneratorExp, If, Is, IsNot, List, ListComp, Load, Name, Not,
                 Raise, Return, Continue, Break, Set, SetComp, Slice, Tuple,
                 UnaryOp, And, walk)
from dataclasses import dataclass

from cinderx.compiler.static.types import CType

from graph_construction import (TYPE, CTX, COERCE, COERCIONS, UNARY,
                                CONTEXT, ELEM_CONTEXT, DYNAMIC_CONTEXT,
                                SELF_CONTEXT, NUMERIC_CONTEXT,
                                COMPARE_CONTEXT, BOOL_CONTEXT,
                                RECEIVER_CONTEXT, FixedSource, BindingGraph)
from graph_rules import callee_params, class_defs
from parent_pointers import build_parents
from patch_picker import boxed_instance
from type_transfer import transfer, element_type
from dunder_table import load_table

RECOMPUTE = (COERCE, UNARY)
CONTAINERS = (List, Tuple, Set, Dict, ListComp, SetComp, GeneratorExp, DictComp)


@dataclass(frozen=True)
class Settlement:
    types: dict
    contexts: dict
    typed: frozenset


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
    """The small set of guard narrowing visible directly in source syntax."""
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
                    continue
                test = stmt.test
                if (isinstance(test, UnaryOp) and isinstance(test.op, Not)
                        and (name := _guarded_name(test.operand)) is not None
                        and stmt.body and isinstance(
                            stmt.body[-1], (Raise, Return, Continue, Break))):
                    result.update(_reads(block[index + 1:], name))
                    result.update(_reads(stmt.orelse, name))
    return result


def declaration_nodes(tree):
    result = set()
    for node in walk(tree):
        if isinstance(node, (arg, FunctionDef, AsyncFunctionDef)):
            result.add(node)
        elif isinstance(node, AnnAssign):
            result.add(node.target)
    return result


def known_type_source(tree, types, constructors, dyn, graph, parents):
    """Nodes whose recorded value enters the replay without a type rule."""
    sources = set()
    classes = class_defs(tree)
    defined = {node.name for node in walk(tree)
               if isinstance(node, (ClassDef, FunctionDef, AsyncFunctionDef))}
    produced = {dst for outputs in graph.values() for dst in outputs}
    narrowed = narrowed_reads(tree)
    for node in walk(tree):
        if isinstance(node, arg):
            if node.annotation is not None or node.arg in ("self", "cls"):
                sources.add(node)
        elif isinstance(node, (FunctionDef, AsyncFunctionDef)):
            if node.returns is not None:
                sources.add(node)
        elif isinstance(node, AnnAssign):
            if node.annotation is not None:
                sources.add(node.target)
        elif isinstance(node, (Constant, *CONTAINERS, Slice)):
            sources.add(node)
        elif isinstance(node, UnaryOp) and isinstance(node.op, Not):
            sources.add(node)
        elif isinstance(node, Compare) and all(
                isinstance(op, (Is, IsNot)) for op in node.ops):
            sources.add(node)
        elif isinstance(node, Name) and isinstance(node.ctx, Load):
            if node in narrowed or node.id in defined:
                sources.add(node)
            elif (node, TYPE) not in produced and types.get(node) not in (None, dyn):
                sources.add(node)
        elif isinstance(node, Call):
            local = callee_params(node, types, classes, parents)[1]
            if (node.func in constructors
                    or isinstance(node.func, Name) and node.func.id in COERCIONS
                    or local is None and types.get(node) not in (None, dyn)):
                sources.add(node)
    return sources


NATURAL = {bool: "bool", int: "int", float: "float", str: "str", bytes: "bytes"}


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


def _erased_declarations(erased):
    result = set()
    for node in erased:
        if isinstance(node, AnnAssign):
            result.add(node.target)
        elif isinstance(node, (arg, FunctionDef, AsyncFunctionDef)):
            result.add(node)
    return result


def _active_sources(bound, graph, erased, parents):
    if not graph.source_nodes:
        graph.source_nodes = known_type_source(
            bound.tree, bound.types, bound.constructors, bound.dynamic,
            graph.g, parents)
        graph.declarations = declaration_nodes(bound.tree)
        for source in graph.source_nodes:
            value = bound.declaration_types.get(source) or bound.types.get(source)
            if value is not None:
                graph.source_values[source] = value
        env = bound.dynamic.klass.type_env
        graph.natural_types = {node: value for node in bound.types
                               if (value := natural_type(node, env)) is not None}
    return {source for source in graph.source_nodes
            if source not in erased and not (
                isinstance(parents.get(source), AnnAssign)
                and parents[source] in erased
                and parents[source].target is source)}


def _literal_is_contextual(node, graph, sources, erased_decls, recorded):
    for demand in graph.demands_for.get(node, ()):
        source, slot = demand.source
        if demand.role == SELF_CONTEXT or source in erased_decls:
            continue
        if demand.role == DYNAMIC_CONTEXT:
            continue
        if isinstance(source, FixedSource):
            value = graph.source_values.get(source)
        elif slot is TYPE and source in sources:
            value = graph.source_values.get(source, recorded.get(source))
        elif demand.role == NUMERIC_CONTEXT:
            value = recorded.get(source)
        else:
            continue
        if isinstance(getattr(value, "klass", None), CType):
            return True
    return False


def _solve_types(bound, graph, sources, erased_decls):
    dyn, recorded = bound.dynamic, bound.types
    values = {}
    for node, old in recorded.items():
        natural = graph.natural_types.get(node)
        if natural is not None and not _literal_is_contextual(
                node, graph, sources, erased_decls, recorded):
            values[node] = natural
        elif node in sources:
            values[node] = graph.source_values.get(node, old)
        elif node in graph.rule_for:
            values[node] = old                 # descending start for cycles
        else:
            values[node] = dyn
    for source, value in graph.source_values.items():
        if isinstance(source, FixedSource) or source in sources:
            values[source] = value
        else:
            values.setdefault(source, dyn)

    table = load_table()
    env = dyn.klass.type_env
    changed = True
    while changed:
        changed = False
        for rule in graph.rules:
            node = rule.output
            if node in sources and not any(
                    role in RECOMPUTE for role, _, _ in rule.inputs):
                continue
            new = transfer(node, rule.inputs, values, recorded, env, dyn, table)
            if (node in erased_decls and new is not None and new is not dyn
                    and isinstance(new.klass, CType)):
                new = boxed_instance(new)
            if new is not None and values.get(node) is not new:
                values[node] = new
                changed = True
    return values


def _solve_contexts(bound, graph, values, sources, erased_decls):
    dyn = bound.dynamic
    cache, visiting = {}, set()

    def context(node):
        if node in cache:
            return cache[node]
        if node in visiting:
            return values.get(node, dyn)
        visiting.add(node)
        demands = [d for d in graph.demands_for.get(node, ())
                   if d.role != SELF_CONTEXT]

        # Conditional demands override ordinary signatures only when active.
        for demand in demands:
            source = demand.source[0]
            current, peer = values.get(node), values.get(source)
            if demand.role == DYNAMIC_CONTEXT:
                result = dyn
            elif demand.role == RECEIVER_CONTEXT:
                result = dyn if peer is dyn else None
            elif demand.role in (COMPARE_CONTEXT, BOOL_CONTEXT):
                result = (dyn if isinstance(getattr(current, "klass", None), CType)
                          and not isinstance(getattr(peer, "klass", None), CType)
                          else None)
            elif demand.role == NUMERIC_CONTEXT:
                result = (peer if not isinstance(getattr(current, "klass", None), CType)
                          and isinstance(getattr(peer, "klass", None), CType)
                          and not isinstance(node, Constant) else None)
            else:
                result = None
            if result is not None:
                cache[node] = result
                visiting.remove(node)
                return result

        for demand in demands:
            source, slot = demand.source
            if source in erased_decls:
                result = dyn
            elif demand.role == ELEM_CONTEXT:
                container = values.get(source, dyn)
                result = (dyn if container is dyn else
                          element_type(container, None, dyn))
            elif slot is TYPE:
                result = values.get(source, graph.source_values.get(source, dyn))
            else:
                result = context(source)
            cache[node] = result
            visiting.remove(node)
            return result

        result = values.get(node, bound.type_contexts.get(node, dyn))
        cache[node] = result
        visiting.remove(node)
        return result

    return {node: context(node) for node in bound.type_contexts}


def settle_tables(bound, graph, erased=()):
    if not isinstance(graph, BindingGraph):
        raise TypeError("settle_tables requires a BindingGraph")
    parents = build_parents(bound.tree)
    sources = _active_sources(bound, graph, erased, parents)
    if not erased:
        typed = frozenset((node, TYPE) for node in bound.types)
        return Settlement(dict(bound.types), dict(bound.type_contexts), typed)
    erased_decls = _erased_declarations(erased)
    values = _solve_types(bound, graph, sources, erased_decls)
    contexts = _solve_contexts(
        bound, graph, values, sources, erased_decls)
    typed = frozenset(
        [(node, TYPE) for node, value in values.items() if value is not bound.dynamic]
        + [(node, CTX) for node, value in contexts.items() if value is not bound.dynamic])
    return Settlement(values, contexts, typed)

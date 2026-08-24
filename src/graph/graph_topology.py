"""The fixed topology and narrowing locations of the type graph."""
import ast
from dataclasses import dataclass

from ..cinderx_binding import can_narrow, is_primative
from .definitionless_names import collect_definitionless_names

TYPE = "type"
CONTEXT = "context"

SIGNATURES = dict.fromkeys(("box", "unbox"), "argument")
SIGNATURES.update(dict.fromkeys(
    ("clen", "cbool", "double", "Array", "cast",
     "int8", "int16", "int32", "int64",
     "uint8", "uint16", "uint32", "uint64"), "own"))
SIGNATURES.update(dict.fromkeys(("pop", "get", "copy", "index", "count"),
                                "receiver"))
SIGNATURES.update(dict.fromkeys(("append", "add", "insert", "extend",
                                 "remove", "discard"), "element"))
CINDER_CONVERSIONS = {n for n, k in SIGNATURES.items() if k == "argument"}
COERCIONS = {"box", "cast"} | {n for n in SIGNATURES if n in (
    "double", "cbool", "int8", "int16", "int32", "int64",
    "uint8", "uint16", "uint32", "uint64")}

BINDING_STATEMENTS = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class Edge:
    """An unconditional dependency between two cells."""
    source: tuple
    target: tuple


@dataclass(frozen=True)
class AnnotationLocation:
    """A definition whose type can change when its annotation is erased."""
    annotation: ast.AST
    target: ast.AST | None
    rhs: ast.AST | None
    owner: ast.AST | None


@dataclass(frozen=True)
class NarrowingChoice:
    """The alternative edges for one declaration/value narrowing decision."""
    annotation: ast.AST
    value: ast.AST
    narrowing_edge: Edge
    declared_edge: Edge


class UnionFind:
    def __init__(self):
        self.parents = {}

    def find(self, item):
        parent = self.parents.setdefault(item, item)
        if parent != item:
            self.parents[item] = self.find(parent)
        return self.parents[item]

    def union(self, left, right):
        self.parents[self.find(left)] = self.find(right)

    def partitions(self):
        groups = {}
        for node in self.parents:
            groups.setdefault(self.find(node), set()).add(node)
        return groups


class Topology(ast.NodeVisitor):
    def __init__(self, bound=None):
        self.edges = set()
        self.narrowing_choices = []
        self.annotation_locations = []
        self.annotation_units = []
        self.benchmark_units = []
        self.function = None
        self.owners = {}
        self.groups = UnionFind()
        self.definitionless_names = {}
        self.resolved = {}
        self.declarations = {}
        self.inline_functions = set()
        self.types = {}
        self._index = None
        self.reaching = {}
        self.missing_read_sources = []
        self.dynamic = None
        if bound is not None:
            self.construct(bound)

    def cell(self, node, slot):
        return (node, slot)

    def add_edge(self, source, target):
        if source is not None and target is not None and source[0] is not target[0]:
            self.edges.add(Edge(source, target))

    def link(self, source, target, slot=TYPE):
        """Edge from one expression's type to another expression's cell."""
        self.add_edge(self.cell(source, TYPE), self.cell(target, slot))

    def narrowing_choice(self, value, declaration, target):
        """Choose the value while it narrows, otherwise the declaration."""
        fallback = self.stored_target(declaration)
        assigned = self.types.get(value)
        declared = self.types.get(fallback)
        if not can_narrow(declared, assigned, self.dynamic):
            self.link(fallback, target)
        else:
            self.narrowing_choices.append(NarrowingChoice(
                annotation=declaration,
                value=value,
                narrowing_edge=Edge(self.cell(value, TYPE),
                                     self.cell(target, TYPE)),
                declared_edge=Edge(self.cell(fallback, TYPE),
                                   self.cell(target, TYPE)),
            ))

    def narrowing_return(self, value, function):
        """Choose an inline expression or its declared return context."""
        assigned = self.types.get(value)
        declared = self.types.get(function)
        if not can_narrow(declared, assigned, self.dynamic):
            self.link(function, value, CONTEXT)
        else:
            self.narrowing_choices.append(NarrowingChoice(
                annotation=function,
                value=value,
                narrowing_edge=Edge(self.cell(value, TYPE),
                                     self.cell(function, TYPE)),
                declared_edge=Edge(self.cell(function, TYPE),
                                   self.cell(value, CONTEXT)),
            ))

    def narrowing_link(self, source, target):
        """A reaching definition always feeds its read."""
        self.link(source, target)

    def result_link(self, source, node):
        """Link a result to the expression that contains it."""
        self.link(source, node)

    def construct(self, bound):
        self.reaching = {read: [d for d in defs if d is not read]
                         for read, defs in bound.resolved_from.items()
                         if isinstance(defs, frozenset)}
        self.dynamic = bound.dynamic
        self.resolved = bound.reverse_outflow
        self.declarations = bound.assignment_declarations
        self.inline_functions = bound.inline_functions
        self.types = bound.types
        self.definitionless_names = collect_definitionless_names(bound.tree)
        self.index(bound.tree)
        roots = ({*bound.outflow, *bound.inflow, *bound.components,
                  *bound.annotation_roots} - {None})
        stored = {node.value: target
                  for node in ast.walk(bound.tree) if type(node) is ast.Assign
                  for target in node.targets if type(target) is ast.Name}
        for root in roots:
            self.groups.find(root)
            for neighbor in filter(None, bound.components.get(root, ())):
                self.groups.union(root, neighbor)
            for flows, slot in ((bound.outflow, TYPE), (bound.inflow, CONTEXT)):
                for node in flows.get(root, ()):
                    if slot is TYPE:
                        reaching = self.reaching.get(node)
                        if reaching and not any(d is root for d in reaching):
                            for definition in reaching:
                                self.narrowing_link(definition, node)
                            continue
                    if slot is CONTEXT and node in stored:
                        continue
                    self.add_edge(self.cell(root, TYPE), self.cell(node, slot))

        self.tree = bound.tree
        self.visit(bound.tree)
        self.read_through_targets()

        ordered = sorted(roots, key=lambda node: (node.lineno, node.col_offset))
        partitions = self.groups.partitions()
        representatives = {}
        for root in ordered:
            representatives.setdefault(self.groups.find(root), root)
        self.roots = list(representatives.values())
        self.annotation_units = [frozenset(partitions[leader])
                                 for leader in representatives]
        self.annotation_locations = [
            AnnotationLocation(
                annotation=node,
                target=(node.target if type(node) is ast.AnnAssign
                        and type(node.target) in (ast.Name, ast.Attribute)
                        else None),
                rhs=node.value if type(node) is ast.AnnAssign else None,
                owner=self.owners.get(node),
            )
            for node in ordered
            if (type(node) in (ast.AnnAssign, ast.arg)
                or getattr(node, "returns", None) is not None)
        ]
        self.benchmark_units = self.group_functions()

    def read_through_targets(self):
        """Route declaration reads through their assignment targets."""
        moved = {node for node in ast.walk(self.tree)
                 if type(node) is ast.AnnAssign
                 and type(node.target) in (ast.Name, ast.Attribute)}
        for edge in list(self.edges):
            node, slot = edge.source
            if slot is not TYPE or type(node) is not ast.AnnAssign:
                continue
            if type(node.target) not in (ast.Name, ast.Attribute):
                continue
            self.edges.discard(edge)
            if edge.target != self.cell(node.target, TYPE):
                self.add_edge(self.cell(node.target, TYPE), edge.target)
        for node in moved:
            self.add_edge(self.cell(node, TYPE), self.cell(node.target, TYPE))

    def index(self, tree):
        """Collect names, classes, and syntactic marks before the walk."""
        for child in ast.iter_child_nodes(tree):
            self.index_node(child, None)

    def index_node(self, node, scope):
        self.owners.setdefault(node, scope)
        if type(node) in BINDING_STATEMENTS:
            return self.index_function(node, scope)
        for child in ast.iter_child_nodes(node):
            self.index_node(child, scope)

    def index_function(self, node, scope):
        for parameter in self.parameters_of(node):
            self.owners.setdefault(parameter, node)
        for child in node.body:
            self.index_node(child, node)

    def parameters_of(self, target):
        return target.args.posonlyargs + target.args.args

    def called_name(self, func):
        if type(func) is ast.Name:
            return func.id
        if type(func) is ast.Attribute:
            return func.attr
        if type(func) is ast.Subscript:
            return self.called_name(func.value)
        return None

    def visit(self, node):
        if type(node) in BINDING_STATEMENTS:
            return super().visit(node)
        self.generic_visit(node)
        visitor = getattr(self, f"visit_{type(node).__name__}", None)
        if visitor is not None:
            return visitor(node)

    def generic_visit(self, node):
        annotation = getattr(node, "annotation", None) or getattr(
            node, "returns", None)
        for child in ast.iter_child_nodes(node):
            if child is not annotation:
                self.visit(child)

    def visit_FunctionDef(self, node):
        outer = self.function
        self.function = node
        self.generic_visit(node)
        self.function = outer
        params = self.parameters_of(node)
        defaults = node.args.defaults
        for param, default in zip(params[len(params) - len(defaults):], defaults):
            self.link(param, default, CONTEXT)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Name(self, node):
        if type(node.ctx) is not ast.Load:
            return
        reaching = self.reaching.get(node)
        if reaching:
            for binder in reaching:
                self.narrowing_link(binder, node)
            return
        if node in self.resolved:
            return
        if any(node.id in self.definitionless_names.get(scope, ())
               for scope in (self.function, None)):
            return
        self.missing_read_sources.append(node)

    def visit_Assign(self, node):
        for target in node.targets:
            self.assign_target(target, node.value)

    def assign_target(self, target, value):
        """Link one assignment, projecting matching unpacking shapes."""
        if type(target) in (ast.Tuple, ast.List):
            values = (value.elts if type(value) in (ast.Tuple, ast.List)
                      and len(value.elts) == len(target.elts) else None)
            for index, element in enumerate(target.elts):
                self.assign_target(element,
                                   values[index] if values is not None else value)
        elif type(target) in (ast.Attribute, ast.Subscript):
            self.link(target, value, CONTEXT)
        elif (declaration := self.declarations.get(target)) is not None:
            self.link(target, value, CONTEXT)
            self.narrowing_choice(value, declaration, target)
        else:
            self.link(value, target)

    def stored_target(self, declaration):
        """The name cell through which a declaration stores its type."""
        target = getattr(declaration, "target", None)
        return target if type(target) is ast.Name else declaration

    def visit_AnnAssign(self, node):
        if node.value is not None:
            self.link(node.target, node.value, CONTEXT)

    def visit_AugAssign(self, node):
        self.link(node.target, node.value, CONTEXT)
        reaching = self.reaching.get(node.target, ())
        for source in reaching:
            self.link(source, node.target)
        if (not reaching
                and (declaration := self.declarations.get(node.target)) is not None):
            self.link(self.stored_target(declaration), node.target)
        self.result_link(node.value, node.target)

    def visit_NamedExpr(self, node):
        self.link(node.value, node.target)
        self.link(node.value, node)

    def visit_Attribute(self, node):
        self.link(node.value, node)

    def visit_Subscript(self, node):
        self.link(node.value, node)
        if type(node.slice) is not ast.Slice:
            self.link(node.value, node.slice, CONTEXT)

    def chain_contexts(self, operands):
        """Link operand contexts from left to right."""
        for left, right in zip(operands, operands[1:]):
            self.add_edge(self.cell(left, CONTEXT), self.cell(right, CONTEXT))

    def visit_BinOp(self, node):
        if type(node.op) is not ast.Pow:
            self.add_edge(self.cell(node, CONTEXT), self.cell(node.left, CONTEXT))
        self.chain_contexts([node.left, node.right])
        self.add_edge(self.cell(node.right, CONTEXT), self.cell(node, TYPE))

    def visit_Compare(self, node):
        self.chain_contexts([node.left, *node.comparators])
        self.add_edge(self.cell(node.comparators[-1], CONTEXT),
                      self.cell(node, TYPE))

    def visit_UnaryOp(self, node):
        if type(node.op) is ast.Not:
            self.link(node.operand, node)
        else:
            self.add_edge(self.cell(node, CONTEXT), self.cell(node.operand, CONTEXT))
            self.result_link(node.operand, node)

    def visit_BoolOp(self, node):
        self.chain_contexts(node.values)
        self.add_edge(self.cell(node.values[-1], CONTEXT), self.cell(node, TYPE))

    def visit_IfExp(self, node):
        self.add_edge(self.cell(node.body, CONTEXT),
                      self.cell(node.orelse, CONTEXT))
        self.add_edge(self.cell(node.orelse, CONTEXT), self.cell(node, TYPE))

    def visit_FormattedValue(self, node):
        self.add_edge(self.cell(node, TYPE), self.cell(node.value, CONTEXT))

    def inlined_result(self, target):
        """Return the sole value expression of an inline function, if any."""
        if target not in self.inline_functions:
            return None
        returned = target.body[0]
        return returned.value if isinstance(returned, ast.Return) else None

    def visit_Call(self, node):
        self.link(node.func, node)
        for argument in node.args:
            if self.called_name(node.func) not in COERCIONS:
                self.link(node.func, argument, CONTEXT)
        if self.called_name(node.func) in CINDER_CONVERSIONS:
            for argument in node.args:
                self.link(argument, node)
            return
        name = self.called_name(node.func)
        if type(node.func) is ast.Attribute:
            kind = SIGNATURES.get(name)
            if kind == "receiver":
                self.result_link(node.func.value, node)
            elif kind == "element":
                for argument in node.args:
                    self.link(node.func.value, argument, CONTEXT)

    def visit_Return(self, node):
        if node.value is not None and self.function is not None:
            if (self.function.returns is not None
                    and self.inlined_result(self.function) is node.value):
                self.narrowing_return(node.value, self.function)
            else:
                self.link(self.function, node.value, CONTEXT)

    def visit_For(self, node):
        iterable = self.resolved.get(node.iter)
        for target in self.loop_targets(node.target):
            declaration = self.declarations.get(target)
            if declaration is None:
                self.link(node.iter, target)
            else:
                self.narrowing_choice(node.iter, declaration, target)
            if iterable is not None and declaration is not None:
                self.groups.union(declaration, iterable)

    visit_AsyncFor = visit_For

    def loop_targets(self, target):
        """Yield the name stores performed by one loop iteration."""
        if type(target) is ast.Name:
            yield target
        elif type(target) in (ast.Tuple, ast.List):
            for element in target.elts:
                yield from self.loop_targets(element)

    def visit_comprehension_expr(self, node, elements):
        for generator in node.generators:
            for target in self.loop_targets(generator.target):
                self.link(generator.iter, target)
        for element in elements:
            self.link(element, node)
            self.add_edge(self.cell(node, CONTEXT), self.cell(element, CONTEXT))

    def visit_ListComp(self, node):
        self.visit_comprehension_expr(node, [node.elt])

    visit_SetComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_DictComp(self, node):
        self.visit_comprehension_expr(node, [node.key, node.value])

    def visit_List(self, node):
        for item in node.elts:
            self.link(item, node)

    visit_Set = visit_List
    visit_Tuple = visit_List

    def visit_Dict(self, node):
        for item in [*(key for key in node.keys if key is not None),
                     *node.values]:
            self.link(item, node)


    def group_functions(self):
        functions = UnionFind()
        for unit in self.annotation_units:
            owners = [self.owners.get(node) for node in unit]
            for owner in owners:
                functions.union(owners[0], owner)
        grouped = {}
        for root, unit in zip(self.roots, self.annotation_units):
            owner = functions.find(self.owners.get(root))
            grouped.setdefault(owner, set()).update(unit)
        return [frozenset(unit) for unit in grouped.values()]

    def units(self, granularity="annotation"):
        if granularity == "benchmark":
            return self.benchmark_units
        return self.annotation_units

    def nodes_for_mask(self, mask, granularity="annotation"):
        return {
            node
            for index, unit in enumerate(self.units(granularity))
            if mask & (1 << index)
            for node in unit
        }

    def index_edges(self):
        """Index topology by source cell and by target node and slot."""
        if self._index is None:
            outgoing, by_type, by_context = {}, {}, {}
            topology = set(self.edges)
            for edge in topology:
                outgoing.setdefault(edge.source, []).append(edge.target)
                node, slot = edge.target
                table = by_context if slot == CONTEXT else by_type
                table.setdefault(node, []).append(edge.source)
            self._index = (outgoing, by_type, by_context)
        return self._index

    def outgoing(self):
        return self.index_edges()[0]


def machine(value):
    """An unboxed type, which an erased slot genuinely cannot hold."""
    try:
        return value is not None and is_primative(value)
    except Exception:
        return False

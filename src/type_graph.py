"""Build and activate the type graph for one annotation mask."""
import ast
from dataclasses import dataclass
from types import SimpleNamespace

from .annotation_remover import _checked_ctor
from .type_rules import PRIMITIVE_NAMES, can_narrow, is_primitive

TYPE = "type"
TYPE_CONSTRAINT = "type_constraint"

SIGNATURES = dict.fromkeys(("pop", "get", "copy", "index", "count"),
                           "receiver")
SIGNATURES.update(dict.fromkeys(("append", "add", "insert", "extend",
                                 "remove", "discard"), "element"))
CINDER_CONVERSIONS = {"box", "unbox"}
COERCIONS = {"box", "cast"} | PRIMITIVE_NAMES

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


class TypeGraph(ast.NodeVisitor):
    def __init__(self, bound=None, mask=0, granularity="annotation"):
        self.edges = set()
        self.narrowing_choices = []
        self.annotation_locations = []
        self.annotation_units = []
        self.benchmark_units = []
        self.function = None
        self.owners = {}
        self.groups = UnionFind()
        self.resolved = {}
        self.declarations = {}
        self.inline_functions = set()
        self.types = {}
        self._index = None
        self.reaching = {}
        self.dynamic = None
        self.bound = bound
        self.original_types = {}
        self.type_constraints = {}
        self.topology_edges = frozenset()
        self.erased = set()
        if bound is not None:
            self.construct(bound)
            self.original_types = self.types
            self.topology_edges = frozenset(self.edges)
            self._apply_mask(mask, granularity)

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
        """Choose an inline expression or its declared return constraint."""
        assigned = self.types.get(value)
        declared = self.types.get(function)
        if not can_narrow(declared, assigned, self.dynamic):
            self.link(function, value, TYPE_CONSTRAINT)
        else:
            self.narrowing_choices.append(NarrowingChoice(
                annotation=function,
                value=value,
                narrowing_edge=Edge(self.cell(value, TYPE),
                                     self.cell(function, TYPE)),
                declared_edge=Edge(self.cell(function, TYPE),
                                   self.cell(value, TYPE_CONSTRAINT)),
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
        self.index(bound.tree)
        roots = ({*bound.outflow, *bound.inflow, *bound.components} - {None})
        stored = {node.value: target
                  for node in ast.walk(bound.tree) if type(node) is ast.Assign
                  for target in node.targets if type(target) is ast.Name}
        for root in roots:
            self.groups.find(root)
            for neighbor in filter(None, bound.components.get(root, ())):
                self.groups.union(root, neighbor)
            for flows, slot in ((bound.outflow, TYPE), (bound.inflow, TYPE_CONSTRAINT)):
                for node in flows.get(root, ()):
                    if slot is TYPE:
                        reaching = self.reaching.get(node)
                        if reaching and not any(d is root for d in reaching):
                            for definition in reaching:
                                self.narrowing_link(definition, node)
                            continue
                    if slot is TYPE_CONSTRAINT and node in stored:
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
            self.link(param, default, TYPE_CONSTRAINT)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Name(self, node):
        if type(node.ctx) is not ast.Load:
            return
        reaching = self.reaching.get(node)
        if reaching:
            for binder in reaching:
                self.narrowing_link(binder, node)

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
            self.link(target, value, TYPE_CONSTRAINT)
        elif (declaration := self.declarations.get(target)) is not None:
            self.link(target, value, TYPE_CONSTRAINT)
            self.narrowing_choice(value, declaration, target)
        else:
            self.link(value, target)

    def stored_target(self, declaration):
        """The name cell through which a declaration stores its type."""
        target = getattr(declaration, "target", None)
        return target if type(target) is ast.Name else declaration

    def visit_AnnAssign(self, node):
        if node.value is not None:
            self.link(node.target, node.value, TYPE_CONSTRAINT)

    def visit_AugAssign(self, node):
        self.link(node.target, node.value, TYPE_CONSTRAINT)
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
            self.link(node.value, node.slice, TYPE_CONSTRAINT)

    def chain_constraints(self, operands):
        """Link operand constraints from left to right."""
        for left, right in zip(operands, operands[1:]):
            self.add_edge(self.cell(left, TYPE_CONSTRAINT), self.cell(right, TYPE_CONSTRAINT))

    def visit_BinOp(self, node):
        if type(node.op) is not ast.Pow:
            self.add_edge(self.cell(node, TYPE_CONSTRAINT), self.cell(node.left, TYPE_CONSTRAINT))
        self.chain_constraints([node.left, node.right])
        self.add_edge(self.cell(node.right, TYPE_CONSTRAINT), self.cell(node, TYPE))

    def visit_Compare(self, node):
        self.chain_constraints([node.left, *node.comparators])
        self.add_edge(self.cell(node.comparators[-1], TYPE_CONSTRAINT),
                      self.cell(node, TYPE))

    def visit_UnaryOp(self, node):
        if type(node.op) is ast.Not:
            self.link(node.operand, node)
        else:
            self.add_edge(self.cell(node, TYPE_CONSTRAINT), self.cell(node.operand, TYPE_CONSTRAINT))
            self.result_link(node.operand, node)

    def visit_BoolOp(self, node):
        self.chain_constraints(node.values)
        self.add_edge(self.cell(node.values[-1], TYPE_CONSTRAINT), self.cell(node, TYPE))

    def visit_IfExp(self, node):
        self.add_edge(self.cell(node.body, TYPE_CONSTRAINT),
                      self.cell(node.orelse, TYPE_CONSTRAINT))
        self.add_edge(self.cell(node.orelse, TYPE_CONSTRAINT), self.cell(node, TYPE))

    def visit_FormattedValue(self, node):
        self.add_edge(self.cell(node, TYPE), self.cell(node.value, TYPE_CONSTRAINT))

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
                self.link(node.func, argument, TYPE_CONSTRAINT)
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
                    self.link(node.func.value, argument, TYPE_CONSTRAINT)

    def visit_Return(self, node):
        if node.value is not None and self.function is not None:
            if (self.function.returns is not None
                    and self.inlined_result(self.function) is node.value):
                self.narrowing_return(node.value, self.function)
            else:
                self.link(self.function, node.value, TYPE_CONSTRAINT)

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
            self.add_edge(self.cell(node, TYPE_CONSTRAINT), self.cell(element, TYPE_CONSTRAINT))

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

    def _nodes_for_mask(self, mask, granularity="annotation"):
        return {
            node
            for index, unit in enumerate(self.units(granularity))
            if mask & (1 << index)
            for node in unit
        }

    def _apply_mask(self, mask, granularity):
        if mask == -1:
            mask = (1 << len(self.units(granularity))) - 1
        self.erased = self._nodes_for_mask(mask, granularity)
        prediction = self.flow(self.erased)
        self.edges = prediction.edges
        self._index = None
        self.types = prediction.types
        self.type_constraints = prediction.constraints

    def clip(self, erased):
        """Return active edges and cells made dynamic directly by erasure."""
        edges = set(self.topology_edges)
        dynamic = set()
        for location in self.annotation_locations:
            if location.annotation not in erased:
                continue
            if def_is_typed(location):
                continue
            if rhs_narrows(location, self.bound):
                edges.add(
                    Edge(
                        self.cell(location.rhs, TYPE),
                        self.cell(location.annotation, TYPE),
                    )
                )
                continue
            dynamic.update(
                (
                    self.cell(location.annotation, TYPE),
                    self.cell(location.annotation, TYPE_CONSTRAINT),
                )
            )
            if location.target is not None:
                dynamic.update(
                    (
                        self.cell(location.target, TYPE),
                        self.cell(location.target, TYPE_CONSTRAINT),
                    )
                )
        return SimpleNamespace(edges=edges, dynamic=dynamic)

    def _propagate(self, edges, initial_dynamic):
        """Propagate dynamic types to a fixpoint over active graph edges."""
        bound = self.bound
        by_type, by_constraint = active_feeds(edges)
        decided_nodes = list(dict.fromkeys([*bound.types, *by_type]))
        dead = set(initial_dynamic)
        constraint_nodes = [
            node
            for node in by_constraint
            if (
                node in bound.type_constraints
                and self.cell(node, TYPE_CONSTRAINT) not in dead
            )
        ]
        pending = True
        while pending:
            pending = False
            for node in decided_nodes:
                cell = self.cell(node, TYPE)
                if cell in dead:
                    continue
                if (
                    decide_type(node, by_type.get(node, ()), dead, bound)
                    is bound.dynamic
                ):
                    dead.add(cell)
                    pending = True
            for node in constraint_nodes:
                cell = self.cell(node, TYPE_CONSTRAINT)
                if cell in dead:
                    continue
                if (
                    decide_constraint(
                        node,
                        by_constraint.get(node, ()),
                        dead,
                        bound,
                    )
                    is bound.dynamic
                ):
                    dead.add(cell)
                    pending = True

        types = {
            node: bound.dynamic if self.cell(node, TYPE) in dead else value
            for node, value in bound.types.items()
        }
        for node in decided_nodes:
            if self.cell(node, TYPE) not in dead:
                decided = decide_type(
                    node,
                    by_type.get(node, ()),
                    dead,
                    bound,
                )
                if decided is not None:
                    types[node] = decided

        constraints = dict(bound.type_constraints)
        for node, slot in initial_dynamic:
            if slot == TYPE_CONSTRAINT:
                constraints[node] = bound.dynamic
        for node in constraint_nodes:
            constraints[node] = decide_constraint(
                node,
                by_constraint[node],
                dead,
                bound,
            )
        return SimpleNamespace(types=types, constraints=constraints)

    def flow(self, erased=()):
        """Activate and resolve the graph for erased annotations."""
        erased = set(erased)
        clipped = self.clip(erased)
        selected = set()
        while True:
            edges = clipped.edges | selected
            prediction = self._propagate(edges, clipped.dynamic)
            changed = {
                (
                    choice.narrowing_edge
                    if (
                        prediction.types.get(choice.value)
                        is not self.bound.dynamic
                        or choice.annotation in erased
                    )
                    else choice.declared_edge
                )
                for choice in self.narrowing_choices
            }
            if changed == selected:
                prediction.edges = edges
                return prediction
            selected = changed

    def index_edges(self):
        """Index active edges by source cell."""
        if self._index is None:
            outgoing = {}
            for edge in self.edges:
                outgoing.setdefault(edge.source, []).append(edge.target)
            self._index = outgoing
        return self._index

    def outgoing(self):
        return self.index_edges()


def machine(value):
    """An unboxed type, which an erased slot genuinely cannot hold."""
    try:
        return value is not None and is_primitive(value)
    except Exception:
        return False


def def_is_typed(location):
    """Whether rewriting preserves this definition independently of flow."""
    node = location.annotation
    return (
        type(node) is ast.AnnAssign
        and location.rhs is not None
        and _checked_ctor(node.annotation, location.rhs) is not None
    )


def rhs_narrows(location, bound):
    """Whether CinderX can infer this erased definition from its RHS."""
    if (
        type(location.annotation) is not ast.AnnAssign
        or location.rhs is None
        or type(location.target) is not ast.Name
        or location.owner is None
    ):
        return False
    value = bound.types.get(location.rhs)
    return (
        can_narrow(value, value, bound.dynamic)
        and not machine(value)
        and not machine(bound.types.get(location.target))
    )


def decide_type(node, feeds, dead, bound):
    """Return the expression type unless one of its feeds is dead."""
    if any(part in dead for part in feeds):
        return bound.dynamic
    return bound.types.get(node)


def decide_constraint(node, feeds, dead, bound):
    """Return the type constraint unless one of its feeds is dead."""
    if any(feed in dead for feed in feeds):
        return bound.dynamic
    return bound.type_constraints.get(node)


def active_feeds(edges):
    by_type, by_constraint = {}, {}
    for edge in edges:
        node, slot = edge.target
        feeds = by_constraint if slot == TYPE_CONSTRAINT else by_type
        feeds.setdefault(node, []).append(edge.source)
    return by_type, by_constraint


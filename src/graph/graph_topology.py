"""The unconditional and annotation-predicated topology of the type graph."""
import ast
from dataclasses import dataclass

from ..annotation_remover import CHECKED
from ..cinderx_binding import is_primative
from types import SimpleNamespace

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
        self.predicated_edges = {}
        self.annotation_units = []
        self.benchmark_units = []
        self.function = None
        self.owners = {}
        self.groups = UnionFind()
        self.bindings = {}
        self.lexical_names = set()
        self.resolved = {}
        self.classes = {}
        self.functions = {}
        self.slots = {}
        self.owning_class = {}
        self.conditions = set()
        self.types = {}
        self._index = None
        self.reaching = {}
        self.missing_read_sources = []
        self.inflow = {}
        if bound is not None:
            self.construct(bound)

    def cell(self, node, slot):
        return (node, slot)

    def add_edge(self, source, target):
        if source is not None and target is not None and source[0] is not target[0]:
            self.edges.add(Edge(source, target))

    def add_predicated_edge(self, annotation, source, target):
        if (annotation is not None and source is not None and target is not None
                and source[0] is not target[0]):
            self.predicated_edges.setdefault(annotation, set()).add(
                Edge(source, target))

    def link(self, source, target, slot=TYPE):
        """Edge from one expression's type to another expression's cell."""
        self.add_edge(self.cell(source, TYPE), self.cell(target, slot))

    def narrowing_link(self, source, target):
        """A reaching definition feeds a read only after its annotation goes."""
        declaration = self.declaration_of(target)
        if declaration is not None and source is not declaration:
            self.add_predicated_edge(declaration, self.cell(source, TYPE),
                                     self.cell(target, TYPE))
        else:
            self.link(source, target)

    def result_link(self, source, node):
        """Link a result to the expression that contains it."""
        self.link(source, node)

    def construct(self, bound):
        unpacked = {element
                    for node in ast.walk(bound.tree)
                    if type(node) in (ast.For, ast.AsyncFor)
                    and type(node.target) in (ast.Tuple, ast.List)
                    for element in ast.walk(node.target)
                    if type(element) is ast.Name}
        self.reaching = {read: [d for d in defs
                                if d is not read and d not in unpacked]
                         for read, defs in bound.resolved_from.items()
                         if isinstance(defs, frozenset)}
        self.inflow = bound.inflow
        self.resolved = bound.reverse_outflow
        self.types = bound.types
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
                        self.add_edge(self.cell(root, TYPE),
                                  self.cell(stored[node], TYPE))
                        continue
                    self.add_edge(self.cell(root, TYPE), self.cell(node, slot))

        self.tree = bound.tree
        self.visit(bound.tree)
        self.link_overrides()
        self.read_through_targets()

        ordered = sorted(roots, key=lambda node: (node.lineno, node.col_offset))
        partitions = self.groups.partitions()
        representatives = {}
        for root in ordered:
            representatives.setdefault(self.groups.find(root), root)
        self.roots = list(representatives.values())
        self.annotation_units = [frozenset(partitions[leader])
                                 for leader in representatives]
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
            if edge.target == self.cell(node.value, CONTEXT):
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
        if type(node) is ast.ClassDef:
            return self.index_class(node, scope)
        self.index_binding(node, scope)
        self.index_marks(node)
        for child in ast.iter_child_nodes(node):
            self.index_node(child, scope)

    def index_function(self, node, scope):
        self.lexical_names.add((scope, node.name))
        self.functions.setdefault(node.name, []).append(node)
        for parameter in self.parameters_of(node):
            self.bind(node, parameter.arg, parameter)
            self.owners.setdefault(parameter, node)
        for child in node.body:
            self.index_node(child, node)

    def index_class(self, node, scope):
        """Index a class and its body and instance attribute declarations."""
        self.lexical_names.add((scope, node.name))
        self.classes.setdefault(node.name, []).append(node)
        for statement in node.body:
            if type(statement) in BINDING_STATEMENTS:
                self.owning_class[statement] = node
                for inner in ast.walk(statement):
                    if (type(inner) is ast.AnnAssign
                            and type(inner.target) is ast.Attribute):
                        self.slots.setdefault(
                            inner.target.attr, []).append((node, inner))
                self.index_node(statement, scope)
            elif (type(statement) is ast.AnnAssign
                    and type(statement.target) is ast.Name):
                self.slots.setdefault(
                    statement.target.id, []).append((node, statement))
                self.owners.setdefault(statement, scope)
            else:
                self.index_node(statement, scope)

    def index_binding(self, node, scope):
        """Every place a name is given a value in this scope."""
        if type(node) is ast.AnnAssign and type(node.target) is ast.Name:
            self.bind(scope, node.target.id, node)
        elif type(node) is ast.Assign:
            for target in node.targets:
                if type(target) is ast.Name:
                    self.bind(scope, target.id, target)
        elif type(node) in (ast.For, ast.AsyncFor):
            if type(node.target) is ast.Name:
                self.bind(scope, node.target.id, node.target)
        elif type(node) is ast.NamedExpr and type(node.target) is ast.Name:
            self.bind(scope, node.target.id, node.target)
        elif type(node) is ast.Import:
            for name in node.names:
                self.lexical_names.add(
                    (scope, name.asname or name.name.split(".", 1)[0]))
        elif type(node) is ast.ImportFrom:
            for name in node.names:
                if name.name != "*":
                    self.lexical_names.add((scope, name.asname or name.name))

    def index_marks(self, node):
        """Positions the later rules ask about: the conditions."""
        if type(node) in (ast.If, ast.While, ast.IfExp):
            self.conditions.add(node.test)

    def bind(self, scope, name, node):
        self.bindings.setdefault((scope, name), []).append(node)

    def parameters_of(self, target):
        return target.args.posonlyargs + target.args.args

    def annotated(self, scope, name):
        for node in self.bindings.get((scope, name), ()):
            if type(node) in (ast.AnnAssign, ast.arg):
                return node
        return None


    def called_name(self, func):
        if type(func) is ast.Name:
            return func.id
        if type(func) is ast.Attribute:
            return func.attr
        if type(func) is ast.Subscript:
            return self.called_name(func.value)
        return None

    def receiver_class(self, func):
        """Return the receiver's class name when it can be resolved."""
        if type(func) is not ast.Attribute:
            return None
        if type(func.value) is ast.Name and func.value.id in self.classes:
            return func.value.id
        name = self.klass_name(self.types.get(func.value))
        return name.rsplit(".", 1)[-1] if name else None

    def klass_name(self, value):
        try:
            return value.klass.type_name.qualname
        except Exception:
            return None

    def related(self, klass, target):
        """Is `target` this class, or one it inherits from?"""
        seen, pending = set(), [klass]
        while pending:
            current = pending.pop()
            if current.name == target:
                return True
            seen.add(current.name)
            for base in current.bases:
                name = base.id if type(base) is ast.Name else None
                if name and name not in seen:
                    pending.extend(self.classes.get(name, ()))
        return False

    def initializers(self, klass, seen=None):
        """The __init__ a class uses, following bases when it defines none."""
        own = [st for st in klass.body if type(st) in BINDING_STATEMENTS
               and st.name == "__init__"]
        if own:
            return own[:1]
        seen = seen or set()
        seen.add(klass.name)
        return [init for base in klass.bases
                if type(base) is ast.Name and base.id not in seen
                for parent in self.classes.get(base.id, ())
                for init in self.initializers(parent, seen)]

    def callees(self, func):
        """Return possible callees and whether each has an implicit instance."""
        name = self.called_name(func)
        found = [(init, True) for klass in self.classes.get(name, ())
                 for init in self.initializers(klass)]
        if found:
            return found
        matches = self.functions.get(name, ())
        receiver = self.receiver_class(func)
        if receiver is not None and len(matches) > 1:
            owned = [m for m in matches
                     if (k := self.owning_class.get(m)) is not None
                     and self.related(k, receiver)]
            matches = owned or matches
        explicit = (type(func) is ast.Attribute
                    and type(func.value) is ast.Name
                    and func.value.id in self.classes)
        return [(m, type(func) is ast.Attribute and not explicit)
                for m in matches]

    def parameters(self, target, skip_self):
        params = self.parameters_of(target)
        if skip_self and params and params[0].arg == "self":
            params = params[1:]
        return params

    DESCEND_FIRST = frozenset(['AnnAssign', 'Assign', 'Attribute', 'AugAssign', 'BinOp', 'BoolOp', 'Call', 'Compare', 'Dict', 'For', 'FormattedValue', 'IfExp', 'List', 'NamedExpr', 'Return', 'Subscript', 'UnaryOp'])

    def visit(self, node):
        if type(node).__name__ in self.DESCEND_FIRST:
            self.generic_visit(node)
        return super().visit(node)

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
        if type(node.ctx) is not ast.Load or node in self.resolved:
            return
        reaching = self.reaching.get(node)
        if reaching:
            for binder in reaching:
                self.narrowing_link(binder, node)
            return
        for scope in (self.function, None):
            binders = self.bindings.get((scope, node.id))
            if binders:
                for binder in binders:
                    if binder is not node:
                        self.narrowing_link(binder, node)
                return
        if any((scope, node.id) in self.lexical_names
               for scope in (self.function, None)):
            return
        self.missing_read_sources.append(node)

    def visit_Assign(self, node):
        for target in node.targets:
            if type(target) in (ast.Attribute, ast.Subscript):
                self.link(target, node.value, CONTEXT)
            elif self.annotated_target(target):
                self.link(target, node.value, CONTEXT)
                declaration = (self.resolved.get(target)
                               or self.annotated(self.function, target.id))
                self.add_predicated_edge(declaration,
                    self.cell(node.value, TYPE), self.cell(target, TYPE))
            else:
                self.link(node.value, target)

    def visit_AnnAssign(self, node):
        if node.value is not None:
            self.link(node, node.value, CONTEXT)
            self.add_predicated_edge(node, self.cell(node.value, TYPE),
                                     self.cell(node, TYPE))

    def declaration_of(self, read):
        """The annotated binding a name reads through, if it has one."""
        for scope in (self.owners.get(read), None):
            declaration = self.annotated(scope, getattr(read, "id", None))
            if declaration is not None:
                return declaration
        return None

    def annotated_target(self, target):
        """Does this target have a declaration that fixes its type?"""
        declaration = self.resolved.get(target)
        if declaration is None and type(target) is ast.Name:
            declaration = self.annotated(self.function, target.id)
        if declaration is None:
            return False
        return (getattr(declaration, "annotation", None) is not None
                or getattr(declaration, "returns", None) is not None)

    def visit_AugAssign(self, node):
        self.link(node.target, node.value, CONTEXT)
        if not self.annotated_target(node.target):
            self.result_link(node.value, node.target)

    def visit_NamedExpr(self, node):
        self.link(node.value, node.target)
        self.link(node.value, node)

    def visit_Attribute(self, node):
        self.link(node.value, node)
        owner = self.receiver_class(node)
        if owner is None:
            return
        for klass, slot in self.slots.get(node.attr, ()):
            if self.related(klass, owner):
                self.link(slot, node)

    def visit_Subscript(self, node):
        if type(node.slice) is not ast.Slice:
            self.link(node.value, node)
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
        if not isinstance(target, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return None
        if "inline" not in {ast.unparse(d) for d in target.decorator_list}:
            return None
        returns = [n for n in ast.walk(target) if type(n) is ast.Return]
        if len(returns) != 1 or returns[0].value is None:
            return None
        return returns[0].value

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
        for target, skip_self in self.callees(node.func):
            for param, argument in zip(self.parameters(target, skip_self),
                                       node.args):
                if argument not in self.inflow.get(param, ()):
                    self.link(param, argument, CONTEXT)
            if target.returns is not None:
                self.link(target, node)

    def visit_Return(self, node):
        if node.value is not None and self.function is not None:
            self.link(self.function, node.value, CONTEXT)
            if self.inlined_result(self.function) is node.value:
                self.link(node.value, self.function)

    def visit_For(self, node):
        if type(node.target) in (ast.Tuple, ast.List):
            return
        if type(node.target) is not ast.Name:
            return
        self.link(node.iter, node.target)
        iterable = self.resolved.get(node.iter)
        target = self.annotated(self.function, node.target.id)
        if iterable is not None and target is not None:
            self.groups.union(target, iterable)

    visit_AsyncFor = visit_For

    def visit_comprehension_expr(self, node, elements):
        self.generic_visit(node)
        for generator in node.generators:
            if type(generator.target) is ast.Name:
                self.link(generator.iter, generator.target)
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
        for item in [*node.keys, *node.values]:
            self.link(item, node)


    def link_overrides(self):
        """Unions across a class and the bases we can see in this module."""
        for classes in self.classes.values():
            for klass in classes:
                for base in klass.bases:
                    name = base.id if type(base) is ast.Name else None
                    for parent in self.classes.get(name, ()):
                        self.merge_class(klass, parent)

    def merge_class(self, klass, parent):
        inherited = self.members(parent)
        for name, slot in self.members(klass).items():
            other = inherited.get(name)
            if other is None:
                continue
            if type(slot) is ast.AnnAssign and type(other) is ast.AnnAssign:
                self.groups.union(slot, other)
            elif type(slot) in BINDING_STATEMENTS and type(other) in BINDING_STATEMENTS:
                for mine, theirs in zip(self.parameters_of(slot),
                                        self.parameters_of(other)):
                    self.groups.union(mine, theirs)
                if slot.returns is not None and other.returns is not None:
                    self.groups.union(slot, other)

    def members(self, klass):
        found = {}
        for statement in klass.body:
            if type(statement) in BINDING_STATEMENTS:
                found[statement.name] = statement
            elif (type(statement) is ast.AnnAssign
                    and type(statement.target) is ast.Name):
                found[statement.target.id] = statement
        return found


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
            for edges in self.predicated_edges.values():
                topology.update(edges)
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

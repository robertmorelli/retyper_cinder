"""The graph itself: what links to what, and which links a mask can turn on.

Mask-unaware from top to bottom. Nothing here reads an erasure -- `gate_edges`
writes down, once, which node each conditional edge waits on, and that is the
whole of what the later phases need to know about the mask. This is also the
only module allowed to create an edge.
"""
import ast
from dataclasses import dataclass

from annotation_remover import CHECKED
from cinderx_binding import is_primative
from types import SimpleNamespace

TYPE = "type"
CONTEXT = "context"

# What a known callable does to types, in one table instead of four sets.
#   "argument"  the result follows its arguments   (valid_links #20)
#   "own"       it yields its own type whatever goes in  (#20, #65)
#   "receiver"  the result comes out of the receiver     (#72)
#   "element"   the arguments go into the receiver       (#72)
# Only `box` and `unbox` derive from the operand, and now only they are listed
# that way. The other conversions were listed as "argument" too, and
# `decide_type` carried a FIXED_RESULT clause whose whole job was to undo the
# edges that produced -- see valid_links #20.
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
# the calls that exist to change a value's representation
COERCIONS = {"box", "cast"} | {n for n in SIGNATURES if n in (
    "double", "cbool", "int8", "int16", "int32", "int64",
    "uint8", "uint16", "uint32", "uint64")}

BINDING_STATEMENTS = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class Edge:
    """A dependency between two cells, and when it is there.

    `gate` is None for the edges every mask has. Otherwise it names the one
    node whose erasure turns this edge on -- the only shape of condition the
    two mask-aware rules have, once they are written down:

      a narrowing edge from an assignment to a read is there only while the
      read's declaration is erased, because a declaration that survives is
      what the read takes its type from  (#65's neighbour)

      the edge from `x: T = v`'s initializer to the declaration is there only
      while the declaration is erased, because cinderx re-infers `x` from `v`
      exactly then  (valid_links #65)
    """
    source: tuple
    target: tuple
    gate: object = None


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
        self.annotation_units = []
        self.benchmark_units = []
        self.function = None
        self.owners = {}
        self.groups = UnionFind()
        self.bindings = {}
        self.resolved = {}
        self.classes = {}
        self.functions = {}
        self.slots = {}
        self.owning_class = {}
        self.conditions = set()
        self.types = {}
        self._index = None
        # read -> the assignments cinderx would narrow it to, kept aside so
        # settle can drop them for a name whose declaration survives the mask
        self.narrowings = {}
        self.reaching = {}
        self.by_gate = {}
        self._base = None
        if bound is not None:
            self.construct(bound)

    def cell(self, node, slot):
        return (node, slot)

    def add_edge(self, source, target):
        # A cell is no source for itself. cinderx files an inflow from a
        # declaration to its own target where an assignment has no value
        # expression -- a loop target, a comprehension target -- and that edge
        # would decide a position from the half of it being decided.
        if source is not None and target is not None and source[0] is not target[0]:
            self.edges.add(Edge(source, target))

    def link(self, source, target, slot=TYPE):
        """Edge from one expression's type to another expression's cell."""
        self.add_edge(self.cell(source, TYPE), self.cell(target, slot))

    def result_link(self, source, node):
        """The containing expression yields whatever this part yields.

        Once a tag, read by `propagate` to decide which edges a new type could
        be copied along. Nothing has read it since propagate went, so the tag
        is gone and only the edge remains.
        """
        self.link(source, node)

    # ------------------------------------------------------------------ setup

    def construct(self, bound):
        # A name unpacked from an iterator is assigned DYNAMIC, which
        # maybe_set_local_type turns back into the declared type, so the
        # position narrows nothing and a read looks straight through it.
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
        self.resolved = bound.reverse_outflow
        self.types = bound.types
        self.index(bound.tree)
        # The annotations join the linked nodes as roots. A procedure is in no
        # flow -- nothing returns a value from it, nothing resolves a call to
        # it -- so its `-> None` was the one annotation no mask could reach.
        # Having no dependents does not make an annotation unerasable, it makes
        # it trivially gate-neutral, and it partitions into a group of its own.
        roots = ({*bound.outflow, *bound.inflow, *bound.components,
                  *bound.annotation_roots} - {None})
        stored = {node.value: target
                  for node in ast.walk(bound.tree) if type(node) is ast.Assign
                  for target in node.targets if type(target) is ast.Name}
        for root in roots:
            self.groups.find(root)
            for neighbor in filter(None, bound.components.get(root, ())):
                self.groups.union(root, neighbor)  # valid_links #3
            for flows, slot in ((bound.outflow, TYPE), (bound.inflow, CONTEXT)):
                for node in flows.get(root, ()):
                    # #1 where slot is TYPE, #2 where it is CONTEXT: the two
                    # foundational links, drawn from cinderx's own analysis
                    if slot is TYPE:
                        reaching = self.reaching.get(node)
                        if reaching and not any(d is root for d in reaching):
                            # the read takes its type from the assignment that
                            # reaches it; the declaration reaches that
                            # assignment, not the read
                            for definition in reaching:
                                self.add_edge(self.cell(definition, TYPE),
                                          self.cell(node, TYPE))
                                self.narrowings.setdefault(node, []).append(
                                    definition)
                            continue
                    if slot is CONTEXT and node in stored:
                        # cinderx has no node for a Store-context name, so it
                        # reports the declaration demanding the assigned value
                        # directly. Route it through the target the way an
                        # attribute target is routed, so a name that goes
                        # dynamic carries its demand to the value.
                        self.add_edge(self.cell(root, TYPE),
                                  self.cell(stored[node], TYPE))
                        continue
                    self.add_edge(self.cell(root, TYPE), self.cell(node, slot))

        self.tree = bound.tree
        self.visit(bound.tree)
        self.link_overrides()
        self.read_through_targets()
        self.gate_edges()

        ordered = sorted(roots, key=lambda node: (node.lineno, node.col_offset))
        partitions = self.groups.partitions()
        representatives = {}
        for root in ordered:
            representatives.setdefault(self.groups.find(root), root)
        self.roots = list(representatives.values())
        self.annotation_units = [frozenset(partitions[leader])
                                 for leader in representatives]
        self.benchmark_units = self.group_functions()

    def gate_edges(self):
        """Mark the edges a mask can turn on, once, in terms of one node each.

        Every rule that reads the mask ends up here, so `clip` never asks a
        question and nothing downstream of it can add or drop an edge.
        """
        gates = {}
        for read, binders in self.narrowings.items():
            declaration = self.declaration_of(read)
            if declaration is None:
                continue
            for binder in binders:
                if binder is not declaration:
                    gates[Edge(self.cell(binder, TYPE),
                               self.cell(read, TYPE))] = declaration
        for node in ast.walk(self.tree):
            if type(node) is ast.AnnAssign and node.value is not None:
                gates[Edge(self.cell(node.value, TYPE),
                           self.cell(node, TYPE))] = node
        for edge, gate in gates.items():
            if edge in self.edges:
                self.edges.discard(edge)
                self.edges.add(Edge(edge.source, edge.target, gate))
        self.by_gate = {}
        for edge in self.edges:
            if edge.gate is not None:
                self.by_gate.setdefault(edge.gate, []).append(edge)

    def read_through_targets(self):
        """A declaration is read through its target, not out of its annotation.

        `x: T = v` gives the annotation's type to `x`, and every use of `x`
        takes it from there, so the reads hang off the left-hand side and the
        annotation has exactly one outgoing edge.
        """
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
                continue  # the declaration's own demand on its initializer
            self.edges.discard(edge)
            if edge.target != self.cell(node.target, TYPE):
                self.add_edge(self.cell(node.target, TYPE), edge.target)
        for node in moved:
            self.add_edge(self.cell(node, TYPE), self.cell(node.target, TYPE))

    def index(self, tree):
        """Collect names, classes and syntactic marks before the walk.

        Doing this first means a link never depends on where in the file the
        annotation happens to sit.
        """
        for child in ast.iter_child_nodes(tree):
            self.index_node(child, None)

    def index_node(self, node, scope):
        self.owners.setdefault(node, scope)
        if type(node) in BINDING_STATEMENTS:
            return self.index_function(node)
        if type(node) is ast.ClassDef:
            return self.index_class(node, scope)
        self.index_binding(node, scope)
        self.index_marks(node)
        for child in ast.iter_child_nodes(node):
            self.index_node(child, scope)

    def index_function(self, node):
        self.functions.setdefault(node.name, []).append(node)
        for parameter in self.parameters_of(node):
            self.bind(node, parameter.arg, parameter)
            self.owners.setdefault(parameter, node)
        for child in node.body:
            self.index_node(child, node)

    def index_class(self, node, scope):
        """A class body binds attributes, not names in the enclosing scope.

        An attribute can be declared either in the body or as
        `self.x: double = x` inside a method -- nbody writes them only the
        second way, so indexing just the body left those attributes with no
        declaration to lose.
        """
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

    # ------------------------------------------------------------- resolution

    def called_name(self, func):
        if type(func) is ast.Name:
            return func.id
        if type(func) is ast.Attribute:
            return func.attr
        if type(func) is ast.Subscript:
            return self.called_name(func.value)
        return None

    def receiver_class(self, func):
        """The class a method call is made on, from the receiver's type.

        Two unrelated classes can define the same method name -- held_karp has
        `get`, `set` and `offset` in both HeldKarpDP and DistanceMatrix -- so
        matching on the name alone gave the argument parameter edges from both.
        A Name that is itself a class means an explicit `Task.__init__(self,..)`.
        """
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
        """Every definition a call could reach, with whether the first
        parameter is the instance.

        Overrides make a name ambiguous and bailing on that dropped most
        methods, so all candidates are linked; the override unions mean the
        real overrides share a unit anyway. A receiver narrows the set to its
        own class and the classes it inherits from.
        """
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

    # These descend before their own rule runs, which is what every one of
    # them used to do with an explicit generic_visit on its first line.
    DESCEND_FIRST = frozenset(['AnnAssign', 'Assign', 'Attribute', 'AugAssign', 'BinOp', 'BoolOp', 'Call', 'Compare', 'Dict', 'For', 'FormattedValue', 'IfExp', 'List', 'NamedExpr', 'Return', 'Subscript', 'UnaryOp'])

    def visit(self, node):
        if type(node).__name__ in self.DESCEND_FIRST:
            self.generic_visit(node)
        return super().visit(node)

    def generic_visit(self, node):
        # An annotation is a type, not an expression: `CheckedList[Body]` is
        # not a subscript of anything, and the edges the ordinary rules draw
        # inside one form a closed island nothing reads.
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
        # A default value has to satisfy its parameter's annotation.
        # valid_links #42
        params = self.parameters_of(node)
        defaults = node.args.defaults
        for param, default in zip(params[len(params) - len(defaults):], defaults):
            self.link(param, default, CONTEXT)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Name(self, node):
        if type(node.ctx) is not ast.Load or node in self.resolved:
            return
        # valid_links #6 -- cinderx resolves annotated and module level names,
        # so this only covers the bindings it leaves unresolved.
        # cinderx's own reaching definitions: a read takes its type from the
        # assignments that reach it, not from every binding of the name.
        reaching = self.reaching.get(node)
        if reaching:
            for binder in reaching:
                self.link(binder, node)
                self.narrowings.setdefault(node, []).append(binder)
            return
        for scope in (self.function, None):
            binders = self.bindings.get((scope, node.id))
            if binders:
                for binder in binders:
                    if binder is not node:
                        self.link(binder, node)
                        self.narrowings.setdefault(node, []).append(binder)
                return

    def visit_Assign(self, node):
        for target in node.targets:
            if type(target) in (ast.Attribute, ast.Subscript):
                # An attribute or an element has a declared type of its own, so
                # the slot demands the value rather than taking its type.
                self.link(target, node.value, CONTEXT)  # valid_links #64
            elif self.annotated_target(target):
                # A declaration fixes the type, so the store demands the value
                # rather than taking its type -- the same shape as #64. The
                # narrowing edge goes in too: once the mask takes the
                # annotation away, cinderx types the target from the value
                # again, and settle drops the edge while the annotation stands.
                self.link(target, node.value, CONTEXT)
                self.link(node.value, target)
                self.narrowings.setdefault(target, []).append(node.value)
            else:
                # valid_links #4, and #38 for the chained form
                self.link(node.value, target)

    def visit_AnnAssign(self, node):
        if node.value is not None:
            self.link(node, node.value, CONTEXT)  # valid_links #5
            # cinderx narrows a declaration to its initializer whether or not
            # the annotation is there, so the value is an ordinary source of
            # the declaration's type rather than something the annotation
            # overrides. valid_links #65
            self.link(node.value, node)

    def declaration_of(self, read):
        """The annotated binding a name reads through, if it has one."""
        for scope in (self.owners.get(read), None):
            declaration = self.annotated(scope, getattr(read, "id", None))
            if declaration is not None:
                return declaration
        return None

    def annotated_target(self, target):
        """Does this target have a declaration that fixes its type?"""
        # reverse_outflow has no entry for a Store-context name, so this uses
        # the binding index the graph builds itself.
        declaration = self.resolved.get(target)
        if declaration is None and type(target) is ast.Name:
            declaration = self.annotated(self.function, target.id)
        if declaration is None:
            return False
        return (getattr(declaration, "annotation", None) is not None
                or getattr(declaration, "returns", None) is not None)

    def visit_AugAssign(self, node):
        self.link(node.target, node.value, CONTEXT)  # valid_links #12
        # `x += v` is `x = x + v`: the implied result flows back into x, so the
        # value feeds the target's type the way #59 feeds a binop's result and
        # #4 feeds an assignment target. valid_links #67
        #
        # Only when the target has no annotation of its own. `e: double = 0.0`
        # stays a double whatever is assigned into it, and letting a dynamic
        # value drag it down stopped the coercion the double slot still needs.
        # If that annotation is erased the target goes dynamic through the
        # ordinary declaration edge instead.
        if not self.annotated_target(node.target):
            self.result_link(node.value, node.target)

    def visit_NamedExpr(self, node):
        self.link(node.value, node.target)  # valid_links #40
        self.link(node.value, node)  # valid_links #41

    def visit_Attribute(self, node):
        self.link(node.value, node)  # valid_links #7
        # slots are keyed by name, so narrow to the receiver's own class the
        # way callees does, or every `.x` links to every `.x` access
        owner = self.receiver_class(node)
        for klass, slot in self.slots.get(node.attr, ()):
            if owner is None or self.related(klass, owner):
                self.link(slot, node)  # valid_links #8

    def visit_Subscript(self, node):
        if type(node.slice) is not ast.Slice:
            self.link(node.value, node)  # valid_links #9
        # A slice is a plain list whatever it came from -- chklist[B][0:] is a
        # list at runtime, and only the typed path skips the check that says
        # so -- so the container's type is no source for it.
        for part in self.slice_parts(node.slice):
            # valid_links #10 and #11
            self.link(node.value, part, CONTEXT)

    def slice_parts(self, node):
        if type(node) is ast.Slice:
            return [part for part in (node.lower, node.upper, node.step) if part]
        return [node]

    def chain_contexts(self, operands):
        """Each operand is asked for what the one before it was asked for.

        A context is the type a position ends up with once the mediator has
        wrapped it, so operands that have to agree agree on contexts rather
        than on what they happen to yield. The chain runs left to right
        because that is the order the mediator can fix things in: bottom up,
        left to right.
        """
        for left, right in zip(operands, operands[1:]):
            self.add_edge(self.cell(left, CONTEXT), self.cell(right, CONTEXT))

    def visit_BinOp(self, node):
        # The demand on the whole reaches the left operand, and the left hands
        # it on to the right. `Pow` is exempt: cinderx clears the context
        # there, the result is a double whatever went in. valid_links #13
        # The demand on the whole reaches the left operand, and the left hands
        # it on to the right. `Pow` is exempt: cinderx clears the context
        # there, the result is a double whatever went in. valid_links #13
        if type(node.op) is not ast.Pow:
            self.add_edge(self.cell(node, CONTEXT), self.cell(node.left, CONTEXT))
        self.chain_contexts([node.left, node.right])
        # Both operands feed the result, and what they feed it is the type
        # they end up with once the mediator has wrapped them -- their
        # contexts, not what they hold now. `"" * 2` stays a str because
        # nothing was erased there; once either operand is dynamic the result
        # is too, whichever side it was. Left-only left `1 << city` looking
        # primitive after `city` died. valid_links #59
        for part in (node.left, node.right):
            self.add_edge(self.cell(part, CONTEXT), self.cell(node, TYPE))

    def visit_Compare(self, node):
        # the operands agree in series and the last of them says what the
        # comparison itself comes out as. valid_links #14
        self.chain_contexts([node.left, *node.comparators])
        # a comparison is a boolean whatever its operands are -- struck #60 --
        # but it is only a *typed* boolean while they are all typed
        for part in (node.left, *node.comparators):
            self.add_edge(self.cell(part, CONTEXT), self.cell(node, TYPE))
        # no result link: a comparison is a boolean whatever its operands
        # are, so its type never follows them. struck #60.

    def visit_UnaryOp(self, node):
        if type(node.op) is not ast.Not:
            # the kind passes through, so the demand on the whole is the
            # demand on the operand. `not x` is a bool whatever x is, so its
            # demand says nothing about what x is asked for.
            self.add_edge(self.cell(node, CONTEXT), self.cell(node.operand, CONTEXT))
            # A sign change or a bitwise invert keeps its operand's type;
            # `not x` is a boolean whatever x is, which is why struck #61
            # was struck out and this visitor went with it. valid_links #68
            self.result_link(node.operand, node)

    def visit_BoolOp(self, node):
        # `a and b` is the union of its arms, and a union of a primitive with
        # anything else is not a type cinderx will build. So the arms agree in
        # series and the last of them decides what the whole yields.
        # valid_links #15 and #62
        # `a and b` is the union of its arms, and a union of a primitive with
        # anything else is not a type cinderx will build. So the arms agree in
        # series and the last of them decides what the whole yields.
        # valid_links #15 and #62
        self.chain_contexts(node.values)
        # every arm feeds the result: it is one of them
        for value in node.values:
            self.add_edge(self.cell(value, CONTEXT), self.cell(node, TYPE))

    def visit_IfExp(self, node):
        self.chain_contexts([node.body, node.orelse])  # valid_links #16
        for arm in (node.body, node.orelse):
            self.result_link(arm, node)  # valid_links #63

    def visit_FormattedValue(self, node):
        # Formatting takes an object, so the interpolated value has to box
        # whatever primitive it still holds. valid_links #57
        self.add_edge(self.cell(node, TYPE), self.cell(node.value, CONTEXT))

    def inlined_result(self, target):
        """The single expression an `@inline` function hands back, or None.

        Only one return, and it has to return a value: cinderx inlines a body
        it can substitute as an expression, and a function with two exits has
        no one expression the call site could be said to yield.
        """
        if not isinstance(target, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return None
        if "inline" not in {ast.unparse(d) for d in target.decorator_list}:
            return None
        returns = [n for n in ast.walk(target) if type(n) is ast.Return]
        if len(returns) != 1 or returns[0].value is None:
            return None
        return returns[0].value

    def visit_Call(self, node):
        self.link(node.func, node)  # valid_links #17
        for argument in node.args:
            # A call whose callee lost its type is a dynamic call, and a
            # dynamic call takes objects: the arguments have to box whatever
            # the surviving parameter annotations still ask for.
            if self.called_name(node.func) not in COERCIONS:
                # A coercion's parameter demand is not the program's demand:
                # `box(i)` says box takes a primitive, not that `i` is asked
                # for one. Wrapping the argument to satisfy it builds a layer
                # inside a tower that the tower's own rebuild then has to
                # undo. valid_links #66
                self.link(node.func, argument, CONTEXT)
        if self.called_name(node.func) in CINDER_CONVERSIONS:
            for argument in node.args:
                self.link(argument, node)  # valid_links #20
            return
        name = self.called_name(node.func)
        if type(node.func) is ast.Attribute:
            kind = SIGNATURES.get(name)
            if kind == "receiver":
                self.result_link(node.func.value, node)  # valid_links #72
            elif kind == "element":
                for argument in node.args:
                    self.link(node.func.value, argument, CONTEXT)  # #72
        # An unresolved call is a builtin as far as we know, and builtins take
        # boxed objects, so the arguments carry no demand.
        for target, skip_self in self.callees(node.func):
            for param, argument in zip(self.parameters(target, skip_self),
                                       node.args):
                # valid_links #18 and #19
                self.link(param, argument, CONTEXT)
            if target.returns is not None:
                self.link(target, node)  # valid_links #22

    def visit_Return(self, node):
        if node.value is not None and self.function is not None:
            self.link(self.function, node.value, CONTEXT)  # valid_links #21
            if self.inlined_result(self.function) is node.value:
                # cinderx substitutes an @inline body, so what the body
                # produces is what the function is. The edge stops at the
                # annotation rather than running on to the call sites: those
                # are already fed from here, and a call whose receiver went
                # dynamic yields a boxed value however the body is written.
                self.link(node.value, self.function)

    def visit_For(self, node):
        if type(node.target) in (ast.Tuple, ast.List):
            # `for b1, b2 in pairs` narrows nothing: assign_value only spreads
            # element types when the source is a literal tuple, so every name
            # unpacked from an iterator is dynamic whatever the container
            # holds, and the annotation on it is all there is. valid_links #69
            return
        if type(node.target) is not ast.Name:
            return
        self.link(node.iter, node.target)  # valid_links #23
        iterable = self.resolved.get(node.iter)
        target = self.annotated(self.function, node.target.id)
        if iterable is not None and target is not None:
            self.groups.union(target, iterable)  # valid_links #24

    visit_AsyncFor = visit_For

    def visit_comprehension_expr(self, node, elements):
        self.generic_visit(node)
        for generator in node.generators:
            if type(generator.target) is ast.Name:
                self.link(generator.iter, generator.target)  # valid_links #25
        for element in elements:
            self.link(element, node)  # valid_links #26
            # and the demand back down: a CheckedList context hands its
            # element type to the payload, which visitListComp does by
            # visiting the element expecting it. Context to context -- what the
            # comprehension is asked for decides what its elements are asked
            # for, whatever type it happens to hold.
            self.add_edge(self.cell(node, CONTEXT), self.cell(element, CONTEXT))

    def visit_ListComp(self, node):
        self.visit_comprehension_expr(node, [node.elt])

    visit_SetComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_DictComp(self, node):
        self.visit_comprehension_expr(node, [node.key, node.value])

    def visit_List(self, node):
        for item in node.elts:
            self.link(item, node)  # valid_links #27

    visit_Set = visit_List
    visit_Tuple = visit_List

    def visit_Dict(self, node):
        for item in [*node.keys, *node.values]:
            self.link(item, node)  # valid_links #27

    # ----------------------------------------------------------- inheritance

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
                self.groups.union(slot, other)  # valid_links #28
            elif type(slot) in BINDING_STATEMENTS and type(other) in BINDING_STATEMENTS:
                for mine, theirs in zip(self.parameters_of(slot),
                                        self.parameters_of(other)):
                    self.groups.union(mine, theirs)  # valid_links #29
                if slot.returns is not None and other.returns is not None:
                    self.groups.union(slot, other)  # valid_links #30

    def members(self, klass):
        found = {}
        for statement in klass.body:
            if type(statement) in BINDING_STATEMENTS:
                found[statement.name] = statement
            elif (type(statement) is ast.AnnAssign
                    and type(statement.target) is ast.Name):
                found[statement.target.id] = statement
        return found

    # ---------------------------------------------------------------- results

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
        """One pass over the edges, indexed both ways.

        `outgoing` maps a cell to what it feeds; the other two map a node to
        the cells feeding each of its slots. A feed is a cell rather than a
        node because an edge can leave either slot: a context to context edge
        says what a position is asked for follows from what its parent is
        asked for, and reading that off the parent's type would decide it
        against the wrong half.
        """
        if self._index is None:
            outgoing, by_type, by_context = {}, {}, {}
            for edge in self.edges:
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

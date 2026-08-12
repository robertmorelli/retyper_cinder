"""Build the type/context graph.

Every expression has two cells: `(node, TYPE)` for the type it holds and
`(node, CONTEXT)` for what the surrounding code expects of it. Links are
either an edge, always leaving a TYPE cell, or a union, which puts two
annotations in the same erasure unit.

Each link below cites the item it implements in valid_links.md.
"""
import ast
from dataclasses import dataclass

from anno_remover import CHECKED, _checked_ctor
from get_ast_data import is_primative
from types import SimpleNamespace

TYPE = "type"
CONTEXT = "context"

# What a known callable does to types, in one table instead of four sets.
#   "argument"  the result follows its arguments   (valid_links #20)
#   "own"       it yields its own type whatever goes in  (#20, #65)
#   "receiver"  the result comes out of the receiver     (#72)
#   "element"   the arguments go into the receiver       (#72)
# Every cinder conversion is listed as "argument" even though only box and
# unbox truly derive from the operand: marking `int64(x)` dynamic is the only
# thing currently stopping the patcher boxing a stale int64, and narrowing it
# costs 11 failures. See valid_links #20.
SIGNATURES = dict.fromkeys(
    ("box", "unbox", "clen", "cbool", "double", "Array", "cast",
     "int8", "int16", "int32", "int64",
     "uint8", "uint16", "uint32", "uint64"), "argument")
SIGNATURES.update(dict.fromkeys(("pop", "get", "copy", "index", "count"),
                                "receiver"))
SIGNATURES.update(dict.fromkeys(("append", "add", "insert", "extend",
                                 "remove", "discard"), "element"))
CINDER_CONVERSIONS = {n for n, k in SIGNATURES.items() if k == "argument"}

# What the cinder intrinsics yield, looked up rather than guessed from the
# argument. These give their own type whatever goes in, so an erased argument
# never makes the call dynamic. `box` and `unbox` are absent on purpose: their
# result really is derived from the argument. valid_links #20 and #65.
FIXED_RESULT = {
    "clen", "cbool", "double", "cast", "Array",
    "int8", "int16", "int32", "int64",
    "uint8", "uint16", "uint32", "uint64",
}

BINDING_STATEMENTS = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class Edge:
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


class Graph(ast.NodeVisitor):
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
        self.operands = set()
        self.types = {}
        self._index = None
        self.tagged = {"result": set(), "sibling": set()}
        if bound is not None:
            self.construct(bound)

    def cell(self, node, slot):
        return (node, slot)

    def flow(self, source, target):
        if source is not None and target is not None:
            self.edges.add(Edge(source, target))

    def link(self, source, target, slot=TYPE, kind=None):
        """Edge from one expression's type to another expression's cell.

        `kind` marks the two edges that mean more than reachability:
        "result" -- the containing expression yields whatever this part
        yields, so a type may be copied along it. An attribute or a subscript
        is not like that. "sibling" -- the demand comes from an operand
        rather than a declaration, which decide_context treats differently.
        """
        self.flow(self.cell(source, TYPE), self.cell(target, slot))
        if kind is not None:
            self.tagged[kind].add((self.cell(source, TYPE),
                                   self.cell(target, slot)))

    def result_link(self, source, node):
        self.link(source, node, kind="result")

    # ------------------------------------------------------------------ setup

    def construct(self, bound):
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
        for root in roots:
            self.groups.find(root)
            for neighbor in filter(None, bound.components.get(root, ())):
                self.groups.union(root, neighbor)  # valid_links #3
            for flows, slot in ((bound.outflow, TYPE), (bound.inflow, CONTEXT)):
                for node in flows.get(root, ()):
                    # #1 where slot is TYPE, #2 where it is CONTEXT: the two
                    # foundational links, drawn from cinderx's own analysis
                    self.flow(self.cell(root, TYPE), self.cell(node, slot))

        self.visit(bound.tree)
        self.link_overrides()

        ordered = sorted(roots, key=lambda node: (node.lineno, node.col_offset))
        partitions = self.groups.partitions()
        representatives = {}
        for root in ordered:
            representatives.setdefault(self.groups.find(root), root)
        self.roots = list(representatives.values())
        self.annotation_units = [frozenset(partitions[leader])
                                 for leader in representatives]
        self.benchmark_units = self.group_functions()

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
        """Positions the later rules ask about: conditions and operands."""
        if type(node) in (ast.If, ast.While, ast.IfExp):
            self.conditions.add(node.test)
        if type(node) is ast.Compare:
            self.operands.add(node.left)
            self.operands.update(node.comparators)

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
        for scope in (self.function, None):
            binders = self.bindings.get((scope, node.id))
            if binders:
                for binder in binders:
                    if binder is not node:
                        self.link(binder, node)
                return

    def visit_Assign(self, node):
        for target in node.targets:
            if type(target) in (ast.Attribute, ast.Subscript):
                # An attribute or an element has a declared type of its own, so
                # the slot demands the value rather than taking its type.
                self.link(target, node.value, CONTEXT)  # valid_links #64
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
        self.link(node.value, node)  # valid_links #9
        for part in self.slice_parts(node.slice):
            # valid_links #10 and #11
            self.link(node.value, part, CONTEXT)

    def slice_parts(self, node):
        if type(node) is ast.Slice:
            return [part for part in (node.lower, node.upper, node.step) if part]
        return [node]

    def siblings(self, operands, tolerant=True):
        """Operands constrain each other.

        `tolerant` was tried as a way to exempt arithmetic from the clause in
        decide_context, on the theory that a comparison can let both sides go
        dynamic while `dynamic / 2.0` must coerce one. It clears nbody's three
        and costs 81 across deltablue, held_karp and richards -- the clause is
        load bearing for arithmetic operands too. Left in place, always true.
        """
        for left in operands:
            for right in operands:
                if left is not right:
                    self.link(left, right, CONTEXT, kind="sibling")

    def visit_BinOp(self, node):
        self.siblings([node.left, node.right])  # valid_links #13
        # Both operands feed the result. `"" * 2` stays a str because nothing
        # was erased there and decide_type keeps the original type; but once
        # either operand is dynamic the result is too, whichever side it was.
        # Left-only left `1 << city` looking primitive after `city` died.
        self.result_link(node.left, node)   # valid_links #59
        self.result_link(node.right, node)  # valid_links #59

    def visit_Compare(self, node):
        self.siblings([node.left, *node.comparators])  # valid_links #14
        # no result link: a comparison is a boolean whatever its operands
        # are, so its type never follows them. struck #60.

    def visit_UnaryOp(self, node):
        if type(node.op) is not ast.Not:
            # A sign change or a bitwise invert keeps its operand's type;
            # `not x` is a boolean whatever x is, which is why struck #61
            # was struck out and this visitor went with it. valid_links #68
            self.result_link(node.operand, node)

    def visit_BoolOp(self, node):
        self.siblings(node.values)  # valid_links #15
        for arm in node.values:
            self.result_link(arm, node)  # valid_links #62

    def visit_IfExp(self, node):
        self.siblings([node.body, node.orelse])  # valid_links #16
        for arm in (node.body, node.orelse):
            self.result_link(arm, node)  # valid_links #63

    def visit_FormattedValue(self, node):
        # Formatting takes an object, so the interpolated value has to box
        # whatever primitive it still holds. valid_links #57
        self.flow(self.cell(node, TYPE), self.cell(node.value, CONTEXT))

    def inlines_into(self, root, node):
        """Is this the cinderx outflow edge from an @inline to one of its calls?

        Only a call: an @inline function is still a value elsewhere -- a name
        that is read, passed or stored takes its declared type as usual, and
        only the call site sees the substituted body.
        """
        return (type(node) is ast.Call
                and self.inlined_result(root) is not None
                and any(target is root for target, _ in self.callees(node.func)))

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
            self.link(node.func, argument, CONTEXT)  # valid_links #66
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
            # `for b1, b2 in pairs` bound nothing at all before this: every
            # element target was invisible to the graph. valid_links #69
            for element in node.target.elts:
                self.link(node.iter, element)
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
        the expressions feeding each of its slots.
        """
        if self._index is None:
            outgoing, by_type, by_context = {}, {}, {}
            for edge in self.edges:
                outgoing.setdefault(edge.source, []).append(edge.target)
                node, slot = edge.target
                table = by_context if slot == CONTEXT else by_type
                table.setdefault(node, []).append(edge.source[0])
            self._index = (outgoing, by_type, by_context)
        return self._index

    def outgoing(self):
        return self.index_edges()[0]

    def propagate(self, node, produced, types, contexts):
        """Carry a patched position's new type to whatever demanded it.

        A coercion changes what this position yields, and the sibling links put
        the other half of any pair that has to agree one edge away -- so the
        far side is told the real type rather than being decided against a
        stale one.
        """
        source = self.cell(node, TYPE)
        for target in self.outgoing().get(source, ()):
            where, slot = target
            if slot == CONTEXT:
                contexts[where] = produced
            elif (source, target) in self.tagged["result"]:
                types[where] = produced

    def sources(self):
        """Each node's feeds, split by slot."""
        return self.index_edges()[1:]

    def survives_erasure(self, node, bound):
        """Does this annotation's target keep a type once the annotation goes?

        Mirrors type_binder.py: narrowing lives in type_state.local_types,
        which is per scope, so it only reaches a read in the scope that
        assigned it.
        """
        if type(node) is not ast.AnnAssign or node.value is None:
            return False   # a parameter, a return, a bare `x: int64`
        if type(node.target) is not ast.Name:
            return False   # visitAttribute goes through the class slot
        if self.owners.get(node) is None:
            return False   # a global read in a function uses the declaration
        if _checked_ctor(node.annotation, node.value) is not None:
            # anno_remover rewrites `todo: CheckedList[C] = [...]` into an
            # explicit CheckedList[C]([...]) constructor, so the container
            # keeps its type through erasure and everything read out of it
            # stays typed. valid_links #71
            return True
        if not self.narrows(bound.types.get(node.value), bound):
            return False   # maybe_set_local_type: a DYNAMIC value never narrows
        # can_be_narrowed is False on CType: a primitive must box, not recover
        return (not self.machine(bound.types.get(node.value))
                and not self.machine(bound.types.get(node.target)))

    def narrows(self, value, bound):
        """A value narrows the name it is assigned to unless it is dynamic.

        `maybe_set_local_type` falls back to the declared type when the value
        is DYNAMIC, and the declared type is what erasure just removed.
        """
        if value is None or value is bound.dynamic:
            return False
        try:
            return value.klass.can_be_narrowed
        except Exception:
            return True

    def machine(self, value):
        """An unboxed type, which an erased slot genuinely cannot hold."""
        try:
            return value is not None and is_primative(value)
        except Exception:
            return False

    def must_agree(self, node):
        """Is this a position where the two sides have to match?

        A comparison's result is not coerced, so its operands must already
        agree and neither can be moved. Arithmetic, an assignment and a call
        argument all coerce the result, so one side may move to meet the
        other. Both rewriters ask this before boxing a machine literal, and
        `decide_context` uses the same distinction for sibling demands.
        """
        return node in self.operands

    def type_parts(self, node, feeds):
        """What an expression's type depends on.

        A boolean operator yields one of its arms. A comparison has no
        incoming type edge -- its type never follows an operand -- so its
        operands are read directly; comparing two dynamics yields a dynamic,
        not a bool, and claiming otherwise held its sibling at cbool and
        produced Union[dynamic, cbool]. Everything else depends on whatever
        feeds its type cell.
        """
        if type(node) is ast.BoolOp:
            return node.values
        if type(node) is ast.Compare:
            return (node.left, *node.comparators)
        return feeds

    def decide_type(self, node, feeds, dead, bound):
        """What this expression yields.

        An AND over its parts: typed only while everything it depends on is.
        No rule for a machine constant here -- struck #70, see the notes.
        """
        if self.called_name(getattr(node, "func", None)) in FIXED_RESULT:
            # an intrinsic gives its own type whatever the argument became
            return bound.types.get(node)
        if any(part in dead for part in self.type_parts(node, feeds)):
            return bound.dynamic
        return bound.types.get(node)

    def decide_context(self, node, feeds, dead, bound):
        """What is expected here. One dead feed is enough.

        An OR over live demands is the honest reading and costs 38 failures;
        see valid_bindings_notes.md, "Rules that were tried and measured
        wrong".
        """
        if any(feed in dead for feed in feeds):
            return bound.dynamic
        if (node in self.conditions and node in dead
                and self.machine(bound.type_contexts.get(node))):
            # A condition that lost its type cannot be asked for a machine
            # boolean; the demand is what makes the patcher wrap it in
            # cbool(). Scoped to conditions on purpose -- the unscoped version
            # of this rule cost 143. valid_links #35 and #36
            return bound.dynamic
        if (node in dead and self.machine(bound.type_contexts.get(node))
                and all((self.cell(feed, TYPE), self.cell(node, CONTEXT))
                        in self.tagged["sibling"] for feed in feeds)):
            # the demand comes from an operand, not a declaration: a
            # dynamic value cannot be coerced up to meet one, so the other
            # side boxes instead. Three attempts to exempt literal or
            # arithmetic siblings each cost ~83; see the notes.
            return bound.dynamic
        return bound.type_contexts.get(node)

    def classify(self, erased, bound):
        """Split the erased annotations by what erasure leaves them with.

        rebuilt   anno_remover puts the type back, whatever the mask did --
                  a checked container built from a literal. Never dynamic.
        recovers  cinderx re-infers it from its initializer, but only while
                  that initializer still has a type, so it joins the fixpoint
                  and the #65 edge can still kill it.
        seeds     nothing to infer from: a parameter, a return, a bare
                  `x: int64`. Dynamic outright.
        """
        rebuilt, recovers, seeds = set(), set(), set()
        for node in erased:
            if (type(node) is ast.AnnAssign
                    and _checked_ctor(node.annotation, node.value) is not None):
                rebuilt.add(node)
            elif self.survives_erasure(node, bound):
                recovers.add(node)
            else:
                seeds.add(node)
        return rebuilt, recovers, seeds

    def settle(self, bound, erased=()):
        by_type, by_context = self.sources()
        _, recovers, seeds = self.classify(erased, bound)
        # Every typed node is decided, not only the ones an edge points at: a
        # comparison has no incoming type edge, so restricting this to edge
        # targets meant its rule never ran and it kept a cbool it no longer
        # had. Recoverable declarations join them so a dead initializer can
        # still take the recovery back.
        decided_nodes = list(bound.types) + list(recovers)

        dead = set(seeds)
        pending = True
        while pending:
            pending = False
            for node in decided_nodes:
                if node in dead:
                    continue
                if self.decide_type(node, by_type.get(node, ()), dead,
                                    bound) is bound.dynamic:
                    dead.add(node)
                    pending = True

        types = {node: (bound.dynamic if node in dead else value)
                 for node, value in bound.types.items()}
        for node in decided_nodes:
            if node not in dead:
                decided = self.decide_type(node, by_type.get(node, ()), dead,
                                           bound)
                if decided is not None:
                    types[node] = decided

        # Only the erased annotations themselves lose their demand. A node
        # whose type went dynamic still has whatever its consumers ask of it.
        contexts = dict(bound.type_contexts)
        for node in seeds:
            contexts[node] = bound.dynamic
        for node, feeds in by_context.items():
            if node in contexts and node not in seeds:
                contexts[node] = self.decide_context(node, feeds, dead, bound)
        return SimpleNamespace(types=types, contexts=contexts)

    def coercion(self, node):
        """Report an author-written cinder coercion and what it wraps.

        The patcher uses this to drop a coercion that erasure has made
        pointless. Returning None unconditionally, as this used to, meant that
        never happened.
        """
        if type(node) is not ast.Call or len(node.args) != 1:
            return None
        name = self.called_name(node.func)
        if name in CINDER_CONVERSIONS or name in FIXED_RESULT:
            return name, node.args[0]
        return None


def build_binding_graph(bound):
    return Graph(bound)

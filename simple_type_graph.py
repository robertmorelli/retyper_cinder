"""Build the type/context graph.

Every expression has two cells: `(node, TYPE)` for the type it holds and
`(node, CONTEXT)` for what the surrounding code expects of it. Links are
either an edge, always leaving a TYPE cell, or a union, which puts two
annotations in the same erasure unit.

Each link below cites the item it implements in valid_links.md.
"""
import ast
from dataclasses import dataclass

from get_ast_data import is_primative
from types import SimpleNamespace

TYPE = "type"
CONTEXT = "context"

# Cinder conversions. Strictly, only `box` and `unbox` give a result derived
# from the argument -- `int64(x)` is an int64 whatever goes in. But narrowing
# this set to those two costs 11 more failures, because marking `int64(x)`
# dynamic is currently the only thing stopping the patcher from boxing a
# stale int64. See valid_links #20.
CINDER_CONVERSIONS = {
    "box", "unbox", "clen", "cbool", "double", "Array", "cast",
    "int8", "int16", "int32", "int64",
    "uint8", "uint16", "uint32", "uint64",
}

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
        self._outgoing = None
        self.result_edges = set()
        self._sources = None
        if bound is not None:
            self.construct(bound)

    def cell(self, node, slot):
        return (node, slot)

    def flow(self, source, target):
        if source is not None and target is not None:
            self.edges.add(Edge(source, target))

    def link(self, source, target, slot=TYPE):
        """Edge from one expression's type to another expression's cell."""
        self.flow(self.cell(source, TYPE), self.cell(target, slot))

    def result_link(self, source, node):
        """A compound expression's type follows the part that decides it.

        Recorded separately because these are the only edges along which a
        type may be copied: the containing expression yields whatever this
        part yields. An attribute or a subscript is not like that.
        """
        self.link(source, node)
        self.result_edges.add((self.cell(source, TYPE), self.cell(node, TYPE)))

    # ------------------------------------------------------------------ setup

    def construct(self, bound):
        self.resolved = bound.reverse_outflow
        self.index(bound.tree)
        roots = {*bound.outflow, *bound.inflow, *bound.components} - {None}
        for root in roots:
            self.groups.find(root)
            for neighbor in filter(None, bound.components.get(root, ())):
                self.groups.union(root, neighbor)  # valid_links #3
            for flows, slot in ((bound.outflow, TYPE), (bound.inflow, CONTEXT)):
                for node in flows.get(root, ()):
                    # valid_links #1 and #2
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
        """Collect every name binding, class, and function ahead of the walk.

        Doing this first means a link never depends on where in the file the
        annotation happens to sit.
        """
        def walk(node, scope):
            self.owners.setdefault(node, scope)
            if type(node) in BINDING_STATEMENTS:
                self.functions.setdefault(node.name, []).append(node)
                for parameter in self.parameters_of(node):
                    self.bind(node, parameter.arg, parameter)
                    self.owners.setdefault(parameter, node)
                for child in node.body:
                    walk(child, node)
                return
            if type(node) is ast.ClassDef:
                self.classes.setdefault(node.name, []).append(node)
                for statement in node.body:
                    # A class body binds attributes, not names in the scope
                    # that encloses the class.
                    if (type(statement) is ast.AnnAssign
                            and type(statement.target) is ast.Name):
                        self.slots.setdefault(
                            statement.target.id, []).append(statement)
                        self.owners.setdefault(statement, scope)
                    else:
                        walk(statement, scope)
                return
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
            for child in ast.iter_child_nodes(node):
                walk(child, scope)
        for child in ast.iter_child_nodes(tree):
            walk(child, None)

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

    def callees(self, func):
        """Every definition a call could reach, paired with whether the first
        parameter is the instance.

        Overrides make a name ambiguous, and bailing on that dropped most
        methods. Linking to all of them is the conservative reading, and the
        override unions mean the real overrides share a unit anyway.
        """
        name = self.called_name(func)
        found = []
        for klass in self.classes.get(name, ()):
            for statement in klass.body:
                if (type(statement) in BINDING_STATEMENTS
                        and statement.name == "__init__"):
                    found.append((statement, True))
        if found:
            return found
        return [(match, type(func) is ast.Attribute)
                for match in self.functions.get(name, ())]

    def parameters(self, target, skip_self):
        params = self.parameters_of(target)
        if skip_self and params and params[0].arg == "self":
            params = params[1:]
        return params

    # ---------------------------------------------------------------- visitors

    def visit_FunctionDef(self, node):
        outer = self.function
        self.function = node
        self.generic_visit(node)
        self.function = outer

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
        self.generic_visit(node)
        for target in node.targets:
            if type(target) in (ast.Attribute, ast.Subscript):
                # An attribute or an element has a declared type of its own, so
                # the slot demands the value rather than taking its type.
                self.link(target, node.value, CONTEXT)  # valid_links #64
            else:
                # valid_links #4, and #38 for the chained form
                self.link(node.value, target)

    def visit_AnnAssign(self, node):
        self.generic_visit(node)
        if node.value is not None:
            self.link(node, node.value, CONTEXT)  # valid_links #5
            # cinderx narrows a declaration to its initializer whether or not
            # the annotation is there, so the value is an ordinary source of
            # the declaration's type rather than something the annotation
            # overrides. valid_links #65
            self.link(node.value, node)

    def visit_AugAssign(self, node):
        self.generic_visit(node)
        self.link(node.target, node.value, CONTEXT)  # valid_links #12

    def visit_NamedExpr(self, node):
        self.generic_visit(node)
        self.link(node.value, node.target)  # valid_links #40
        self.link(node.value, node)  # valid_links #41

    def visit_Attribute(self, node):
        self.generic_visit(node)
        self.link(node.value, node)  # valid_links #7
        for slot in self.slots.get(node.attr, ()):
            self.link(slot, node)  # valid_links #8

    def visit_Subscript(self, node):
        self.generic_visit(node)
        self.link(node.value, node)  # valid_links #9
        for part in self.slice_parts(node.slice):
            # valid_links #10 and #11
            self.link(node.value, part, CONTEXT)

    def slice_parts(self, node):
        if type(node) is ast.Slice:
            return [part for part in (node.lower, node.upper, node.step) if part]
        return [node]

    def siblings(self, operands):
        for left in operands:
            for right in operands:
                if left is not right:
                    self.link(left, right, CONTEXT)

    def visit_BinOp(self, node):
        self.generic_visit(node)
        self.siblings([node.left, node.right])  # valid_links #13
        # The left operand decides the result: `"" * 2` is a str and `[1] * 2`
        # is a list, whatever the count on the right is.
        self.result_link(node.left, node)  # valid_links #59

    def visit_Compare(self, node):
        self.generic_visit(node)
        self.siblings([node.left, *node.comparators])  # valid_links #14
        # No result link: a comparison is a boolean whatever its operands are,
        # so its type does not follow them. See valid_links #60.

    def visit_BoolOp(self, node):
        self.generic_visit(node)
        self.siblings(node.values)  # valid_links #15
        for arm in node.values:
            self.result_link(arm, node)  # valid_links #62

    def visit_IfExp(self, node):
        self.generic_visit(node)
        self.siblings([node.body, node.orelse])  # valid_links #16
        for arm in (node.body, node.orelse):
            self.result_link(arm, node)  # valid_links #63

    def visit_Call(self, node):
        self.generic_visit(node)
        self.link(node.func, node)  # valid_links #17
        if self.called_name(node.func) in CINDER_CONVERSIONS:
            for argument in node.args:
                self.link(argument, node)  # valid_links #20
            return
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
        self.generic_visit(node)
        if node.value is not None and self.function is not None:
            self.link(self.function, node.value, CONTEXT)  # valid_links #21

    def visit_For(self, node):
        self.generic_visit(node)
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
        self.generic_visit(node)
        for item in node.elts:
            self.link(item, node)  # valid_links #27

    visit_Set = visit_List
    visit_Tuple = visit_List

    def visit_Dict(self, node):
        self.generic_visit(node)
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

    def rewrite(self, table, slot, dynamic, dead):
        return {
            node: dynamic if (node, slot) in dead else value
            for node, value in table.items()
        }

    def outgoing(self):
        if self._outgoing is None:
            self._outgoing = {}
            for edge in self.edges:
                self._outgoing.setdefault(edge.source, []).append(edge.target)
        return self._outgoing

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
            elif (source, target) in self.result_edges:
                types[where] = produced

    def sources(self):
        """For each cell, every expression feeding it, split by slot."""
        if self._sources is None:
            self._sources = ({}, {})
            for edge in self.edges:
                node, slot = edge.target
                table = self._sources[1] if slot == CONTEXT else self._sources[0]
                table.setdefault(node, []).append(edge.source[0])
        return self._sources

    def machine(self, value):
        """An unboxed type, which an erased slot genuinely cannot hold."""
        try:
            return value is not None and is_primative(value)
        except Exception:
            return False

    def decide_type(self, node, feeds, dead, bound):
        """What this expression yields, given everything feeding it."""
        if self.called_name(getattr(node, "func", None)) in FIXED_RESULT:
            # an intrinsic gives its own type whatever the argument became
            return bound.types.get(node)
        if type(node) is ast.BoolOp:
            # the result is one of the arms, so it is their join: dynamic as
            # soon as any arm is
            if any(arm in dead for arm in node.values):
                return bound.dynamic
            return bound.types.get(node)
        if type(node) is ast.Compare:
            # A comparison has no incoming type edge -- its type never follows
            # an operand -- so the AND is applied to the operands directly.
            # Comparing two dynamics yields a dynamic, not a bool: claiming a
            # bool here left the comparison looking typed, which held its
            # sibling at cbool and produced Union[dynamic, cbool].
            if any(operand in dead
                   for operand in (node.left, *node.comparators)):
                return bound.dynamic
            return bound.types.get(node)
        if any(feed in dead for feed in feeds):
            return bound.dynamic
        return bound.types.get(node)

    def decide_context(self, node, feeds, dead, bound):
        """What is expected here. One dead feed is enough.

        An OR would be the honest reading -- a slot is typed while anything
        typed still asks for it -- but it cannot be expressed here. There is
        one context cell per node and a node can have several consumers
        wanting different things at once: `city` passed both to a parameter
        whose annotation was erased and to one that survived. The OR keeps the
        surviving demand, no coercion is inserted for the erased one, and a
        primitive reaches a dynamic slot. Taking the weakest demand instead
        over-boxes, which the stronger consumer can re-coerce. Measured at 38
        fewer failures. The real fix is a context per use, not per node.
        """
        if any(feed in dead for feed in feeds):
            return bound.dynamic
        return bound.type_contexts.get(node)

    def settle(self, bound, erased=()):
        by_type, by_context = self.sources()
        # Every typed node gets decided, not only the ones an edge points at.
        # A comparison has no incoming type edge -- its type never follows its
        # operands -- so restricting this to edge targets meant its rule never
        # ran and it kept a cbool it no longer had.
        decided_nodes = list(bound.types)
        # An erased annotation is not automatically dynamic. `x: T = v` keeps
        # whatever `v` yields, because that is what cinderx infers once the
        # annotation is gone. Only an annotation with nothing to infer from --
        # a parameter, a return, a bare `x: int64` -- is dynamic outright.
        # Recovery is only valid when the initializer actually reproduces the
        # annotation's type. `out: Variable = self.output()` does. But
        # `taskTab: List[Task] = [None] * N` infers as a plain list, so the
        # slot really does lose its type and everything reached through it
        # has to know. A machine type never recovers: a primitive cannot sit
        # in a dynamic slot at all.
        recoverable = {
            node for node in erased
            if type(node) is ast.AnnAssign and node.value is not None
            and not self.machine(bound.types.get(node.value))
            and not self.machine(bound.types.get(node.target))
            and bound.types.get(node.value) is bound.types.get(node.target)
            and bound.types.get(node.target) is not None
        }
        seeds = set(erased) - recoverable
        dead = set(seeds)
        pending = True
        while pending:
            pending = False
            for node in decided_nodes:
                if node in dead:
                    continue
                feeds = by_type.get(node, ())
                if self.decide_type(node, feeds, dead, bound) is bound.dynamic:
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

        contexts = dict(bound.type_contexts)
        # only the erased annotations themselves lose their demand; a node
        # whose type went dynamic still has whatever its consumers ask of it
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

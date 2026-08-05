"""Post-hoc type/context dependency graph.

A vertex is (ast node, is_ctx): False is the node's type slot, True is the
context imposed on it. For a FunctionDef the type slot means its return type,
for an arg it means the parameter's declared type.

Edges are directed and mean "flows into": g[v] is the list of vertices v feeds.
Types flow up (operand -> expression, value -> target), contexts flow down
(target -> value, expression -> operand).
"""
from ast import (AnnAssign, Attribute, ClassDef, Constant, FunctionDef,
                 AsyncFunctionDef, List, Load, Name, NodeVisitor, Subscript,
                 Tuple, walk)
from dunder_resolver import resolve_user, user_method
from fun_grouping import func_of

from cinderx.compiler.static.types import CType

TYPE, CTX = False, True

# What an edge *means*, as opposed to merely that it exists. The typedness
# solver ignores these; the type solver dispatches on them.
VALUE = "value"          # src's type is dst's type
ELEM = "elem"            # dst is an element drawn out of the container src
RETURN = "return"        # src is the callee whose return type dst takes
RECEIVER = "receiver"    # src only gates dst: dynamic receiver, dynamic result
FIELD = "field"          # src is the declaration of the field dst reads
BINOP = "binop"          # src is an operand of dst; the op decides the result
CMPOP = "cmpop"
UNARY = "unary"
BRANCH = "branch"        # src is one arm of a merge: join, do not overwrite
COERCE = "coerce"        # src is the operand of a coercion: box(x), int64(x)
ELTS = "elts"            # src is an element of dst, a container literal

# The coercions written in the source. Their result is not a mystery -- the
# binder knows exactly what `box` does to a given operand -- so they take an
# edge from their operand and are re-decided, rather than being frozen at the
# type they had before erasure.
COERCIONS = ("cast", "box", "int64", "cbool", "clen", "double")


def _is_final(stmt):
    ann = getattr(stmt, "annotation", None)
    base = getattr(ann, "value", ann)
    return isinstance(base, Name) and base.id == "Final"


def class_defs(tree):
    return {c.name: c for c in walk(tree) if isinstance(c, ClassDef)}


def enclosing_class(node, parents):
    p = parents.get(node)
    while p is not None:
        if isinstance(p, ClassDef):
            return p.name
        p = parents.get(p)
    return None


def owner_name(node, types, parents=None):
    """Class a receiver belongs to, resolving `self` and `cls`."""
    if parents is not None and isinstance(node, Name) and node.id in ("self", "cls"):
        return enclosing_class(node, parents)
    return class_of(node, types)


def resolve_callee(func, types, classes, parents=None):
    """The FunctionDef a call reaches: function, method, classmethod or ctor."""
    fn = getattr(types.get(func), "node", None)
    if isinstance(fn, (FunctionDef, AsyncFunctionDef)):
        return fn
    if isinstance(fn, ClassDef):
        return user_method(fn.name, "__init__", classes)
    if isinstance(func, Name) and func.id in classes:
        return user_method(func.id, "__init__", classes)
    if isinstance(func, Attribute) and classes is not None:
        # Set64.from_city(...) or receiver.method(...)
        owner = func.value.id if isinstance(func.value, Name) and func.value.id in classes \
            else owner_name(func.value, types, parents)
        cls = classes.get(owner)
        if cls is not None:
            for stmt in cls.body:
                if isinstance(stmt, (FunctionDef, AsyncFunctionDef)) and stmt.name == func.attr:
                    return stmt
    return None


def callee_params(node, types, classes=None, parents=None):
    """(ast arg nodes, return-type vertex source) for a resolvable call."""
    fn = resolve_callee(node.func, types, classes, parents)
    if fn is None:
        return [], None
    params = fn.args.args
    bound = isinstance(node.func, Attribute) or fn.name == "__init__"
    return (params[1:] if bound else params), fn


def element_typed(node, types):
    """Does iterating or indexing this expression yield a known type?

    Only generic containers carry one (CheckedList[T], Array[T], ...). Builtin
    range/list/zip have no element type, so what comes out is dynamic.
    """
    t = types.get(node)
    return bool(getattr(getattr(t, "klass", None), "type_args", None))


def class_of(node, types):
    """Name of the class a receiver expression is typed as, if any."""
    t = types.get(node)
    tn = getattr(getattr(t, "klass", None), "type_name", None)
    return tn and tn.readable_name.rsplit(".", 1)[-1]


def class_fields(tree):
    """(class name, attr) -> the AnnAssign target declaring it.

    Fields are declared either in the class body (`x: int`) or on self inside a
    method (`self.x: int = ...`); both are declarations, neither is inferred.
    """
    out = {}
    for cls in walk(tree):
        if not isinstance(cls, ClassDef):
            continue
        for stmt in cls.body:
            if isinstance(stmt, AnnAssign) and isinstance(stmt.target, Name):
                out[(cls.name, stmt.target.id)] = stmt.target
        for n in walk(cls):
            if (isinstance(n, AnnAssign) and isinstance(n.target, Attribute)
                    and isinstance(n.target.value, Name) and n.target.value.id == "self"):
                out.setdefault((cls.name, n.target.attr), n.target)
    return out


class GraphBuilder(NodeVisitor):
    """Builds the dependency graph in one pass over the tree.

    Two structures come out of it. `g` maps a vertex to the vertices it feeds
    and answers "is this still typed"; `deps` maps a node to its labelled
    inflow, `[(role, src, op)]`, and answers "what type is it". They are built
    together because they are the same edges seen two ways -- keeping them in
    separate passes is how an edge ends up in one and not the other.
    """

    def __init__(self, tree, parents, types, reverse_outflow, declared=(),
                 resolved_from=None):
        self.g = {}
        self.deps = {}
        self.dyn_ctx = set()
        self.elem_ctx = {}
        self.parents = parents
        self.types = types
        self.reverse_outflow = reverse_outflow
        self.declared = declared
        self.resolved_from = resolved_from or {}
        self.fields = class_fields(tree)
        self.classes = class_defs(tree)

    # -- edge primitives ---------------------------------------------------

    def link(self, a, b):
        self.g.setdefault(a, []).append(b)

    def up(self, src, dst, role=VALUE, op=None):
        """src's type derives dst's type."""
        self.link((src, TYPE), (dst, TYPE))
        self.deps.setdefault(dst, []).append((role, src, op))

    def down(self, outer, inner):
        """outer's context lands on inner."""
        self.link((outer, CTX), (inner, CTX))

    def bind(self, value, target):
        """Assignment to a local: infers the target *and* constrains the value."""
        self.link((value, TYPE), (target, TYPE))
        self.link((target, TYPE), (value, CTX))
        self.deps.setdefault(target, []).append((VALUE, value, None))

    def expect_dynamic(self, *nodes):
        """Positions the language itself requires to be non-primitive.

        `visitList`, `visitTuple`, `visitSlice` and `visitStarred` all call
        `visitExpectedType(elt, DYNAMIC)`. That is a demand no annotation
        creates and none can erase, so a primitive landing there has to box --
        and modelling it as "the container's context flows down" gets it
        exactly backwards.
        """
        for n in nodes:
            if n is not None:
                self.dyn_ctx.add(n)

    def elem_demand(self, container, value):
        """What `xs.append(v)` asks of `v`: the container's *element* type.

        `visitCall` records `add_inflow(decl, args[0])` for append, so the
        declaration constrains the argument -- but the declaration is the
        container and the demand is on one element of it, so this cannot be an
        ordinary edge to the container's own type.
        """
        self.elem_ctx[value] = container

    def demand(self, target, value):
        """A declared type constrains what fills it, and never infers from it."""
        self.link((target, TYPE), (value, CTX))

    def literal_context(self, expr, node, other):
        """What a literal operand is asked to be.

        Two demands, both of them things cinder actually does. `visitBinOp`
        hands the expression's own type_ctx to each operand, so that flows down.
        And a literal beside a *primitive* is that primitive -- it is how
        `clen(xs) - 1` subtracts two int64s rather than an int64 and an int.

        Both are restricted to literals. Giving every operand its sibling's
        context invents demands that were never there: `[0] * n` becomes a
        `list` context on `n`, and the program miscompiles.
        """
        if not isinstance(node, Constant):
            return
        if expr is not None:
            self.link((expr, CTX), (node, CTX))
        t = self.types.get(other)
        if isinstance(getattr(t, "klass", None), CType):
            self.link((other, TYPE), (node, CTX))

    # -- shared queries ----------------------------------------------------

    def owner_of(self, node):
        return owner_name(node, self.types, self.parents)

    def field_decl(self, receiver, attr):
        """The declaration of `receiver.attr`, searching base classes too.

        A field declared on a base class is just as much a declaration as one
        declared locally, and stopping at the subclass leaves the store looking
        undeclared -- so it keeps the type it was recorded with and the pass
        coerces to a type the erased program no longer has.
        """
        owner, seen = self.owner_of(receiver), set()
        while owner is not None and owner not in seen:
            seen.add(owner)
            if (decl := self.fields.get((owner, attr))) is not None:
                return decl
            cls = self.classes.get(owner)
            bases = [b.id for b in getattr(cls, "bases", []) if isinstance(b, Name)] \
                if cls is not None else []
            owner = bases[0] if bases else None
        return None

    def at_module_scope(self, node):
        """True only for module level -- a class body is its own scope."""
        p = self.parents.get(node)
        while p is not None:
            if isinstance(p, (FunctionDef, AsyncFunctionDef, ClassDef)):
                return False
            p = self.parents.get(p)
        return True

    def assign(self, value, target):
        """Only the assignment that establishes a local infers from its value.

        Later assignments, and every member or element store, must conform to a
        type that is already fixed.
        """
        if isinstance(target, (Tuple, List)):
            for elt in target.elts:
                self.assign(value, elt)
        elif isinstance(target, Attribute):
            decl = self.field_decl(target.value, target.attr)
            self.demand(decl if decl is not None else target, value)
            if decl is target:
                # the declaration itself: with the annotation gone it holds
                # whatever was assigned, not whatever the receiver is
                self.up(value, target)
        elif isinstance(target, Subscript):
            # a[i] = v types v by the *element*, not the container: the
            # subscript node already carries the element type
            self.demand(target, value)
        elif self.at_module_scope(target) and not _is_final(self.parents.get(target)):
            # a global written from more than one place is not inferred the way
            # a local is: reads elsewhere see dynamic unless the annotation
            # survives, or it is Final and therefore fixed
            self.demand(target, value)
        elif target in self.declared:
            self.bind(value, target)
        else:
            self.demand(target, value)      # re-assignments must conform

    def loop_var_uses(self, scope, target):
        """The loop variable's type reaches every read of it in the body."""
        if not isinstance(target, Name):
            return
        for n in walk(scope):
            if isinstance(n, Name) and n.id == target.id and isinstance(n.ctx, Load):
                self.up(target, n)

    def iterate(self, node, iter_expr, target):
        """`for x in xs` and the generator clause of a comprehension."""
        if element_typed(iter_expr, self.types):
            self.assign(iter_expr, target)
        self.loop_var_uses(node, target)

    def comprehension(self, node, elts):
        for gen in node.generators:
            self.iterate(node, gen.iter, gen.target)
        for e in elts:
            self.up(e, node, ELTS)
            self.expect_dynamic(e)
        self.generic_visit(node)

    # -- statements --------------------------------------------------------

    def visit_Assign(self, node):
        for t in node.targets:
            self.assign(node.value, t)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if node.value is not None:
            self.assign(node.value, node.target)
        # reads reach the AnnAssign, declarations sit on its target: same
        # declaration, so keep the two vertices in step
        self.up(node.target, node)
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        if node.value is not None:
            self.assign(node.value, node.target)
        self.up(node.target, node)        # `visitAugAssign` types the node as its target
        self.generic_visit(node)

    def visit_NamedExpr(self, node):
        self.assign(node.value, node.target)
        self.up(node.target, node)        # a walrus is worth its target
        self.generic_visit(node)

    def visit_FormattedValue(self, node):
        # `visitFormattedValue` is DYNAMIC and forbids a primitive inside
        self.expect_dynamic(node.value)
        self.generic_visit(node)

    def visit_Return(self, node):
        if node.value is not None:
            self.demand(func_of(node, self.parents), node.value)
        self.generic_visit(node)

    def visit_For(self, node):
        self.iterate(node, node.iter, node.target)
        self.generic_visit(node)

    visit_AsyncFor = visit_For

    # -- expressions -------------------------------------------------------

    def visit_Call(self, node):
        params, fn = callee_params(node, self.types, self.classes, self.parents)
        for arg, param in zip(node.args, params):
            self.demand(param, arg)
        by_name = {p.arg: p for p in params}
        for kw in node.keywords:
            if kw.arg in by_name:
                self.demand(by_name[kw.arg], kw.value)
        if fn is not None:
            self.up(fn, node, RETURN)         # return type -> call expression
        if isinstance(node.func, Attribute):
            # calling a method on a dynamic object yields dynamic, whatever the
            # method's declared return type says
            self.up(node.func.value, node, RECEIVER)
        if isinstance(node.func, Attribute) and node.args:
            if node.func.attr == "append":
                self.elem_demand(node.func.value, node.args[0])
            elif node.func.attr == "extend":
                self.up(node.func.value, node.args[0], RECEIVER)
        if isinstance(node.func, Attribute) and node.func.attr == "pop":
            # a pop yields an element, not the container
            self.up(node.func.value, node, ELEM)
        if isinstance(node.func, Name) and node.func.id in COERCIONS and node.args:
            # `box(size)` is only valid while size is a primitive, and what it
            # produces depends on which primitive. That is knowable, so it is an
            # edge: the operand feeds the coercion and the result is recomputed.
            operand = node.args[1] if node.func.id == "cast" and len(node.args) > 1 \
                else node.args[0]
            self.up(operand, node, COERCE, node.func.id)
        self.generic_visit(node)

    def visit_Name(self, node):
        """A read sees what reached it, not what the variable was declared as.

        `resolved_from` is the binder's own reaching definitions, so a read
        after a merge depends on every branch and a loop-carried write reaches
        the reads above it. Linking every write of the name instead would be
        wrong -- cinder narrows per path.
        """
        if isinstance(node.ctx, Load):
            reaching = self.resolved_from.get(node)
            if reaching:
                for d in reaching:
                    self.up(d, node, BRANCH)
            elif (decl := self.reverse_outflow.get(node)) is not None:
                self.up(decl, node)
        self.generic_visit(node)

    def visit_Attribute(self, node):
        decl = self.field_decl(node.value, node.attr)
        if decl is not None and decl is not node:
            # both must hold: the field is declared, *and* the receiver still
            # has a type. A declared field read off a dynamic object is
            # dynamic. (`decl is node` at the declaration itself: a self-edge
            # would keep it alive under a greatest fixpoint.)
            self.up(decl, node, FIELD)
            self.up(node.value, node, RECEIVER)
        elif decl is None:
            # a method reference: only as typed as the object it is bound to
            self.up(node.value, node, RECEIVER)
        self.generic_visit(node)

    def visit_Subscript(self, node):
        if element_typed(node.value, self.types):
            self.up(node.value, node, ELEM)
        # the index gets no context: it would be the container's *index* type,
        # and passing the container down coerces to the wrong one
        self.generic_visit(node)

    def visit_BinOp(self, node):
        m = resolve_user(node.op, self.owner_of(node.left), self.classes)
        if m is not None:
            self.up(m, node, RETURN)          # the dunder's declared return type
            if len(m.args.args) > 1:
                self.demand(m.args.args[1], node.right)
        else:
            # the operands together decide the result -- `[0] * n` is a list,
            # `int64 + int64` is int64 -- so both are inputs and the op selects
            # the rule. NB: they do *not* supply each other's context; cinder
            # binds them independently, and an edge there produces casts like
            # `cast(float, 4)`.
            self.up(node.left, node, BINOP, node.op)
            self.up(node.right, node, BINOP, node.op)
            # `visitBinOp` visits both operands with the expression's own
            # type_ctx, so the demand on the result is the demand on each
            # operand. Without this an operand keeps the context it was
            # recorded with before erasure and gets coerced to a type its
            # sibling no longer has -- `n - int64(i)` after n stopped being
            # primitive.
            self.down(node, node.left)
            self.down(node, node.right)
            self.literal_context(None, node.left, node.right)
            self.literal_context(None, node.right, node.left)
        self.generic_visit(node)

    def visit_UnaryOp(self, node):
        self.up(node.operand, node, UNARY, node.op)
        self.generic_visit(node)

    def visit_BoolOp(self, node):
        # `visitBoolOp` widens over every value -- differing types union rather
        # than collapsing to dynamic, and the join does exactly that
        for e in node.values:
            self.up(e, node, BRANCH)
        # ...but a union cannot contain a primitive. Whether that bites depends
        # on the types the arms *settle* to, not the ones they had before
        # erasure, so the rule is applied in `_settle_ctxs` rather than here.
        self.generic_visit(node)

    def visit_Compare(self, node):
        one = node.ops[0] if len(node.ops) == 1 else None
        m = one is not None and resolve_user(one, self.owner_of(node.left), self.classes)
        if m:
            self.up(m, node, RETURN)
            if len(m.args.args) > 1:
                self.demand(m.args.args[1], node.comparators[0])
        else:
            for e in [node.left, *node.comparators]:
                self.up(e, node, CMPOP, one)
            if len(node.comparators) == 1:
                # visitCompare passes no type_ctx to its operands, so only the
                # primitive-sibling coercion applies here
                self.literal_context(None, node.left, node.comparators[0])
                self.literal_context(None, node.comparators[0], node.left)
        self.generic_visit(node)

    def visit_IfExp(self, node):
        self.up(node.body, node, BRANCH)
        self.up(node.orelse, node, BRANCH)
        self.down(node, node.body)        # visitIfExp passes type_ctx to both
        self.down(node, node.orelse)
        self.generic_visit(node)

    def visit_Starred(self, node):
        # `visitStarred` sets the node itself DYNAMIC and expects a
        # non-primitive inside, so nothing flows up from the value
        self.expect_dynamic(node.value)
        self.generic_visit(node)

    def visit_Slice(self, node):
        self.expect_dynamic(node.lower, node.upper, node.step)
        self.generic_visit(node)

    # -- container literals: the container's context reaches its elements ---

    def visit_List(self, node):
        # the item type is widened over the elements (`visitList`), so elements
        # feed the container -- and each is expected DYNAMIC, not the
        # container's own context
        for e in node.elts:
            self.up(e, node, ELTS)
            self.expect_dynamic(e)
        self.generic_visit(node)

    visit_Tuple = visit_List
    visit_Set = visit_List

    def visit_Dict(self, node):
        for k, v in zip(node.keys, node.values):
            if k is not None:
                self.up(k, node, ELTS)
                self.expect_dynamic(k)
            self.up(v, node, ELTS)
            self.expect_dynamic(v)
        self.generic_visit(node)

    def visit_ListComp(self, node):
        self.comprehension(node, [node.elt])

    visit_SetComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_DictComp(self, node):
        self.comprehension(node, [node.key, node.value])


def build_graph(tree, parents, types, reverse_outflow, declared=(), resolved_from=None):
    """Returns (g, deps).

    `g` is the typedness graph: vertex -> the vertices it feeds. `deps` is the
    same edges labelled with *what kind of derivation* each one is, which is
    what turns typedness into a type. A role is deliberately not a per-edge
    function: `a + b` cannot be resolved from either operand alone, so the rule
    belongs to the destination node and the edge only says which slot an input
    fills.
    """
    builder = GraphBuilder(tree, parents, types, reverse_outflow, declared,
                           resolved_from)
    builder.visit(tree)
    return builder.g, builder.deps, builder.dyn_ctx, builder.elem_ctx

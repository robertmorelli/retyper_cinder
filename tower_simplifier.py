from sys import path
from ast import Call, Compare, Name, NodeTransformer, unparse, walk
from patch_picker import pick_patch

path.insert(0, "_cinderx/cinderx/PythonLib")
from cinderx.compiler.static.types import CType

def extract_coerced(node: Call, constructors):
    if isinstance(node, Call):
        first, second, *_ = node.args + [None, None]
        if node.func in constructors:
            if isinstance(constructors[node.func], CType):
                return extract_coerced(first, constructors) or first
        elif isinstance(node.func, Name):
            if node.func.id == "box":
                return extract_coerced(first, constructors) or first
            elif node.func.id == "cast":
                return extract_coerced(second, constructors) or second
    return None

class TowerSimplifier(NodeTransformer):
    def __init__(self, constructors, valid_pair, types, type_ctxs, needs_exact,
                 dyn, graph):
        self.graph = graph
        self.constructors = constructors
        self.valid_pair = valid_pair
        self.types = types
        self.type_ctxs = type_ctxs
        self.needs_exact = needs_exact
        self.dyn = dyn

    def _narrowing_cast(self, node):
        if not (isinstance(node.func, Name) and node.func.id == "cast"
                and len(node.args) == 2):
            return False
        t = self.types.get(node.args[1])
        if t is None:
            return False
        target = unparse(node.args[0])
        return t.klass.type_name.readable_name in (
            f"Optional[{target}]", f"{target} | None")

    def load_bearing(self, node, inner):
        """Would collapsing this coercion cost anything downstream its type?

        The pass only ever asked whether the operand fits the slot the
        coercion sits in. `cast(WorkerTaskRec, r)` in an Any slot passes that
        test -- both sides are dynamic -- but the cast is the only thing
        giving the declaration a type, and every later read of it breaks.
        """
        produced, replacement = self.types.get(node), self.types.get(inner)
        if produced is None or produced is replacement or produced is self.dyn:
            return False
        return any(self.types.get(where) is not self.dyn
                   for where, slot in
                   self.graph.outgoing().get(self.graph.cell(node, "type"), ())
                   if slot == "type")

    def visit_Call(self, node):
        if node in self.needs_exact:
            return node
        if self._narrowing_cast(node):
            self.generic_visit(node)
            return node
        if inner := extract_coerced(node, self.constructors):
            if self.load_bearing(node, inner):
                self.generic_visit(node)
                return node
            tc = self.type_ctxs.get(node)
            t = self.types.get(inner)
            node = pick_patch(inner, t, tc, self.valid_pair, self.needs_exact,
                              self.types, self.type_ctxs, self.dyn,
                              self.graph.must_agree(node)).wrap()
            if self.types.get(node) is not None:
                # the position now yields whatever the collapse left behind
                self.graph.propagate(node, self.types.get(node), self.types,
                                     self.type_ctxs)
        self.generic_visit(node)
        return node

def simplify_coercions(tree, constructors, valid_pair, types, type_ctxs,
                       needs_exact, dyn, graph):
    simplifier = TowerSimplifier(constructors, valid_pair, types, type_ctxs,
                                 needs_exact, dyn, graph)
    simplifier.visit(tree)
    return tree
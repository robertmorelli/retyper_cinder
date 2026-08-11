from sys import path
from ast import NodeTransformer
from ast import FunctionDef, Return, unparse, walk

path.insert(0, "_cinderx/cinderx/PythonLib")
from cinderx.compiler.static.types import CType

class InlineCallArgFinder(NodeTransformer):
    def __init__(self, reverse_outflow):
        self.reverse_outflow = reverse_outflow
        self.needs_exact = set()

    def visit_FunctionDef(self, node):
        if "inline" in set(map(unparse, node.decorator_list)):
            self.needs_exact |= {n.value for n in walk(node)
                                 if isinstance(n, Return) and n.value is not None}
        self.generic_visit(node)
        return node

    def visit_Call(self, node):
        if source := self.reverse_outflow.get(node):
            if isinstance(source, FunctionDef):
                if "inline" in set(map(unparse, source.decorator_list)):
                    self.needs_exact |= set(node.args)
        self.generic_visit(node)
        return node

class IteratorFinder(NodeTransformer):
    """A list comprehension's element has to be spelled exactly.

    This looks removable and is not. In isolation the cast it forces changes
    no outcome -- CheckedList checks every element as it is built -- and on
    wrong data it turns a clean `bad value 'int' for chklist[C]` into a
    segfault. Taking it out passes those probes and then fails six held_karp
    masks with `Literal[2] received for positional arg`, because the exact
    spelling is also what keeps `_choose` off the `valid_pair` branch. The
    comprehension casts it produces are the price of that.
    """

    def __init__(self):
        self.needs_exact = set()

    def visit_ListComp(self, node):
        self.needs_exact |= set((node.elt,))
        self.generic_visit(node)
        return node


def _is_int(t):
    """An int, exact or not.

    There are two `int` instances in play -- cinderx hands back a NumInstance
    where the slot holds a NumExactInstance -- so this asks the name rather
    than the identity.
    """
    return t is not None and t.klass.type_name.readable_name == "int"


def find_needs_exact(tree, reverse_outflow, types=None):
    """Positions whose value has to be spelled exactly, `box(T(x))` not `box(x)`.

    Never for an int: CheckedList[int] and CheckedDict[int, int] both take a
    bool -- an int subclass -- without complaint, and every slot we tried
    accepts an inexact int.

    That is the only safe trim. Exactness looks equally meaningless where the
    demand is an object, but `_choose` also reads this set to decide whether to
    try `valid_pair` at all, so waving it through there drops coercions the
    position still needs. Moving the question into `_choose` and skipping the
    list comprehensions were both tried, and both cost real failures.
    """
    inline_finder = InlineCallArgFinder(reverse_outflow)
    # iter_finder = IteratorFinder()
    inline_finder.visit(tree)
    # iter_finder.visit(tree)
    found = inline_finder.needs_exact # | iter_finder.needs_exact
    if types is None:
        return found
    return {node for node in found if not _is_int(types.get(node))}
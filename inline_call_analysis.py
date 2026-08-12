from sys import path
from ast import NodeTransformer
from ast import FunctionDef, unparse

path.insert(0, "_cinderx/cinderx/PythonLib")
from cinderx.compiler.static.types import CType

class InlineCallArgFinder(NodeTransformer):
    """The arguments of a call to an `@inline` function.

    Only the arguments. The returned expression used to be here too, and it is
    the one position where the demand costs something it does not buy: the
    return is read once, by whatever the call feeds, and that consumer decides
    its own coercion. Marking it exact only pushed `_choose` off `valid_pair`
    and onto a cast to `object`, which is a cast to dynamic -- no narrowing, no
    check, nothing but a wrapper on the value the body already produced.

    The arguments are different. cinderx substitutes the body at each call
    site, so two arguments that disagree -- one still typed, one erased --
    become an `int64 < dynamic` inside it. The cast to `object` is what makes
    them agree, and dropping it costs six deltablue masks.
    """

    def __init__(self, reverse_outflow):
        self.reverse_outflow = reverse_outflow
        self.inline_args = set()

    def visit_Call(self, node):
        if source := self.reverse_outflow.get(node):
            if isinstance(source, FunctionDef):
                if "inline" in set(map(unparse, source.decorator_list)):
                    self.inline_args |= set(node.args)
        self.generic_visit(node)
        return node

class IteratorFinder(NodeTransformer):
    """A list comprehension's element, spelled exactly. Nothing uses this now.

    It used to be needed, and the reason it stopped is worth keeping. Marking
    the element exact kept `_choose` off the `valid_pair` branch, which is
    what dropping it cost: six held_karp masks with `Literal[2] received for
    positional arg`. In exchange it produced a cast to `object` on every
    comprehension element -- a cast to dynamic, which checks nothing.

    Once `CastWrapper` stopped emitting a cast to `int` the held_karp masks
    held on their own, so the exactness was buying nothing and the casts were
    pure cost: pystone alone lost 25 of them. Measured over the 830 masks with
    this finder unused, both modes, no regressions.
    """

    def __init__(self):
        self.inline_args = set()

    def visit_ListComp(self, node):
        self.inline_args |= set((node.elt,))
        self.generic_visit(node)
        return node


def _is_int(t):
    """An int, exact or not.

    There are two `int` instances in play -- cinderx hands back a NumInstance
    where the slot holds a NumExactInstance -- so this asks the name rather
    than the identity.
    """
    return t is not None and t.klass.type_name.readable_name == "int"


def find_inline_args(tree, reverse_outflow, types=None):
    """The arguments of every call to an `@inline` function.

    cinderx substitutes the body at the call site with the types the arguments
    actually have, so two arguments that no longer agree -- one still typed,
    one erased -- meet inside it as `int64 < dynamic`. The mediator brings the
    typed one down with a cast to `object`; this is the set that lets it,
    by keeping `_choose` off the `valid_pair` branch that would pass the
    argument through untouched. Six deltablue masks turn on it.

    Never for an int. CheckedList[int] and CheckedDict[int, int] both take a
    bool -- an int subclass -- without complaint, and every slot we tried
    accepts an inexact int, so an int argument is left alone.

    This set used to hold two other things, both dropped and measured. A
    returned expression is read once, by whatever the call feeds, and that
    consumer picks its own coercion. List comprehension elements went the same
    way; see IteratorFinder.
    """
    inline_finder = InlineCallArgFinder(reverse_outflow)
    inline_finder.visit(tree)
    found = inline_finder.inline_args
    if types is None:
        return found
    return {node for node in found if not _is_int(types.get(node))}
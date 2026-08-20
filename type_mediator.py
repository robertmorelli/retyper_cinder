"""Walk the tree bottom up and let `mediate` decide each position.

Nothing is decided here. Every edit -- the `clen`/`len` spelling, the wrappers
a test, a unary operator or a subscript index does not want, the towers that
collapse, the author's coercions erasure made pointless, and the wrapper a
pair still needs -- is one function, `patch_picker.mediate`, so that the whole
policy can be read in one place. This is the traversal it runs under and the
one piece of context that has to be gathered before the walk begins.

Bottom up throughout. The result links point from an operand up to the
expression containing it, so an operand has to be decided and propagated
before the expression above it can be judged.

Erasure is not part of this. `remove_annotations` must finish across the whole
tree first, because whether a value needs coercing depends on what every other
annotation became.
"""
from ast import (Call, DictComp, GeneratorExp, If, IfExp, ListComp,
                 NodeTransformer, SetComp, Slice, Subscript, While, walk)

from inline_call_analysis import inline_args, is_inline_call
from patch_picker import mediate


def _payloads(tree):
    """Every expression a comprehension builds its container out of.

    Gathered before the walk because it is a fact about the shape of the tree
    rather than about any position's types, and because the walk rewrites the
    elements it is asking about.
    """
    found = set()
    for node in walk(tree):
        if isinstance(node, (ListComp, SetComp, GeneratorExp)):
            found.add(node.elt)
        elif isinstance(node, DictComp):
            found.update((node.key, node.value))
    return found


class Coercer(NodeTransformer):
    """Bottom up, telling `mediate` what each node is being used as.

    A node cannot tell whether it is a value, somebody's condition, somebody's
    index or the argument of an inline call -- only the position it hangs in
    knows that, and by the time the walk reaches the parent the child has
    already been decided. So the parent marks all three on the way down, and
    the flags reach `mediate` as the constants they are.
    """

    def __init__(self, types, type_ctxs, dyn, valid_pair, reverse_outflow,
                 graph, payloads):
        self.tables = (types, type_ctxs, dyn, valid_pair, graph, payloads)
        self.types, self.reverse_outflow = types, reverse_outflow
        self.tests, self.indices, self.inlined = set(), set(), set()

    def visit(self, node):
        if isinstance(node, (If, IfExp, While)):
            self.tests.add(node.test)
        elif isinstance(node, Subscript) and not isinstance(node.slice, Slice):
            self.indices.add(node.slice)
        elif isinstance(node, Call) and is_inline_call(node,
                                                       self.reverse_outflow):
            self.inlined.update(inline_args(node, self.types))
        self.generic_visit(node)
        return mediate(node, *self.tables, is_test=node in self.tests,
                       is_index=node in self.indices,
                       is_inline_arg=node in self.inlined)


def coerce_tree(tree, types, type_ctxs, dyn, valid_pair, reverse_outflow,
                graph):
    Coercer(types, type_ctxs, dyn, valid_pair, reverse_outflow, graph,
            _payloads(tree)).visit(tree)
    return tree

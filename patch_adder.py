from ast import Compare, NodeTransformer, Load, Slice, walk
from patch_picker import pick_patch

# coercions that exist only to produce a primitive. In a slot that is no longer
# primitive they do nothing but make the program invalid.
TO_PRIMITIVE = ("int64", "cbool", "clen", "double")
from get_ast_data import get_ctx

class PatchAdder(NodeTransformer):
    def __init__(self, types, type_ctxs, dyn, valid_pair, needs_exact, graph=None):
        self.types = types
        self.type_ctxs = type_ctxs
        self.dyn = dyn
        self.valid_pair = valid_pair
        self.needs_exact = needs_exact
        self.graph = graph

    def redundant_coercion(self, node):
        """A coercion already in the source that no longer coerces anything.

        `a: Array[int64] = Array[int64](box(size))` is the author's own box, not
        one we emitted. Erase `size: int64` and it receives a plain int, which
        cinder rejects -- but only because the box is now a no-op. The graph
        settles such a call to its operand's own type, so equality of the two is
        the test, and the fix is to drop the wrapper rather than to give up.
        """
        coercion = self.graph.coercion(node) if self.graph is not None else None
        if coercion is None:
            return None
        name, operand = coercion
        t = self.types.get(node)
        if name == "box" and t is not None and t is self.types.get(operand):
            return self.collapse(node, operand)   # coerces to what it already was
        if name in TO_PRIMITIVE and self.type_ctxs.get(node) is self.dyn:
            # the author wrote `cbool(x)` because something demanded a
            # primitive. Erase that demand and the coercion is not merely
            # redundant, it is invalid -- a primitive cannot sit in a dynamic
            # slot, and there is nothing left that wants one.
            return self.collapse(node, operand)
        return None

    def collapse(self, node, operand):
        """Drop a coercion, recording it the way an inserted one is recorded.

        `_record` writes the tables when a wrapper goes in. Taking one out has
        to write them too, or the position keeps describing a coercion that is
        no longer there.
        """
        self.type_ctxs[operand] = self.type_ctxs.get(node, self.type_ctxs.get(operand))
        if self.graph is not None:
            produced = self.types.get(operand)
            if produced is not None:
                self.graph.propagate(operand, produced, self.types, self.type_ctxs)
        return operand

    def index_comparisons(self, tree):
        """Operands of a comparison, which must agree with each other.

        A machine literal can be boxed in arithmetic because the result gets
        coerced to whatever the target wants. A comparison's result is not
        coerced, so boxing one operand just breaks the pair.
        """
        self.compared = set()
        for node in walk(tree):
            if isinstance(node, Compare):
                self.compared.add(node.left)
                self.compared.update(node.comparators)

    def visit(self, node):
        # Children first: the result links point from an operand up to the
        # expression containing it, so a coercion below has to be decided and
        # propagated before this position can be judged.
        self.generic_visit(node)
        # A coercion that no longer coerces is dropped -- but the position it
        # sat in still has to be decided, so this replaces the node and falls
        # through to pick_patch rather than returning early.
        target = self.redundant_coercion(node) or node
        result = target
        # only values get coerced; an assignment target or a slice is not a
        # position a coercion can wrap
        ctx = get_ctx(node)
        if not (isinstance(node, Slice)
                or (ctx is not None and not isinstance(ctx, Load))):
            # when a coercion was dropped the position holds what the operand
            # holds, not what the coercion produced -- carrying the coercion's
            # type forward is how a dynamic value ends up being boxed
            t = self.types.get(target) if target is not node else self.types.get(node)
            if t and (tc := self.type_ctxs.get(node)):
                wrapper = pick_patch(target, t, tc, self.valid_pair,
                                     self.needs_exact, self.types,
                                     self.type_ctxs, self.dyn,
                                     node in self.compared)
                result = wrapper.wrap()
                if result is not target and wrapper.T is not None \
                        and self.graph is not None:
                    # The coercion changed what this position yields. The other
                    # half of any pair that has to agree is one edge away, so
                    # let the graph carry the new type there before that side
                    # is decided against a stale one.
                    self.graph.propagate(node, wrapper.T, self.types,
                                         self.type_ctxs)
        return result

def add_patches(tree, types, type_ctxs, dyn, valid_pair, needs_exact, graph=None):
    adder = PatchAdder(types, type_ctxs, dyn, valid_pair, needs_exact, graph)
    adder.index_comparisons(tree)
    adder.visit(tree)
    return tree

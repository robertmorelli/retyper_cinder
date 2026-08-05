from ast import NodeTransformer, Call, Name, Load, Slice
from ast import Assign, AnnAssign, AugAssign, For
from patch_picker import pick_patch
from graph_construction import COERCIONS

# coercions that exist only to produce a primitive. In a slot that is no longer
# primitive they do nothing but make the program invalid.
TO_PRIMITIVE = ("int64", "cbool", "clen", "double")
from get_ast_data import get_ctx

class PatchAdder(NodeTransformer):
    def __init__(self, types, type_ctxs, dyn, valid_pair, needs_exact):
        self.types = types
        self.type_ctxs = type_ctxs
        self.dyn = dyn
        self.valid_pair = valid_pair
        self.needs_exact = needs_exact

    def redundant_coercion(self, node):
        """A coercion already in the source that no longer coerces anything.

        `a: Array[int64] = Array[int64](box(size))` is the author's own box, not
        one we emitted. Erase `size: int64` and it receives a plain int, which
        cinder rejects -- but only because the box is now a no-op. The graph
        settles such a call to its operand's own type, so equality of the two is
        the test, and the fix is to drop the wrapper rather than to give up.
        """
        if not (isinstance(node, Call) and isinstance(node.func, Name)
                and node.func.id in COERCIONS and node.args):
            return None
        operand = node.args[1] if node.func.id == "cast" and len(node.args) > 1 \
            else node.args[0]
        t = self.types.get(node)
        if t is not None and t is self.types.get(operand):
            return operand                # coerces to what it already was
        if node.func.id in TO_PRIMITIVE and self.type_ctxs.get(node) is self.dyn:
            # the author wrote `cbool(x)` because something demanded a
            # primitive. Erase that demand and the coercion is not merely
            # redundant, it is invalid -- a primitive cannot sit in a dynamic
            # slot, and there is nothing left that wants one.
            return operand
        return None

    def visit(self, node):
        self.generic_visit(node)
        # A coercion that no longer coerces is dropped -- but the position it
        # sat in still has to be decided, so this replaces the node and falls
        # through to pick_patch rather than returning early.
        target = self.redundant_coercion(node) or node
        result = target
        # only values get coerced; an assignment target or a slice is not a
        # position a coercion can wrap
        ctx = get_ctx(node)
        if isinstance(node, Slice) or (ctx is not None and not isinstance(ctx, Load)):
            return result
        # when a coercion was dropped the position holds what the operand
        # holds, not what the coercion produced -- carrying the coercion's type
        # forward is how a dynamic value ends up being boxed
        t = self.types.get(target) if target is not node else self.types.get(node)
        if t and (tc := self.type_ctxs.get(node)):
            result = pick_patch(target, t, tc, self.valid_pair, self.needs_exact,
                                self.types, self.type_ctxs, self.dyn).wrap()
        return result

def add_patches(tree, types, type_ctxs, dyn, valid_pair, needs_exact):
    PatchAdder(types, type_ctxs, dyn, valid_pair, needs_exact).visit(tree)
    return tree

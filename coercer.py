"""Decide the coercion at every position, in one pass.

This was three: len_fixer swapped `clen` for `len` when its operand went
dynamic, patch_adder inserted wrappers and dropped the author's own when
erasure made them pointless, and tower_simplifier collapsed the towers that
left behind. All three rewrite a call from its operand's type and record what
the position now yields, so they are one decision asked three times.

Bottom up throughout. The result links point from an operand up to the
expression containing it, so an operand has to be decided and propagated
before the expression above it can be judged.

Erasure is not part of this. `remove_annotations` must finish across the whole
tree first, because whether a value needs coercing depends on what every other
annotation became.
"""
from ast import Call, Load, Name, NodeTransformer, Slice, unparse

from get_ast_data import get_ctx
from patch_picker import pick_patch

# coercions that exist only to produce a primitive. In a slot that is no longer
# primitive they do nothing but make the program invalid.
TO_PRIMITIVE = ("int64", "cbool", "clen", "double")


def extract_coerced(node, constructors):
    """The value inside a coercion, however many layers deep."""
    if not isinstance(node, Call):
        return None
    first, second, *_ = node.args + [None, None]
    if node.func in constructors:
        from cinderx.compiler.static.types import CType
        if isinstance(constructors[node.func], CType):
            return extract_coerced(first, constructors) or first
    elif isinstance(node.func, Name):
        if node.func.id == "box":
            return extract_coerced(first, constructors) or first
        if node.func.id == "cast":
            return extract_coerced(second, constructors) or second
    return None


class Coercer(NodeTransformer):
    def __init__(self, types, type_ctxs, dyn, valid_pair, needs_exact, graph,
                 constructors):
        self.types = types
        self.type_ctxs = type_ctxs
        self.dyn = dyn
        self.valid_pair = valid_pair
        self.needs_exact = needs_exact
        self.graph = graph
        self.constructors = constructors

    # ---------------------------------------------------------------- record

    def record(self, position, value):
        """A position now yields what `value` yields. Write it and propagate.

        `_record` in patch_picker does this when a wrapper goes in. Taking one
        out, or swapping one call for another, has to write the tables too, or
        the position keeps describing a coercion that is no longer there.
        """
        self.type_ctxs[value] = self.type_ctxs.get(
            position, self.type_ctxs.get(value))
        produced = self.types.get(value)
        if produced is not None:
            self.graph.propagate(value, produced, self.types, self.type_ctxs)
        return value

    # --------------------------------------------------------------- rewrites

    def demote_clen(self, node):
        """`clen(x)` yields an int64, `len(x)` a dynamic. Once the operand is
        dynamic the primitive form has nothing to measure."""
        if (isinstance(node, Call) and isinstance(node.func, Name)
                and node.func.id == "clen" and node.args
                and self.types.get(node.args[0]) == self.dyn):
            node.func.id = "len"
            self.types[node] = self.dyn

    def pointless(self, node):
        """A coercion in the source that no longer coerces anything.

        `a: Array[int64] = Array[int64](box(size))` is the author's own box.
        Erase `size: int64` and it receives a plain int, which cinderx rejects
        -- but only because the box is now a no-op, so the fix is to drop the
        wrapper rather than to give up.
        """
        coercion = self.graph.coercion(node)
        if coercion is None:
            return None
        name, operand = coercion
        produced = self.types.get(node)
        if name == "box" and produced is not None \
                and produced is self.types.get(operand):
            return self.record(node, operand)     # coerces to what it already was
        if name in TO_PRIMITIVE and self.type_ctxs.get(node) is self.dyn:
            # the author wrote `cbool(x)` because something demanded a
            # primitive. Erase that demand and the coercion is not merely
            # redundant, it is invalid -- a primitive cannot sit in a dynamic
            # slot, and nothing is left that wants one.
            return self.record(node, operand)
        return None

    def load_bearing(self, node, inner):
        """Would collapsing this cost anything downstream its type?

        The old pass only asked whether the operand fits the slot the coercion
        sits in. `cast(WorkerTaskRec, r)` in an Any slot passes that test --
        both sides are dynamic -- but the cast is the only thing giving the
        declaration a type, and every later read of it breaks.
        """
        produced, replacement = self.types.get(node), self.types.get(inner)
        if produced is None or produced is replacement or produced is self.dyn:
            return False
        return any(self.types.get(where) is not self.dyn
                   for where, slot in
                   self.graph.outgoing().get(self.graph.cell(node, "type"), ())
                   if slot == "type")

    def narrowing_cast(self, node):
        if not (isinstance(node.func, Name) and node.func.id == "cast"
                and len(node.args) == 2):
            return False
        t = self.types.get(node.args[1])
        if t is None:
            return False
        target = unparse(node.args[0])
        return t.klass.type_name.readable_name in (
            f"Optional[{target}]", f"{target} | None")

    def collapse_tower(self, node):
        """A tower this pass or the author built, whose layers cancel."""
        if node in self.needs_exact or self.narrowing_cast(node):
            return None
        inner = extract_coerced(node, self.constructors)
        if inner is None or self.load_bearing(node, inner):
            return None
        collapsed = pick_patch(
            inner, self.types.get(inner), self.type_ctxs.get(node),
            self.valid_pair, self.needs_exact, self.types, self.type_ctxs,
            self.dyn, self.graph.must_agree(node)).wrap()
        if self.types.get(collapsed) is not None:
            self.graph.propagate(collapsed, self.types.get(collapsed),
                                 self.types, self.type_ctxs)
        return collapsed

    # ------------------------------------------------------------------ visit

    def visit(self, node):
        self.generic_visit(node)
        self.demote_clen(node)
        if isinstance(node, Call):
            if (collapsed := self.collapse_tower(node)) is not None:
                node = collapsed
        # A coercion that no longer coerces is dropped, but the position it sat
        # in still has to be decided, so this replaces the node and falls
        # through to pick_patch rather than returning early.
        target = self.pointless(node) or node
        # only values get coerced; an assignment target or a slice is not a
        # position a coercion can wrap
        ctx = get_ctx(node)
        if isinstance(node, Slice) or (ctx is not None
                                       and not isinstance(ctx, Load)):
            return target
        t = self.types.get(target)
        tc = self.type_ctxs.get(node)
        if not (t and tc):
            return target
        wrapper = pick_patch(target, t, tc, self.valid_pair, self.needs_exact,
                             self.types, self.type_ctxs, self.dyn,
                             self.graph.must_agree(node))
        result = wrapper.wrap()
        if result is not target and wrapper.T is not None:
            # the position yields something new, so the other half of any pair
            # that has to agree hears about it before it is decided
            self.graph.propagate(node, wrapper.T, self.types, self.type_ctxs)
        return result


def coerce_tree(tree, types, type_ctxs, dyn, valid_pair, needs_exact, graph,
                constructors):
    Coercer(types, type_ctxs, dyn, valid_pair, needs_exact, graph,
            constructors).visit(tree)
    return tree

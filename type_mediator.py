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
from ast import (Call, If, IfExp, Load, Name, NodeTransformer, Not, Slice,
                 Subscript, UnaryOp, While, copy_location, unparse)

from cinderx_binding import get_ctx, is_primative
from patch_picker import (PRIMITIVE_NAMES, choose_patch, pick_patch,
                          record_patch)

# coercions that exist only to produce a primitive. In a slot that is no longer
# primitive they do nothing but make the program invalid.
TO_PRIMITIVE = ("int64", "cbool", "clen", "double")


def unwrapped_primitive(node):
    """What a primitive constructor was converting, or None.

    `clen` is not one of these. It is in TO_PRIMITIVE because it exists to
    produce a primitive, but it computes a length rather than converting its
    operand, and stepping over it would change what the program says.
    """
    if (isinstance(node, Call) and isinstance(node.func, Name)
            and node.func.id in PRIMITIVE_NAMES and len(node.args) == 1):
        return node.args[0]
    return None


def fast_len(measured):
    """Can `clen` measure this at all?

    Not every non-dynamic type: `clen` compiles to FAST_LEN, which reads a
    length straight out of the object's layout, so it takes a tuple, set,
    list, str, dict, Array, CheckedDict or CheckedList and nothing else. A
    user class with its own `__len__` -- deltablue/shallow's
    `OrderedCollection(list)` -- has a length and no layout to read it from,
    and promoting there is not a slow path, it is `bad argument type
    'OrderedCollection' for clen()` at compile time.

    Asked of cinderx rather than answered from a list of names: this is the
    same predicate `CLenFunction.bind_call` uses to accept or reject the call,
    so the two cannot drift apart.
    """
    try:
        return measured.get_fast_len_type() is not None
    except Exception:
        return False


def beneath_unary(node):
    """What a chain of unary operators finally reads."""
    while isinstance(node, UnaryOp):
        node = node.operand
    return node


def extract_coerced(node, constructors):
    """The value inside a coercion, however many layers deep.

    A unary operator on the way down is carried out along with the value rather
    than stepped over. The layers beneath it still cancel, but the operator is
    part of what the position yields, and handing back the bare operand would
    invert every branch reading a `not` and flip the sign under every `-`.
    """
    if isinstance(node, UnaryOp):
        inner = extract_coerced(node.operand, constructors)
        return None if inner is None else copy_location(
            UnaryOp(op=node.op, operand=inner), node)
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
    def __init__(self, types, type_ctxs, dyn, valid_pair, inline_args, graph,
                 constructors):
        self.types = types
        self.type_ctxs = type_ctxs
        self.dyn = dyn
        self.valid_pair = valid_pair
        self.inline_args = inline_args
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

    def bare_test(self, node):
        """A branch demands nothing of its test, so a coercion there is noise.

        `cbool(x)` compiles only where x is already a bool, a cbool or dynamic,
        and cinderx branches on all three unwrapped, so the wrapper changes no
        outcome. It does cost one: on a dynamic it demands exactly `bool` at
        runtime, and a truthy non-bool the bare test would have accepted raises
        instead. Nothing reads a test, so there is no type to record.

        `if`, `while` and the test of a conditional expression, which discard
        their test alike. Not `and` or `or`: those return an operand, so a
        coercion in one is a value and may be carrying its type onward.

        Any primitive constructor, not just the `cbool` erasure usually leaves.
        A test reads for truth whatever it was handed, so `int64(k)` is as
        pointless here as `cbool(k)` -- and worse, it raises on the non-int a
        bare test would have taken.
        """
        inner = unwrapped_primitive(node.test)
        if inner is not None:
            node.test = inner
        self.promote_clen(node)

    def promote_clen(self, node):
        """`len(x)` measuring a test, where the operand still has a type.

        The mirror of `demote_clen`. `clen` is the primitive length and `len`
        the trip through the object protocol, and the difference is not small:
        on a CheckedList in a loop condition, `clen(x)` measured 15x faster
        than `len(x)` and 2.4x faster than testing the container itself. The
        one thing `clen` cannot measure is a dynamic, which is the case
        `demote_clen` exists for, so everything else belongs in the fast form.
        """
        test = node.test
        if not (isinstance(test, Call) and isinstance(test.func, Name)
                and test.func.id == "len" and len(test.args) == 1):
            return
        measured = self.types.get(test.args[0])
        if measured is None or measured is self.dyn or not fast_len(measured):
            return
        test.func.id = "clen"
        self.types[test] = self.dyn.klass.type_env.int64.instance

    def bare_unary(self, node):
        """A unary operator passes its operand's kind through, so a coercion
        under one belongs to the position above it instead.

        `not` reads its operand for truth alone; `-` and `~` hand back what
        they were given. Either way the layer underneath is doing work the
        enclosing position can decide for itself, and usually does not want.

        Taking it out changes what the operator yields, so the tables hear
        about it before the position above is judged: a negation is a truth
        value, cbool over a primitive and bool over anything else, while the
        arithmetic operators keep their operand's type outright. Miss that and
        the box picked for the primitive that used to be here lands on a bool,
        which cinderx will not box at all.
        """
        inner = unwrapped_primitive(node.operand)
        if inner is None:
            return
        node.operand = inner
        produced = self.types.get(inner)
        if isinstance(node.op, Not):
            env = self.dyn.klass.type_env
            self.types[node] = (env.cbool.instance
                                if produced is not None and is_primative(produced)
                                else env.bool.instance)
        elif produced is not None:
            self.types[node] = produced

    def bare_index(self, node):
        """A subscript demands no primitive of its index.

        `int64(i)` narrows the position rather than widening it: a list, a
        CheckedList and an Array all take a dynamic index, while a str, a
        CheckedDict and a dynamic container reject the primitive outright. The
        wrapper is here because the index was an int64 when it was written and
        the tables still record that demand. It also costs: int64 wraps an
        index too large for 64 bits into some other valid one, where the bare
        subscript would have raised.

        An index written as a float wants a guard this does not have: `int64`
        truncates it, and dropping the call hands the subscript a float it has
        no use for. Nothing in the benchmarks indexes with one, so the guard is
        left out until something needs it.
        """
        index = node.slice
        if (isinstance(index, Call) and isinstance(index.func, Name)
                and index.func.id == "int64" and len(index.args) == 1):
            node.slice = index.args[0]

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
        """A tower this pass or the author built, whose layers cancel.

        The type asked about is the innermost value's, which for a rebuilt
        `not` is a small lie. `not` preserves the primitive-or-object kind of
        its operand and nothing finer: `not <int64>` is a cbool, not an int64.
        The kind is all `_choose` reads to decide between a box, a constructor
        and nothing, so the wrapper it picks is right, and asking about the
        value keeps a freshly built negation -- which has no entry of its own
        -- out of a `types.get` that would return None and drop us into the
        cast fallback.

        Three ways the lie can bite, none of them seen in these benchmarks.
        The position is filed under the operand's type, so the tables call a
        bool an int. `valid_pair` is asked about the operand where the slot
        sees the negation. A `must_agree` pair weighs one against the other.
        All three come out the same against a dynamic slot, and a dynamic slot
        is what erasure leaves, so this holds everywhere but the rare case of
        a tower collapsing into a narrowly typed position.
        """
        if node in self.inline_args or self.narrowing_cast(node):
            return None
        inner = extract_coerced(node, self.constructors)
        if inner is None:
            return None
        value = beneath_unary(inner)
        candidate = choose_patch(
            inner, self.types.get(value), self.type_ctxs.get(node),
            self.valid_pair, self.inline_args, self.dyn,
            self.graph.must_agree(node))
        # What replaces the tower is this rebuild, not the bare value inside
        # it. Where the rebuild hands back the same type the tower did --
        # `int64(int64(x))` rebuilt as `int64(x)` -- nothing downstream can
        # tell the difference, so the question load_bearing asks does not
        # arise. Only when the rebuild produces something else, a bare value
        # most of all, can a consumer lose its type.
        if (candidate.T is not self.types.get(node)
                and self.load_bearing(node, value)):
            return None
        record_patch(candidate, inner, self.types.get(value),
                     self.type_ctxs.get(node), self.types, self.type_ctxs,
                     self.dyn, self.constructors)
        collapsed = candidate.wrap()
        if self.types.get(collapsed) is not None:
            self.graph.propagate(collapsed, self.types.get(collapsed),
                                 self.types, self.type_ctxs)
        return collapsed

    # ------------------------------------------------------------------ visit

    def visit(self, node):
        self.generic_visit(node)
        self.demote_clen(node)
        if isinstance(node, (If, IfExp, While)):
            self.bare_test(node)
        if isinstance(node, UnaryOp):
            self.bare_unary(node)
        if isinstance(node, Subscript):
            self.bare_index(node)
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
        wrapper = pick_patch(target, t, tc, self.valid_pair, self.inline_args,
                             self.types, self.type_ctxs, self.dyn,
                             self.constructors, self.graph.must_agree(node))
        result = wrapper.wrap()
        if result is not target and wrapper.T is not None:
            # the position yields something new, so the other half of any pair
            # that has to agree hears about it before it is decided
            self.graph.propagate(node, wrapper.T, self.types, self.type_ctxs)
        return result


def coerce_tree(tree, types, type_ctxs, dyn, valid_pair, inline_args, graph,
                constructors):
    Coercer(types, type_ctxs, dyn, valid_pair, inline_args, graph,
            constructors).visit(tree)
    return tree

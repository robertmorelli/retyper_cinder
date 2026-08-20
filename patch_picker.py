from ast import If, IfExp, Name, Load, Attribute, Call, copy_location, walk
from ast import NodeTransformer, NodeVisitor, Not, Slice, Subscript, UnaryOp
from ast import While, unparse

from cinderx_binding import get_ctx, is_primative, is_const

def _type_expr(name):
    parts = name.split(".")
    node = Name(id=parts[0], ctx=Load())
    for attr in parts[1:]:
        node = Attribute(value=node, attr=attr, ctx=Load())
    return node

# cbool is a CIntType, and CIntType.boxed hardcodes int -- see cbool_not_int_bro.md
def boxed_instance(t):
    env = t.klass.type_env
    return env.bool.instance if t.klass is env.cbool else t.klass.boxed.instance

# only these can be written back out as a constructor, so only these can be
# boxed in the exact form `box(T(x))`. A Literal[N] answers is_primative but
# has no constructor spelling, and boxing it fails in the strict loader.
PRIMITIVE_NAMES = {"double", "cbool", "int8", "int16", "int32", "int64",
                   "uint8", "uint16", "uint32", "uint64"}

def readable_name(t):
    if (inner := option_of(t)) is not None:
        # cinder rejects `Optional[X]`, accepts `X | None`
        return f"{readable_name(inner)} | None"
    name: str = t.klass.type_name.readable_name
    for k,v in {"chklist": "CheckedList", "chkdict": "CheckedDict", "chkset": "CheckedSet"}.items():
        name = name.replace(k, v)
    return name


def option_of(t):
    """The T of an `Optional[T]`, as an instance, or None if it is not one.

    Asked of the type rather than of its printed name. cinderx spells the same
    type three ways -- `Optional[T]`, `T | None`, and `T?` in a descriptor --
    and matching on any of them is a guess about which spelling arrived.
    `opt_type` is the union's own answer.
    """
    inner = getattr(t.klass, "opt_type", None) if t is not None else None
    return None if inner is None else inner.instance


def is_option_of(maybe, inner):
    """Is `maybe` exactly `inner`, or None?"""
    if maybe is None or inner is None:
        return False
    return (held := option_of(maybe)) is not None and held.klass is inner.klass

# node -> node
class Wrapper:
    T = None
    def __init__(self, node):
        self.node = self.next_root = node
    def wrap(self):
        return self.next_root

# node -> box(node)
class BoxWrapper(Wrapper):
    def __init__(self, node, t, exact=False):
        self.node = node
        self.T = boxed_instance(t)
        value = (copy_location(Call(_type_expr(readable_name(t)), [node], []), node)
                 if exact else node)
        self.next_root = copy_location(Call(Name("box", Load()), [value], []), node)

# node -> T(node)
class ConstrWrapper(Wrapper):
    def __init__(self, T, node):
        self.node, self.T = node, T
        self.next_root = copy_location(Call(_type_expr(readable_name(T)), [node], []), node)

DONT_CAST = ('int',)
# the boxed scalars, which convert what they are handed the way a machine type
# does. `float(x)` is a conversion; `cast(float, x)` is an assertion.
CONVERTIBLE = ('float', 'int', 'bool')
# containers whose slice comes back as a plain list
CHECKED_NAMES = ('chklist', 'CheckedList', 'chkdict', 'CheckedDict')
# node -> cast(T,node)
class CastWrapper(Wrapper):
    def __init__(self, T, node):
        if readable_name(T) in DONT_CAST:
            # every slot we tried accepts an inexact int, so the cast checks
            # nothing. T stays None, which keeps _record from filing a
            # coercion that is not there.
            return super().__init__(node)
        self.node, self.T = node, T
        self.next_root = copy_location(
            Call(Name("cast", Load()), [_type_expr(readable_name(T)), node], []), node)

# Nothing is written back. A wrapper this pass builds is never read: the
# position above strips straight through it and decides from the value at the
# bottom, so what we might record about it -- its type, its context, the
# context of what it wraps, the propagation to a sibling -- is never asked for.
# Measured by removing each write and then all of them together: 0 of 216 masks
# change, and 48 of 48 samples still compile.


# TODO: figure out if this produces enough casts
def _still_required(produced, held, type_ctx, readers, dyn):
    """The type this position must go on producing, or None.

    Stripping is unconditional, so this is all that stands between a tower and
    the value inside it -- and it is a wrap choice, which is why it is here and
    not in the walk. Not "may I strip" but "what has to come back".

      cast(DeviceTaskRec, r) in an Any slot   -> DeviceTaskRec, because the
          cast is the only thing typing the declaration
      cast(Task, x) over an Optional[Task]    -> Task, and no reader has to
          want it: the None it keeps out arrives at runtime regardless
      int64(k) in an Any slot                 -> nothing. A primitive cannot
          sit in a dynamic slot, so there is no type to keep

    `produced` is what the position yielded before the layers came off, and is
    None where none did: with the tower intact the position still produces what
    it always did, and comparing its type to its operand's would call every
    `not` over a dynamic a loss. `readers` is what reads this position, and an
    entry that is None is a consumer the walk has not reached yet, which counts
    -- postorder means the reads of a declaration are decided after it.
    """
    if produced is None or produced is held or produced is dyn:
        return None
    if is_primative(produced) and type_ctx is dyn:
        return None
    if is_option_of(held, produced):
        return produced
    return produced if any(read is not dyn for read in readers) else None


def _choose(node, type, type_ctx, valid_pair, is_inline_arg, dyn=None,
            payload=False, produced=None, readers=()):
    if (required := _still_required(produced, type, type_ctx, readers,
                                    dyn)) is not None:
        # rebuilt whatever the pair would otherwise say. A machine value boxes;
        # a type with a constructor is constructed, `float(x)` being the
        # conversion where `cast(float, x)` only asserts; a class is a cast.
        if is_primative(type) and not is_primative(required):
            return BoxWrapper(node, type, is_inline_arg)
        if is_primative(required) or readable_name(required) in CONVERTIBLE:
            return ConstrWrapper(required, node)
        return CastWrapper(required, node)
    if payload and type is dyn and type_ctx is not dyn:
        # `[s for s in sources]` builds its container out of what the payload
        # yields, and chklist[dynamic] is not chklist[T] however assignable the
        # elements are. Only a dynamic needs pinning; T1 < T2 is fine.
        return CastWrapper(type_ctx, node)
    if is_option_of(type, type_ctx):
        # the cast is what narrows: drop it and the None reaches the use site.
        return CastWrapper(type_ctx, node)
    if dyn is not None and type_ctx is dyn and is_primative(type) and not is_const(node):
        # a primitive does not fit a dynamic slot whatever check_can_assign_from
        # says, and erasure makes exactly this: the annotation that made the
        # slot primitive is gone and the value still is one. `is_const` is the
        # exception -- cinder will not box a `Literal[True]` at all.
        return BoxWrapper(node, type, is_inline_arg)
    if (node.__class__ is Subscript and node.slice.__class__ is Slice
            and readable_name(type_ctx).startswith(CHECKED_NAMES)):
        # a slice hands back a plain list whatever it was taken from, and
        # `cast` checks exactness and fails on it, so the container is rebuilt.
        # Must precede `valid_pair`, which counts dynamic as fine here.
        return ConstrWrapper(type_ctx, node)
    if not is_inline_arg and valid_pair(type, type_ctx, node):
        return Wrapper(node)
    elif type == type_ctx:
        return Wrapper(node)
    elif is_primative(type):
        if is_const(node):
            return Wrapper(node)
        else:
            return BoxWrapper(node, type)
    elif is_primative(type_ctx):
        return ConstrWrapper(type_ctx, node)
    else:
        # a cast to dynamic, reached only where `is_inline_arg` held the
        # position off `valid_pair`. It makes two arguments of an @inline call
        # agree once cinderx substitutes the body.
        return CastWrapper(type_ctx, node)

# the single-argument conversions, by the only name each can be called under.
# `cast` is the odd one: two arguments, and the value is the second.
UNWRAPPABLE = {"box"} | PRIMITIVE_NAMES


class Operators(NodeVisitor):
    """File what each unary operator in a stripped tower yields.

    A negation is a truth value -- cbool over a primitive, bool otherwise --
    while `-` and `~` keep their operand's type. Without it held_karp emits
    `box(bits) & ~other`, the int64 escaping into a boxed `&`.

    Operators only. `generic_visit` stops rather than descending, or a `not`
    buried inside the value at the bottom would be retyped as though it were
    part of the tower.
    """

    def __init__(self, types, env):
        self.types, self.env = types, env

    def visit_UnaryOp(self, node):
        self.visit(node.operand)
        produced = self.types.get(node.operand)
        if isinstance(node.op, Not):
            self.types[node] = (self.env.cbool.instance
                                if produced is not None and is_primative(produced)
                                else self.env.bool.instance)
        elif produced is not None:
            self.types[node] = produced

    def generic_visit(self, node):
        return


class Tower(NodeTransformer):
    """An expression with every coercion layer taken off.

    `peel` is the one definition of "is this a layer"; what it hands back is
    dispatched again, since it may be another. `generic_visit` is the stop, and
    it must be: left at its default it would walk into a call's arguments and a
    subscript's index, which are separate positions the walk owns.
    """

    def peel(self, node):
        """What a single coercion holds, or None if it is not a coercion."""
        if not (isinstance(node, Call) and isinstance(node.func, Name)):
            return None
        if node.func.id == "cast" and len(node.args) == 2:
            return node.args[1]
        if node.func.id in UNWRAPPABLE and len(node.args) == 1:
            return node.args[0]
        return None

    def visit(self, node):
        if inner := self.peel(node):
            return self.visit(inner)
        return super().visit(node)

    def ends(self, node):
        """The stripped tower and the value at the bottom of it.

        One descent answers both. The top is what stands in the position, the
        bottom is what its type is read from, and asking for the second with a
        separate walk over the first was the same descent twice.
        """
        top = self.visit(node)
        bottom = top
        while isinstance(bottom, UnaryOp):
            bottom = bottom.operand
        return top, bottom

    def visit_UnaryOp(self, node):
        """The operator is kept; hand back the bare operand and every `not`
        inverts. Unchanged is the same node, not a copy: the caller reads
        "nothing came off" off identity.
        """
        operand = self.visit(node.operand)
        return node if operand is node.operand else copy_location(
            UnaryOp(op=node.op, operand=operand), node)

    def generic_visit(self, node):
        return node


def spell_len(call, asked_for, types, type_ctxs, dyn, is_test):
    """`clen` and `len` are one measurement in two spellings.

      box(clen(e))     -> len(e)     if e has a fast length
      int64(len(e))    -> clen(e)     "
      <test> len(e)    -> clen(e)     "

    `asked_for` is the layer just stripped off this position and is the whole
    demand: a box wanted an object, a primitive constructor wanted a machine
    word. Never dropped -- a length is not a conversion of its operand -- so
    the layer goes and the spelling carries what it meant.

    The fast-length test is cinderx's own, the one `CLenFunction.bind_call`
    accepts or rejects the call with. It is defined on `Value` and answers None
    there, so the only untyped case is a position the binder never reached.
    """
    if not (isinstance(call, Call) and isinstance(call.func, Name)
            and call.func.id in ("clen", "len") and len(call.args) == 1):
        return
    measured = types.get(call.args[0])
    fast = (measured is not None and measured is not dyn
            and measured.get_fast_len_type() is not None)
    primitive = fast and (is_test or asked_for in PRIMITIVE_NAMES)
    call.func.id = "clen" if primitive else "len"
    types[call] = dyn.klass.type_env.int64.instance if primitive else dyn


def mediate(node, types, type_ctxs, dyn, valid_pair, graph, payloads,
            is_test=False, is_index=False, is_inline_arg=False):
    """Every edit the mediation walk makes at one position, decided here.

    The walk visits bottom up and hands each node to this; what comes back is
    what stands in that position. Nothing else edits the tree.

      a. every layer comes off, leaving the unary operators over the value
         they read.
      b. one `_choose`, from the type at the bottom of that tower against the
         demand on its top -- never from the wrapper that happened to be
         there, whose type is an artifact of the last pass.
      c. a test, an index, a slice and a store take no narrowing wrapper, and
         `clen`/`len` is a spelling rather than a wrap.

    `is_test`, `is_index` and `is_inline_arg` are told to this function rather
    than read off the node: only the walk knows what a node is being used as.
    An inline argument is held off `valid_pair` and boxed in the exact form,
    because cinderx substitutes the body at the call site and two arguments
    that no longer agree meet inside it. `dyn` must be
    the sentinel of the bind these tables came from, never another Compiler's.
    `graph` is read for `outgoing` and `cell` and nothing else -- the mediator
    meets obligations, it does not negotiate them.
    """
    # ------------------------------------------------------------- tables

    # --------------------------------------------- c. the special positions

    # ------------------------------------------------------------- the walk

    ctx = get_ctx(node)
    if isinstance(node, Slice) or (ctx is not None
                                   and not isinstance(ctx, Load)):
        # an assignment target, a deletion and a slice are not positions a
        # wrapper can wrap, so nothing here is decided at all
        return node

    # a. nothing here decides whether to strip. A tower that carried something
    # the position still needs gets it back in part b, as a wrapper chosen for
    # that reason rather than as a tower left standing because a veto fired.
    tower, value = Tower().ends(node)
    if tower is not node:
        Operators(types, dyn.klass.type_env).visit(tower)
    spell_len(tower, node.func.id if tower is not node
              and isinstance(node, Call) and isinstance(node.func, Name)
              else None, types, type_ctxs, dyn, is_test)

    # b. `Operators` has just filed what the stripped tower yields, so the
    # tower's own entry is the bottom's type carried up through the operators
    # -- not the same number for a `not`.
    target = tower
    t, tc = types.get(target), type_ctxs.get(node)
    result = target if not (t and tc) else _choose(
        target, t, tc, valid_pair, is_inline_arg, dyn,
        node in payloads, None if tower is node else types.get(node),
        [types.get(where) for where, slot in
         graph.outgoing().get(graph.cell(node, "type"), ())
         if slot == "type"]).wrap()

    # c. a branch discards its test and a subscript demands no primitive of
    # its index, so in both the wrapper only narrows: `cbool(x)` makes a
    # dynamic answer to exactly `bool`, and `int64(i)` wraps an index too large
    # for 64 bits where the bare subscript would have raised. Only the
    # narrowing comes off an index -- a `box` there is what makes a primitive
    # usable as a key. Nothing reads a test, so nothing is recorded.
    if is_test:
        inner = Tower().peel(result)
        if inner is not None:
            result = inner
    elif (is_index and isinstance(result, Call)
            and isinstance(result.func, Name) and result.func.id == "int64"
            and len(result.args) == 1):
        result = result.args[0]
    return result

from re import compile
from ast import Name, Load, Attribute, Call, copy_location, walk
from get_ast_data import is_primative, is_const

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

_OPT = compile(r"^Optional\[(.+)\]$")

# only these can be written back out as a constructor, so only these can be
# boxed in the exact form `box(T(x))`. A Literal[N] answers is_primative but
# has no constructor spelling, and boxing it fails in the strict loader.
PRIMITIVE_NAMES = {"double", "cbool", "int8", "int16", "int32", "int64",
                   "uint8", "uint16", "uint32", "uint64"}

def readable_name(t):
    name: str = t.klass.type_name.readable_name
    for k,v in {"chklist": "CheckedList", "chkdict": "CheckedDict", "chkset": "CheckedSet"}.items():
        name = name.replace(k, v)
    m = _OPT.match(name)          # cinder rejects `Optional[X]`, accepts `X | None`
    return m.group(1) + " | None" if m else name

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

# record the wrap in the type tables: the new node takes the context the
# wrapped node was in, and the wrapped node now needs no further coercion
def _record(w, node, t, tc, types, ctxs, dyn, constructors):
    if w.next_root is not node:
        types[w.next_root] = w.T
        ctxs[w.next_root] = tc
        ctxs[node] = t
        # the callee expression we synthesized: box/cast are real functions,
        # the T(..) type expression is not
        fT = dyn.klass.type_env.function.instance if isinstance(w, (BoxWrapper, CastWrapper)) else dyn
        for sub in walk(w.next_root.func):
            types[sub] = ctxs[sub] = fT
        # a constructor this pass writes joins the ones the author wrote. The
        # map is keyed by callee node, so a wrapper built here is invisible to
        # anything reading it -- which left the tower collapser unable to see
        # through a layer it had just built, rebuilding the tower it was asked
        # to remove. Registering by node keeps the identity check honest;
        # matching on the name instead would catch `clen`, which computes a
        # length rather than coercing its operand.
        if isinstance(w, ConstrWrapper):
            constructors[w.next_root.func] = w.T.klass
        elif isinstance(w, BoxWrapper):
            boxed = w.next_root.args[0]
            if boxed is not node:                 # the exact form, box(T(x))
                constructors[boxed.func] = t.klass
    return w

def _narrows_optional(type, type_ctx):
    """Optional[T] flowing into a T context.

    The cast is what does the narrowing, so it is load-bearing even when the
    pair would otherwise look assignable -- drop it and the None reaches the
    use site at runtime.
    """
    if type is None or type_ctx is None:
        return False
    inner = readable_name(type_ctx)
    return readable_name(type) in (f"Optional[{inner}]", f"{inner} | None")


# TODO: figure out if this produces enough casts
def _choose(node, type, type_ctx, valid_pair, needs_exact, dyn=None, compared=False):
    if _narrows_optional(type, type_ctx):
        return CastWrapper(type_ctx, node)
    # Boxing a machine literal in a dynamic slot -- `box(double(2.0))` -- fixes
    # nbody's `dynamic / 2.0`, three level masks. It also breaks richards and
    # deltablue at import with `can't box non-primitive: Literal[True]`: the
    # target type is a real cbool, but the constant itself is a Literal and
    # cinder will not box it. Guarding on the target type's name does not help,
    # the guard has to be on the constant's own type. Only the strict loader
    # sees this; stage two's rebind accepts it.
    if dyn is not None and type_ctx is dyn and is_primative(type) and not is_const(node):
        # a primitive does not fit a dynamic slot, whatever check_can_assign_from
        # says: cinder rejects `int64 cannot be assigned to dynamic` outright.
        # This is the case erasure creates -- the annotation that made the slot
        # primitive is gone, and the value still is one, so it has to box.
        return BoxWrapper(node, type, node in needs_exact)
    if node not in needs_exact and valid_pair(type, type_ctx, node):
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
        # A cast to `object` -- to dynamic -- reaches this branch only where
        # needs_exact kept it off `valid_pair` above, which is the argument of
        # an @inline call and nothing else. That cast is load-bearing: it is
        # what makes two arguments agree once cinderx substitutes the body.
        return CastWrapper(type_ctx, node)

def pick_patch(node, type, type_ctx, valid_pair, needs_exact, types, ctxs, dyn,
               constructors, compared=False):
    return _record(_choose(node, type, type_ctx, valid_pair, needs_exact, dyn,
                           compared),
                   node, type, type_ctx, types, ctxs, dyn, constructors)
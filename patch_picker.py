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

def readable_name(t):
    name: str = t.klass.type_name.readable_name
    for k,v in {"chklist": "CheckedList", "chkdict": "CheckedDict", "chkset": "CheckedSet"}.items():
        name = name.replace(k, v)
    return name

# node -> node
class Wrapper:
    T = None
    def __init__(self, node):
        self.node = self.next_root = node
    def wrap(self):
        return self.next_root

# node -> box(node)
class BoxWrapper(Wrapper):
    def __init__(self, node, t):
        self.node = node
        self.T = boxed_instance(t)
        self.next_root = copy_location(Call(Name("box", Load()), [node], []), node)

# node -> T(node)
class ConstrWrapper(Wrapper):
    def __init__(self, T, node):
        self.node, self.T = node, T
        self.next_root = copy_location(Call(_type_expr(readable_name(T)), [node], []), node)

# node -> cast(T,node)
class CastWrapper(Wrapper):
    def __init__(self, T, node):
        self.node, self.T = node, T
        self.next_root = copy_location(
            Call(Name("cast", Load()), [_type_expr(readable_name(T)), node], []), node)

# node -> _cast(Any, node)
class CastAnyWrapper(Wrapper):
    def __init__(self, node, dyn):
        self.node, self.T = node, dyn
        self.next_root = copy_location(
            Call(Name("_cast", Load()), [Name("Any", Load()), node], []), node)

# record the wrap in the type tables: the new node takes the context the
# wrapped node was in, and the wrapped node now needs no further coercion
def _record(w, node, t, tc, types, ctxs, dyn):
    if w.next_root is not node:
        types[w.next_root] = w.T
        ctxs[w.next_root] = tc
        ctxs[node] = t
        # the callee expression we synthesized: box/cast are real functions,
        # _cast and the T(..) type expression are not
        fT = dyn.klass.type_env.function.instance if isinstance(w, (BoxWrapper, CastWrapper)) else dyn
        for sub in walk(w.next_root.func):
            types[sub] = ctxs[sub] = fT
    return w

# TODO: figure out if this produces enough casts
def _choose(node, type, type_ctx, valid_pair, needs_exact):
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
        return CastWrapper(type_ctx, node)

def pick_patch(node, type, type_ctx, valid_pair, needs_exact, types, ctxs, dyn):
    return _record(_choose(node, type, type_ctx, valid_pair, needs_exact), node, type, type_ctx, types, ctxs, dyn)

def pick_erasure_wrap(node, type, type_ctx, dyn, types, ctxs):
    if type is None:
        return Wrapper(node)
    # not valid
    # if is_primative(type):
    #     return BoxWrapper(node)
    w = _record(CastAnyWrapper(node, dyn), node, type, type_ctx, types, ctxs, dyn)
    if is_primative(type):      # erasure destroys the primitive context; the value boxes
        types[node] = ctxs[node] = boxed_instance(type)
    return w
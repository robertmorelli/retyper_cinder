from ast import Name, Load, Attribute, Call, copy_location
from get_ast_data import is_primative, is_const

def _type_expr(name):
    parts = name.split(".")
    node = Name(id=parts[0], ctx=Load())
    for attr in parts[1:]:
        node = Attribute(value=node, attr=attr, ctx=Load())
    return node

# node -> node
class Wrapper:
    def __init__(self, node):
        self.node = node
    def wrap(self):
        return self.node

# node -> box(node)
class BoxWrapper:
    def __init__(self, node):
        self.node = node
    def wrap(self):
        return copy_location(Call(Name("box", Load()), [self.node], []), self.node)

# node -> T(node)
class ConstrWrapper:
    def __init__(self, T, node):
        self.T = T
        self.node = node
    def wrap(self):
        name = self.T.klass.type_name.readable_name
        return copy_location(Call(_type_expr(name), [self.node], []), self.node)

# node -> cast(T,node)
class CastWrapper:
    def __init__(self, T, node):
        self.T = T
        self.node = node
    def wrap(self):
        name = self.T.klass.type_name.readable_name
        return copy_location(Call(Name("cast", Load()), [_type_expr(name), self.node], []), self.node)

# TODO: figure out if this produces enough casts
def pick_patch(node, type, type_ctx, valid_pair):
    if valid_pair(type, type_ctx, node):
        return Wrapper(node)
    if is_primative(type):
        if is_const(node):
            return Wrapper(node)
        else:
            return BoxWrapper(node)
    elif is_primative(type_ctx):
        return ConstrWrapper(type_ctx, node)
    else:
        return CastWrapper(type_ctx, node)


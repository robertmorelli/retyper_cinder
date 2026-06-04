from get_ast_data import is_primative

from cinderx.compiler.static.types import Class

class Wrapper:
    def wrap(self, node):
        return node

class BoxWrapper:
    def wrap(self, node):
        # emit box call with node inside
        pass

class CastWrapper:
    def __init__(self, T: Class):
        self.T = T

    def wrap(self, node):
        # emit cast call with T and node inside
        # type.klass.type_name.readable_name
        pass

class ConstrWrapper:
    def __init__(self, T: Class):
        self.T = T

    def wrap(self, node):
        # emit T call with node inside
        # type.klass.type_name.readable_name
        pass

def pick_patch(node, type, type_ctx, valid_pair):
    if valid_pair(type, type_ctx, node):
        return Wrapper()
    if is_primative(type):
        return BoxWrapper(node)
    elif is_primative(type_ctx):
        return ConstrWrapper(type_ctx)
    else:
        return CastWrapper(type_ctx)


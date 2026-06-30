from ast import NodeTransformer, Call, Name, Load, copy_location
from patch_picker import pick_patch

class PatchAdder(NodeTransformer):
    def __init__(self, types, type_ctxs, valid_pair, inflow_nodes):
        self.types = types
        self.type_ctxs = type_ctxs
        self.valid_pair = valid_pair
        self.inflow_nodes = inflow_nodes

    def visit(self, node):
        self.generic_visit(node)
        result = node
        if (t := self.types.get(node)) and (tc := self.type_ctxs.get(node)):
            result = pick_patch(node, t, tc, self.valid_pair).wrap()
        ctx = getattr(node, "ctx", None)
        if node in self.inflow_nodes and (ctx is None or isinstance(ctx, Load)):
            result = copy_location(
                Call(Name("_cast", Load()), [Name("Any", Load()), result], []),
                result,
            )
        return result

def add_patches(tree, types, type_ctxs, valid_pair, inflow_nodes):
    PatchAdder(types, type_ctxs, valid_pair, inflow_nodes).visit(tree)
    return tree

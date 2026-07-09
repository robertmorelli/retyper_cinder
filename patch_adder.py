from ast import NodeTransformer, Load, Name, Attribute, Subscript, Starred, List, Tuple
from patch_picker import pick_patch, pick_erasure_wrap
from get_ast_data import get_ctx

class PatchAdder(NodeTransformer):
    def __init__(self, types, type_ctxs, valid_pair, inflow_nodes, needs_exact):
        self.types = types
        self.type_ctxs = type_ctxs
        self.valid_pair = valid_pair
        self.inflow_nodes = inflow_nodes
        self.needs_exact = needs_exact

    def visit(self, node):
        self.generic_visit(node)
        result = node
        if (t := self.types.get(node)) and (tc := self.type_ctxs.get(node)):
            result = pick_patch(node, t, tc, self.valid_pair, self.needs_exact).wrap()
        ctx = get_ctx(node)
        if node in self.inflow_nodes and (ctx is None or isinstance(ctx, Load)):
            result = pick_erasure_wrap(result, self.types.get(node)).wrap()
        return result

def add_patches(tree, types, type_ctxs, valid_pair, inflow_nodes, needs_exact):
    PatchAdder(types, type_ctxs, valid_pair, inflow_nodes, needs_exact).visit(tree)
    return tree

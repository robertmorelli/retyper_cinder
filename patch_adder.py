from ast import NodeTransformer, Load, Name, Attribute, Subscript, Starred, List, Tuple
from ast import Assign, AnnAssign, AugAssign, For
from patch_picker import pick_patch, pick_erasure_wrap
from get_ast_data import get_ctx

class PatchAdder(NodeTransformer):
    def __init__(self, types, type_ctxs, dyn, valid_pair, inflow_nodes, needs_exact):
        self.types = types
        self.type_ctxs = type_ctxs
        self.dyn = dyn
        self.valid_pair = valid_pair
        self.inflow_nodes = inflow_nodes
        self.needs_exact = needs_exact

    def visit(self, node):
        self.generic_visit(node)
        # children are patched by now, so a binding can take its source's type
        if isinstance(node, (Assign, AnnAssign, AugAssign)) and node.value is not None:
            if (tv := self.types.get(node.value)) is not None:
                for tgt in (node.targets if isinstance(node, Assign) else [node.target]):
                    self.types[tgt] = tv
        if isinstance(node, For) and isinstance(node.target, Name):
            if self.types.get(node.iter) is self.dyn:
                self.types[node.target] = self.dyn
        result = node
        if (t := self.types.get(node)) and (tc := self.type_ctxs.get(node)):
            result = pick_patch(node, t, tc, self.valid_pair, self.needs_exact,
                                self.types, self.type_ctxs, self.dyn).wrap()
        ctx = get_ctx(node)
        if node in self.inflow_nodes and (ctx is None or isinstance(ctx, Load)):
            result = pick_erasure_wrap(result, self.types.get(node),
                                       self.type_ctxs.get(result) or self.dyn,
                                       self.dyn, self.types, self.type_ctxs).wrap()
        return result

def add_patches(tree, types, type_ctxs, dyn, valid_pair, inflow_nodes, needs_exact):
    PatchAdder(types, type_ctxs, dyn, valid_pair, inflow_nodes, needs_exact).visit(tree)
    return tree

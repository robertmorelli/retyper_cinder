import ast
from patch_picker import pick_patch

class PatchAdder(ast.NodeTransformer):
    def __init__(self, types, type_ctxs, valid_pair):
        self.types = types
        self.type_ctxs = type_ctxs
        self.valid_pair = valid_pair

    def visit(self, node):
        self.generic_visit(node)
        if (t := self.types.get(node)) and (tc := self.type_ctxs.get(node)):
            return pick_patch(node, t, tc, self.valid_pair).wrap()
        return node

def add_patches(tree, types, type_ctxs, valid_pair):
    PatchAdder(types, type_ctxs, valid_pair).visit(tree)
    return tree

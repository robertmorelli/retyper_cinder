from ast import Name, NodeTransformer

class LenClenFixer(NodeTransformer):
    def __init__(self, types, dyn):
        self.types = types
        self.dyn = dyn

    def visit_Call(self, node):
        self.generic_visit(node)
        if isinstance(node.func, Name):
            if node.func.id == "clen":
                if t := self.types.get(node.args[0]):
                    if t == self.dyn:
                        node.func.id = "len"
                        self.types[node] = self.dyn
            # TODO: patch imports to get clen and check outer context for box wrap
            # elif node.func.id == "len":
            #     if t := self.types.get(node.args[0]):
            #         if t is not self.dyn and t.get_fast_len_type() is not None:
            #             node.func.id = "clen"
        return node

def fix_len(tree, types, dyn):
    LenClenFixer(types, dyn).visit(tree)
    return tree
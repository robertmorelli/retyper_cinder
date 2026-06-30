from ast import Name, Load, NodeTransformer

class AnnoRemover(NodeTransformer):
    def __init__(self, targets):
        self.targets = targets

    def visit_arg(self, node):
        if node not in self.targets: return node
        node.annotation = Name(id='Any', ctx=Load())
        return node

    def visit_FunctionDef(self, node):
        self.generic_visit(node)
        if node not in self.targets: return node
        node.returns = Name(id='Any', ctx=Load())
        return node

    def visit_AnnAssign(self, node):
        self.generic_visit(node)
        if node not in self.targets: return node
        node.annotation = Name(id='Any', ctx=Load())
        return node


def remove_annotations(tree, node_set):
    AnnoRemover(node_set).visit(tree)
    return tree

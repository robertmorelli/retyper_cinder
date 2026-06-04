import ast

class Detyper(ast.NodeTransformer):
    def __init__(self, targets):
        self.targets = targets

    def visit_arg(self, node):
        if node in self.targets:
            node.annotation = None
        return node

    def visit_FunctionDef(self, node):
        self.generic_visit(node)
        if node in self.targets:
            node.returns = None
        return node

    def visit_AnnAssign(self, node):
        if node not in self.targets:
            return node
        return ast.copy_location(ast.Assign(targets=[node.target], value=node.value), node)

def remove_annotations(tree, node_set):
    Detyper(node_set).visit(tree)
    return tree

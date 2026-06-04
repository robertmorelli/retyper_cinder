import ast

class Detyper(ast.NodeTransformer):
    def __init__(self, targets):
        self.targets = targets
        self.translation = {}

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
        if node.value is None:
            self.translation[node] = None
            return None
        new = ast.copy_location(
            ast.Assign(targets=[node.target], value=node.value), node)
        self.translation[node] = new
        return new

def remove_annotations(tree, node_set):
    detyper = Detyper(node_set)
    detyper.visit(tree)
    return tree, detyper.translation
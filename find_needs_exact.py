from sys import path
from ast import NodeTransformer
from ast import FunctionDef, unparse

path.insert(0, "_cinderx/cinderx/PythonLib")
from cinderx.compiler.static.types import CType

class InlineCallArgFinder(NodeTransformer):
    def __init__(self, reverse_outflow):
        self.reverse_outflow = reverse_outflow
        self.needs_exact = set()

    def visit_Call(self, node):
        if source := self.reverse_outflow.get(node):
            if isinstance(source, FunctionDef):
                if "inline" in set(map(unparse, source.decorator_list)):
                    self.needs_exact |= set(node.args)
        self.generic_visit(node)
        return node

class IteratorFinder(NodeTransformer):
    def __init__(self):
        self.needs_exact = set()

    def visit_ListComp(self, node):
        self.needs_exact |= set((node.elt,))
        self.generic_visit(node)
        return node

def find_needs_exact(tree, reverse_outflow):
    inline_finder = InlineCallArgFinder(reverse_outflow)
    iter_finder = IteratorFinder()
    inline_finder.visit(tree)
    iter_finder.visit(tree)
    return inline_finder.needs_exact | iter_finder.needs_exact
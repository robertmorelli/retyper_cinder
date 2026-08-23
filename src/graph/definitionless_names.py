"""Collect names which intentionally have no typedness-graph source."""
import ast


class DefinitionlessNames(ast.NodeVisitor):
    """Index visible names whose definitions do not provide graph cells.

    They do have definitions, but not ones we can point directly to: function
    definitions, class definitions, and imports establish that a name exists
    without supplying an ordinary definition-to-read typedness edge.
    """

    def __init__(self):
        self.names = {}
        self.scope = None
        self.in_class = False

    def add(self, name):
        if not self.in_class:
            self.names.setdefault(self.scope, set()).add(name)

    def visit_function(self, node):
        self.add(node.name)
        outer_scope, outer_class = self.scope, self.in_class
        self.scope, self.in_class = node, False
        for statement in node.body:
            self.visit(statement)
        self.scope, self.in_class = outer_scope, outer_class

    def visit_FunctionDef(self, node):
        self.visit_function(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.add(node.name)
        outer_class = self.in_class
        self.in_class = True
        for statement in node.body:
            self.visit(statement)
        self.in_class = outer_class

    def visit_Import(self, node):
        for name in node.names:
            self.add(name.asname or name.name.split(".", 1)[0])

    def visit_ImportFrom(self, node):
        for name in node.names:
            if name.name != "*":
                self.add(name.asname or name.name)


def collect_definitionless_names(tree):
    """Return lexical scope -> names known without a graph definition cell."""
    collector = DefinitionlessNames()
    collector.visit(tree)
    return collector.names

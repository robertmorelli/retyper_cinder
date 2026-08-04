from ast import NodeTransformer, Import, ImportFrom, alias
STATIC_NAMES = ('box', 'cast', 'int64', 'cbool', 'clen')

def _is_static_flag(stmt):
    return isinstance(stmt, Import) and any(a.name == '__static__' and a.asname is None
                                            for a in stmt.names)

def _insert_point(body):
    """First index that new imports may occupy.

    Static Python only compiles a module when a bare `import __static__` precedes
    every other import, so anything added here has to land after it -- and after
    the __future__ imports it in turn has to follow.
    """
    i = 0
    for j, stmt in enumerate(body):
        if (isinstance(stmt, ImportFrom) and stmt.module == '__future__') or _is_static_flag(stmt):
            i = j + 1
    return i

class StaticImportAdder(NodeTransformer):
    def __init__(self):
        self.found = False
    def visit_ImportFrom(self, node):
        if node.module == '__static__':
            self.found = True
            have = {a.name for a in node.names}
            node.names += [alias(name=n) for n in STATIC_NAMES if n not in have]
        return node

class TypingImportAdder(NodeTransformer):
    def visit_Module(self, node):
        node.body.insert(_insert_point(node.body),
                         ImportFrom(module='typing',
                                    names=[alias(name='Any'),
                                           alias(name='cast', asname='_cast')], level=0))
        return node

def add_all_static(tree):
    tree.body.insert(_insert_point(tree.body),
                     ImportFrom(module='__static__',
                                names=[alias(name=n) for n in STATIC_NAMES], level=0))

def add_imports(tree):
    sa = StaticImportAdder()
    sa.visit(tree)
    if not sa.found:
        add_all_static(tree)
    TypingImportAdder().visit(tree)
    return tree

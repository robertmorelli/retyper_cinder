"""Import exactly the names the earlier passes wrote into the tree.

This runs last, on the finished tree, so it does not have to guess: whatever
`box`, `cast`, `int64`, `cbool`, `clen` or `Any` survived the coercion pass is
what gets imported. Adding all of them unconditionally left modules importing
five __static__ names they never used, which reads nothing like the program a
person would have written.
"""
from ast import (Import, ImportFrom, Load, Name, NodeTransformer, alias, walk)

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


def find_needed_imports(tree):
    """The names this module still reads, of the ones we are here to supply.

    A read is a Load of the bare name, which is how every wrapper the coercer
    builds and every annotation the remover writes spells it. Reading too
    widely only costs an unused import; reading too narrowly breaks the module,
    so a local that happens to be called `cast` counts, and that is fine.
    """
    read = {node.id for node in walk(tree)
            if isinstance(node, Name) and isinstance(node.ctx, Load)}
    return tuple(n for n in STATIC_NAMES if n in read), 'Any' in read


def _already_imported(tree, module, name):
    return any(isinstance(stmt, ImportFrom) and stmt.module == module
               and any(a.name == name for a in stmt.names)
               for stmt in walk(tree))


class StaticImportAdder(NodeTransformer):
    """Extend the module's own `from __static__ import ...` rather than add a second."""

    def __init__(self, needed):
        self.needed = needed
        self.found = False

    def visit_ImportFrom(self, node):
        if node.module == '__static__':
            self.found = True
            have = {a.name for a in node.names}
            node.names += [alias(name=n) for n in self.needed if n not in have]
        return node


def add_imports(tree):
    needed, wants_any = find_needed_imports(tree)
    if needed:
        adder = StaticImportAdder(needed)
        adder.visit(tree)
        if not adder.found:
            tree.body.insert(_insert_point(tree.body),
                             ImportFrom(module='__static__',
                                        names=[alias(name=n) for n in needed],
                                        level=0))
    if wants_any and not _already_imported(tree, 'typing', 'Any'):
        tree.body.insert(_insert_point(tree.body),
                         ImportFrom(module='typing',
                                    names=[alias(name='Any')], level=0))
    return tree

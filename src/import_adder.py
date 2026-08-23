"""Import the names used by the finished transformed tree."""
from ast import (Constant, Import, ImportFrom, Load, Name, NodeTransformer,
                 alias, walk)
from re import findall

STATIC_NAMES = ('box', 'cast', 'int64', 'cbool', 'clen')


def _is_static_flag(stmt):
    return isinstance(stmt, Import) and any(a.name == '__static__' and a.asname is None
                                            for a in stmt.names)


def _insert_point(body):
    """Return the first index after future imports and the static flag."""
    i = 0
    for j, stmt in enumerate(body):
        if (isinstance(stmt, ImportFrom) and stmt.module == '__future__') or _is_static_flag(stmt):
            i = j + 1
    return i


def find_names_read(tree):
    """Return every bare name the transformed module may read."""
    read = {node.id for node in walk(tree)
            if isinstance(node, Name) and isinstance(node.ctx, Load)}
    # Include strings so names used only in quoted annotations remain imported.
    for node in walk(tree):
        if isinstance(node, Constant) and isinstance(node.value, str):
            read |= set(findall(r'\b\w+\b', node.value))
    return read


def _already_imported(tree, module, name):
    return any(isinstance(stmt, ImportFrom) and stmt.module == module
               and any(a.name == name for a in stmt.names)
               for stmt in walk(tree))


class StaticImports(NodeTransformer):
    """Synchronize named static and typing imports without touching the flag."""

    PRUNED = ('__static__', 'typing')

    def __init__(self, needed, read):
        self.needed = needed
        self.read = read
        self.found = False

    def visit_ImportFrom(self, node):
        if node.module not in self.PRUNED:
            return node
        keep = [a for a in node.names if a.name in self.read or a.asname]
        if node.module == '__static__':
            self.found = True
            have = {a.name for a in keep}
            keep += [alias(name=n) for n in self.needed if n not in have]
        if not keep:
            return None
        node.names = keep
        return node


def add_imports(tree):
    read = find_names_read(tree)
    needed = tuple(n for n in STATIC_NAMES if n in read)
    adder = StaticImports(needed, read)
    adder.visit(tree)
    if needed and not adder.found:
        tree.body.insert(_insert_point(tree.body),
                         ImportFrom(module='__static__',
                                    names=[alias(name=n) for n in needed],
                                    level=0))
    if 'Any' in read and not _already_imported(tree, 'typing', 'Any'):
        tree.body.insert(_insert_point(tree.body),
                         ImportFrom(module='typing',
                                    names=[alias(name='Any')], level=0))
    return tree

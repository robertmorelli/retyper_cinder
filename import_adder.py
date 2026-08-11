"""Import exactly the names the earlier passes wrote into the tree.

This runs last, on the finished tree, so it does not have to guess: whatever
`box`, `cast`, `int64`, `cbool`, `clen` or `Any` survived the coercion pass is
what gets imported. Adding all of them unconditionally left modules importing
five __static__ names they never used, which reads nothing like the program a
person would have written.
"""
from ast import (Constant, Import, ImportFrom, Load, Name, NodeTransformer,
                 alias, walk)
from re import findall

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


def find_names_read(tree):
    """Every bare name this module still reads.

    A Load of the bare name is how a wrapper the coercer builds, an annotation
    the remover writes, and a decorator the author wrote are all spelled, so
    one sweep answers for all of them. Reading too widely only costs an unused
    import; reading too narrowly deletes one that was needed, so a local that
    happens to be called `cast` counts, and that is fine.
    """
    read = {node.id for node in walk(tree)
            if isinstance(node, Name) and isinstance(node.ctx, Load)}
    # A quoted annotation -- `self: 'Foo'` -- is a string, not a Name, so a
    # name used only there would look dead. Every identifier in every string
    # counts as read: it over-imports on a docstring that happens to say
    # `cast`, and it never deletes an import the module needs.
    for node in walk(tree):
        if isinstance(node, Constant) and isinstance(node.value, str):
            read |= set(findall(r'\b\w+\b', node.value))
    return read


def _already_imported(tree, module, name):
    return any(isinstance(stmt, ImportFrom) and stmt.module == module
               and any(a.name == name for a in stmt.names)
               for stmt in walk(tree))


class StaticImports(NodeTransformer):
    """Make the module's `from __static__ ...` and `from typing ...` say what is true.

    Adding the missing names is only half of it. The line the author wrote goes
    stale in the other direction too: erasure deletes the last `cbool` in a
    module and turns the annotation that wanted `Optional` into `Any`, so a
    name that was needed when the file was written need not be needed when we
    are done. This rewrites each list rather than appending to it.

    Only `from X import ...`. A bare `import __static__` is not an import of a
    name, it is what installs the static loader, and a module that loses it
    stops being a Static Python module at all -- so `Import` statements are
    never visited, and nothing here may be changed to visit them.
    """

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

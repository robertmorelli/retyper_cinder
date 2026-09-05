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


def analyze_import_usage(tree):
    """Return every bare name the transformed module may read."""
    read = {node.id for node in walk(tree)
            if isinstance(node, Name) and isinstance(node.ctx, Load)}
    # Include strings so names used only in quoted annotations remain imported.
    for node in walk(tree):
        if isinstance(node, Constant) and isinstance(node.value, str):
            read |= set(findall(r'\b\w+\b', node.value))
    return read


def required_imports(usage):
    requirements = {
        "__static__": tuple(name for name in STATIC_NAMES if name in usage),
        "typing": ("Any",) if "Any" in usage else (),
    }
    return {module: names for module, names in requirements.items() if names}


class ManagedImports(NodeTransformer):
    """Prune managed imports and add their required bare names."""

    MODULES = ("__static__", "typing")

    def __init__(self, usage, requirements):
        self.usage = usage
        self.requirements = requirements
        self.found = set()

    def visit_ImportFrom(self, node):
        if node.module not in self.MODULES:
            return node
        keep = [a for a in node.names if a.name in self.usage or a.asname]
        have = {a.name for a in keep if a.asname is None}
        keep += [
            alias(name=name)
            for name in self.requirements.get(node.module, ())
            if name not in have
        ]
        self.found.add(node.module)
        if not keep:
            return None
        node.names = keep
        return node


def synchronize_existing_imports(tree, usage, requirements):
    synchronizer = ManagedImports(usage, requirements)
    synchronizer.visit(tree)
    return synchronizer.found


def insert_missing_imports(tree, requirements, found_modules):
    for module, names in requirements.items():
        if module not in found_modules:
            tree.body.insert(
                _insert_point(tree.body),
                ImportFrom(
                    module=module,
                    names=[alias(name=name) for name in names],
                    level=0,
                ),
            )


def add_imports(tree):
    usage = analyze_import_usage(tree)
    requirements = required_imports(usage)
    found_modules = synchronize_existing_imports(tree, usage, requirements)
    insert_missing_imports(tree, requirements, found_modules)
    return tree

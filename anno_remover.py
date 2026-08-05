from ast import (Call, Dict, List, Load, Name, NodeTransformer, Set,
                 Subscript, copy_location)

CHECKED = {"CheckedList": List, "CheckedDict": Dict, "CheckedSet": Set}


def _checked_ctor(annotation, value):
    """`xs: CheckedList[int] = []` -> `xs: Any = CheckedList[int]()`.

    The annotation is what made the bare literal a checked container; erase it
    and the literal is a plain list, which changes the object at runtime, not
    just its static type. Naming the constructor keeps the object it built.
    """
    if not isinstance(annotation, Subscript) or not isinstance(annotation.value, Name):
        return None
    literal = CHECKED.get(annotation.value.id)
    if literal is None or not isinstance(value, literal):
        return None
    if getattr(value, "elts", None) or getattr(value, "keys", None):
        return None                       # only a bare literal builds nothing
    return copy_location(Call(func=annotation, args=[], keywords=[]), value)


class AnnoRemover(NodeTransformer):
    def __init__(self, targets, types=None, type_ctxs=None):
        self.targets = targets
        self.types = types
        self.type_ctxs = type_ctxs

    def _record(self, node, of):
        """The synthesized call carries the type the annotation used to supply."""
        for table in (self.types, self.type_ctxs):
            if table is not None and of in table:
                table[node] = table[of]

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
        if (ctor := _checked_ctor(node.annotation, node.value)) is not None:
            self._record(ctor, node.value)
            node.value = ctor
        node.annotation = Name(id='Any', ctx=Load())
        return node


def remove_annotations(tree, node_set, types=None, type_ctxs=None):
    AnnoRemover(node_set, types, type_ctxs).visit(tree)
    return tree

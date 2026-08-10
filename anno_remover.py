from ast import (Call, Dict, List, ListComp, Load, Name, NodeTransformer, Set,
                 Subscript, copy_location)

CHECKED = {"CheckedList": (List, ListComp), "CheckedDict": Dict, "CheckedSet": Set}


def _checked_ctor(annotation, value):
    """Preserve the checked container an annotation built from a literal.

    Without the explicit constructor, erasure turns the value into an ordinary
    list, dict, or set and changes both its static type and runtime behavior.
    """
    if not isinstance(annotation, Subscript) or not isinstance(annotation.value, Name):
        return None
    literal = CHECKED.get(annotation.value.id)
    if literal is None or not isinstance(value, literal):
        return None
    return copy_location(Call(func=annotation, args=[value], keywords=[]), value)


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

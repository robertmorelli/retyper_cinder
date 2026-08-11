"""Erase the annotations a mask selects, in one of two spellings.

The default writes `Any`, which is what a Static Python slot has to say to mean
"no static type at all". `less_any` instead deletes the annotation wherever the
grammar allows, which is what a person would have written -- but it is a
different program, not a tidier one: cinderx infers a type for an unannotated
binding, so `i = 0` is a static int where `i: Any = 0` is dynamic. Which one
the erasure of a benchmark should mean is the question the flag exists to ask.

A bare `i: int64` with no value has no less-any spelling. Dropping the
annotation leaves `i`, an expression statement that declares nothing, so those
fall back to `Any` in both modes.
"""
from ast import (Assign, AsyncFunctionDef, Call, Dict, FunctionDef, List,
                 ListComp, Load, Name, NodeTransformer, Set, Subscript,
                 copy_location)

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
    def __init__(self, targets, types=None, type_ctxs=None, less_any=False):
        self.targets = targets
        self.types = types
        self.type_ctxs = type_ctxs
        self.less_any = less_any
        self.receivers = set()

    def _record(self, node, of):
        """The synthesized call carries the type the annotation used to supply."""
        for table in (self.types, self.type_ctxs):
            if table is not None and of in table:
                table[node] = table[of]

    def _erased(self):
        """What an erased annotation is spelled as, or None to delete it."""
        return None if self.less_any else Name(id='Any', ctx=Load())

    def _pins(self, value):
        """Would dropping the annotation pin the binding to `None`?

        A module-level binding takes its type from the assignment that declares
        it and nothing widens it afterwards -- there is no back edge from the
        later assignments, the way there is for a local or an attribute. So
        `PtrGlb: Record | None = None` erased to `PtrGlb = None` is a global of
        type None, and the `PtrGlb = Record()` that follows will not compile.
        Keeping `Any` there costs one annotation and saves the module.
        """
        if self.types is None:
            return False
        produced = self.types.get(value)
        return (produced is not None
                and produced.klass.type_name.readable_name == "None")

    def visit_ClassDef(self, node):
        """Note the receivers before descending, so `visit_arg` can spot them."""
        for statement in node.body:
            if isinstance(statement, (FunctionDef, AsyncFunctionDef)):
                args = statement.args.posonlyargs + statement.args.args
                if args:
                    self.receivers.add(args[0])
        self.generic_visit(node)
        return node

    def visit_arg(self, node):
        if node not in self.targets: return node
        if node in self.receivers:
            # A receiver is the one parameter that omitting does not make
            # dynamic -- leave it bare and it is the class, which turns every
            # `self.m(...)` back into a checked call and every erased argument
            # into a type error. `Any` is what says otherwise, so it stays in
            # both modes.
            node.annotation = Name(id='Any', ctx=Load())
        else:
            node.annotation = self._erased()
        return node

    def visit_FunctionDef(self, node):
        self.generic_visit(node)
        if node not in self.targets: return node
        node.returns = self._erased()
        return node

    def visit_AnnAssign(self, node):
        self.generic_visit(node)
        if node not in self.targets: return node
        if (ctor := _checked_ctor(node.annotation, node.value)) is not None:
            self._record(ctor, node.value)
            node.value = ctor
        if self.less_any and node.value is not None and not self._pins(node.value):
            # `x: T = v` has a less-any spelling, `x = v`. A bare `x: T` does
            # not -- `x` alone binds nothing -- so it keeps the annotation.
            return copy_location(
                Assign(targets=[node.target], value=node.value), node)
        node.annotation = Name(id='Any', ctx=Load())
        return node


def remove_annotations(tree, node_set, types=None, type_ctxs=None, less_any=False):
    AnnoRemover(node_set, types, type_ctxs, less_any).visit(tree)
    return tree

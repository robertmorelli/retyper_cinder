"""
Collect parent-defined expression roles before mediation

This collects info on the following:
- tests: the expressions at the top of if and while statements. like `e` in `if e:` or `while e:`
- indices: like `i` in `a[i]`
- inline args: suppose the definition of some function `f` were marked with `@inline` then it would be like `e` in `f(e)`
- comprehension payloads: like `e + 1` in `[e + 1 for e in a]`
"""

from ast import NodeVisitor, Slice

from .type_rules import exact_inline_args


class StructuralInfo(NodeVisitor):
    def __init__(self, types, inline_calls):
        self.types = types
        self.inline_calls = inline_calls
        self.tests = set()
        self.indices = set()
        self.inline_args = set()
        self.comprehension_payloads = set()

    @classmethod
    def collect(cls, tree, types, inline_calls):
        info = cls(types, inline_calls)
        info.visit(tree)
        return info

    def visit_test(self, node):
        self.tests.add(node.test)
        self.generic_visit(node)

    visit_If = visit_test
    visit_IfExp = visit_test
    visit_While = visit_test

    def visit_Subscript(self, node):
        if isinstance(node.slice, Slice):
            self.indices.update(
                part
                for part in (node.slice.lower, node.slice.upper, node.slice.step)
                if part is not None
            )
        else:
            self.indices.add(node.slice)
        self.generic_visit(node)

    def visit_Call(self, node):
        if node in self.inline_calls:
            self.inline_args.update(exact_inline_args(node, self.types))
        self.generic_visit(node)

    def _visit_comprehension_expression(self, node):
        self.comprehension_payloads.add(node.elt)
        self.generic_visit(node)

    visit_ListComp = _visit_comprehension_expression
    visit_SetComp = _visit_comprehension_expression
    visit_GeneratorExp = _visit_comprehension_expression

    def visit_DictComp(self, node):
        self.comprehension_payloads.update((node.key, node.value))
        self.generic_visit(node)

    def is_test(self, node):
        return node in self.tests

    def is_index(self, node):
        return node in self.indices

    def is_inline_arg(self, node):
        return node in self.inline_args

    def is_comprehension_payload(self, node):
        return node in self.comprehension_payloads

"""Walk the tree bottom-up and mediate each expression position."""
from ast import (Call, DictComp, FunctionDef, GeneratorExp, If, IfExp,
                 ListComp, NodeTransformer, SetComp, Slice, Subscript, While,
                 unparse, walk)

from .patch_picker import mediate


def is_inline_call(call, reverse_outflow):
    """Whether a call resolves to an `@inline` function."""
    source = reverse_outflow.get(call)
    return (isinstance(source, FunctionDef)
            and "inline" in {unparse(d) for d in source.decorator_list})


def inline_args(call, types):
    """Inline-call arguments that require exact mediation."""
    return [argument for argument in call.args
            if (value := types.get(argument)) is None
            or value.klass.type_name.readable_name != "int"]


def _payloads(tree):
    """Collect comprehension payloads before rewriting them."""
    found = set()
    for node in walk(tree):
        if isinstance(node, (ListComp, SetComp, GeneratorExp)):
            found.add(node.elt)
        elif isinstance(node, DictComp):
            found.update((node.key, node.value))
    return found


class Coercer(NodeTransformer):
    """Walk bottom-up and pass parent-known position flags to `mediate`."""

    def __init__(self, types, type_ctxs, dyn, valid_pair, reverse_outflow,
                 graph, payloads):
        self.tables = (types, type_ctxs, dyn, valid_pair, graph, payloads)
        self.types, self.reverse_outflow = types, reverse_outflow
        self.tests, self.indices, self.inlined = set(), set(), set()

    def visit(self, node):
        if isinstance(node, (If, IfExp, While)):
            self.tests.add(node.test)
        elif isinstance(node, Subscript):
            if isinstance(node.slice, Slice):
                self.indices.update(part for part in (
                    node.slice.lower, node.slice.upper, node.slice.step)
                                    if part is not None)
            else:
                self.indices.add(node.slice)
        elif isinstance(node, Call) and is_inline_call(node,
                                                       self.reverse_outflow):
            self.inlined.update(inline_args(node, self.types))
        self.generic_visit(node)
        return mediate(node, *self.tables, is_test=node in self.tests,
                       is_index=node in self.indices,
                       is_inline_arg=node in self.inlined)


def coerce_tree(tree, types, type_ctxs, dyn, valid_pair, reverse_outflow,
                graph):
    Coercer(types, type_ctxs, dyn, valid_pair, reverse_outflow, graph,
            _payloads(tree)).visit(tree)
    return tree

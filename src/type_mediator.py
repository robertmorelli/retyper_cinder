"""Walk the tree bottom-up and mediate each expression position."""
from ast import (Call, DictComp, GeneratorExp, If, IfExp, ListComp,
                 NodeTransformer, SetComp, Slice, Subscript, While, walk)
from dataclasses import replace

from .patch_picker import MediationRequest, mediate


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

    def __init__(self, types, type_ctxs, dyn, valid_pair, inline_calls,
                 graph, payloads):
        self.request = MediationRequest(
            node=None,
            types=types,
            type_contexts=type_ctxs,
            dynamic=dyn,
            valid_pair=valid_pair,
            graph=graph,
            payloads=payloads,
        )
        self.types, self.inline_calls = types, inline_calls
        self.tests, self.indices, self.inlined = set(), {}, set()
        self.prepared = set()

    def _register(self, node):
        """Record position facts before planning or descending into a node."""
        if isinstance(node, (If, IfExp, While)):
            self.tests.add(node.test)
        elif isinstance(node, Subscript):
            if isinstance(node.slice, Slice):
                for part in (node.slice.lower, node.slice.upper,
                             node.slice.step):
                    if part is not None:
                        self.indices[part] = node.value
            else:
                self.indices[node.slice] = node.value
        elif isinstance(node, Call) and node in self.inline_calls:
            self.inlined.update(inline_args(node, self.types))

    def _request(self, node):
        self._register(node)
        return replace(
            self.request,
            node=node,
            is_test=node in self.tests,
            is_index=node in self.indices,
            is_inline_arg=node in self.inlined,
            index_container=self.indices.get(node),
        )

    def _prepare(self, node):
        """Mediate context-independent descendants exactly once."""
        if node not in self.prepared:
            self._register(node)
            self.generic_visit(node)
            self.prepared.add(node)

    def visit(self, node):
        self._register(node)
        return mediate(self._request(node), self._request, self._prepare)


def coerce_tree(tree, types, type_ctxs, dyn, valid_pair, inline_calls,
                graph):
    Coercer(types, type_ctxs, dyn, valid_pair, inline_calls, graph,
            _payloads(tree)).visit(tree)
    return tree

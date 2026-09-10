"""Walk the AST and recursively mediate its expression positions."""

from ast import Load, NodeTransformer, Slice
from dataclasses import replace

from utilities.ast_nodes import ast_position

from .mediation_request import EditRequest, MediationRequest
from .structural_info import StructuralInfo


def can_mediate(node, request):
    position = ast_position(node)
    return (
        request.types.get(node) is not None
        and request.type_constraints.get(node) is not None
        and not isinstance(node, Slice)
        and (position is None or isinstance(position, Load))
    )


class Mediator(NodeTransformer):
    """Walk and recursively select edits for every expression position."""

    def __init__(self, request, structure):
        self.request = request
        self.structure = structure
        self.prepared = set()

    def _request_for(self, node):
        if can_mediate(node, self.request):
            return replace(
                self.request,
                node=node,
                is_test=self.structure.is_test(node),
                is_index=self.structure.is_index(node),
                is_inline_arg=self.structure.is_inline_arg(node),
                is_comprehension_payload=(
                    self.structure.is_comprehension_payload(node)
                ),
            )

    def _prepare(self, node):
        """Walk an expression's children once before mediation resolves it."""
        if node not in self.prepared:
            self.generic_visit(node)
            self.prepared.add(node)

    def visit(self, node):
        if request := self._request_for(node):
            return EditRequest.plan(request, self).run()
        return self.generic_visit(node)


def mediate_tree(tree, types, type_constraints, dyn, valid_pair, inline_calls,
                graph):
    structure = StructuralInfo.collect(tree, types, inline_calls)
    request = MediationRequest(
        node=None,
        types=types,
        type_constraints=type_constraints,
        dynamic=dyn,
        valid_pair=valid_pair,
        graph=graph,
    )
    Mediator(request, structure).visit(tree)
    return tree

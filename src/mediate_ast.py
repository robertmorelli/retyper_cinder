"""Walk the AST and recursively mediate its expression positions."""

from ast import Load, NodeTransformer, Slice
from dataclasses import replace

from utilities.ast_nodes import ast_position, operands

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

    def _mediate_candidate(self, mediation, candidate, outer_constraint):
        """Mediate all operands and then mediate the result for a given candidate"""
        return EditRequest.resolve(
            mediation,
            inner=EditRequest.resolve_multiop(
                mediation,
                candidate,
                tuple(
                    self._mediate(
                        self._request_for(operand),
                        type_constraint=constraint,
                    )
                    for operand, constraint in candidate
                ),
                outer_constraint,
            ),
            type_constraint=outer_constraint,
        )

    def _mediate_multiop(self, mediation, outer_constraint=None):
        """Return the cheapest complete edit among plausible mediations."""
        if not (expr_operands := operands(mediation.node)):
            return None
        candidates = tuple(dict.fromkeys(mediation.generate_candidates(expr_operands)))
        candidate_edits = [
            self._mediate_candidate(
                mediation,
                candidate,
                outer_constraint or mediation.type_constraints[mediation.node]
            )
            for candidate in candidates
        ]
        return min(candidate_edits, key=lambda edit: edit.cost)

    def _mediate(self, mediation, type_constraint=None):
        """Compose a deferred edit for one raw expression position."""
        if edit := self._mediate_multiop(mediation, type_constraint):
            return edit
        if mediation.node not in self.prepared:
            self.generic_visit(mediation.node)
            self.prepared.add(mediation.node)
        return EditRequest.resolve(
            mediation,
            type_constraint=type_constraint,
        )

    def visit(self, node):
        if request := self._request_for(node):
            return self._mediate(request).run()
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

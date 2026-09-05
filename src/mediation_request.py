"""Requests that carry and resolve mediation state."""

from ast import BoolOp, Slice, Subscript
from dataclasses import dataclass, replace

from utilities.iterables import first

from .edits import (
    BoxEdit,
    CastEdit,
    ConstrEdit,
    DiscardIndexNarrowing,
    DiscardTestConversion,
    LenEdit,
    MultiOpEdit,
    NoEdit,
)
from .type_rules import (
    PRIMITIVE_NAMES,
    is_optional_of,
    is_primitive,
    readable_type_name,
)
from .unop_spine import UnopSpine

CONVERTIBLE = ("float", "int", "bool")
CHECKED_NAMES = ("chklist", "CheckedList", "chkdict", "CheckedDict")


def has_typed_reader(mediation, node):
    """Whether a downstream consumer still reads a typed representation."""
    type_cell = mediation.graph.cell(node, "type")
    outgoing = mediation.graph.outgoing().get(type_cell, ())
    return any(
        mediation.types.get(reader) is not mediation.dynamic
        for reader, slot in outgoing
        if slot == "type"
    )


@dataclass(frozen=True)
class MediationRequest:
    node: object
    types: dict
    type_constraints: dict
    dynamic: object
    valid_pair: object
    graph: object
    is_test: bool = False
    is_index: bool = False
    is_inline_arg: bool = False
    is_comprehension_payload: bool = False

    def candidate_is_uniform(self, candidate):
        candidate_type = candidate[0][1]
        return all(
            type_constraint is candidate_type
            for _, type_constraint in candidate[1:]
        )

    def multiop_result_type(self, candidate):
        """Return the result type induced by a multi-operator candidate."""
        candidate_type = candidate[0][1]
        if not self.candidate_is_uniform(candidate):
            return self.types[self.node]
        if candidate_type is self.dynamic:
            return self.dynamic
        if isinstance(self.node, BoolOp):
            return candidate_type
        return (
            self.graph.original_types[self.node]
            if self.node in self.graph.original_types
            else self.types[self.node]
        )

    def generate_candidates(self, expr_operands):
        """Generate plausible operand representations for a multi-operator."""
        expr_operand_types = (self.types[operand] for operand in expr_operands)
        original_candidate = tuple(
            (operand, self.type_constraints[operand])
            for operand in expr_operands
        )
        original_is_uniform = self.candidate_is_uniform(original_candidate)

        yield original_candidate
        for candidate_type in (
            *expr_operand_types,
            *(type_constraint for _, type_constraint in original_candidate),
        ):
            if is_primitive(candidate_type) and (
                not original_is_uniform
                or candidate_type is not original_candidate[0][1]
            ):
                yield tuple(
                    (operand, candidate_type)
                    for operand in expr_operands
                )
        if (
            not original_is_uniform
            or original_candidate[0][1] is not self.dynamic
        ):
            yield tuple(
                (operand, self.dynamic)
                for operand in expr_operands
            )


@dataclass(frozen=True)
class EditRequest:
    mediation: MediationRequest
    spine: object
    type: object
    type_constraint: object
    produced: object = None
    has_typed_reader: bool = False
    sits_on_len: bool = False
    asked_for: str | None = None
    inner: object = None

    @staticmethod
    def resolve(mediation, inner=None, type_constraint=None):
        node = mediation.node
        spine = UnopSpine.analyze(
            inner.node if inner is not None else node,
            mediation.types,
            mediation.dynamic.klass.type_env,
            remove_coercions=inner is None,
        )
        type_constraint = type_constraint or mediation.type_constraints.get(node)
        request = EditRequest(
            mediation=mediation,
            spine=spine,
            type=inner.type if inner is not None else spine.type_of(spine.top),
            type_constraint=type_constraint,
            produced=(
                mediation.types.get(node) if spine.coercion_removed else None
            ),
            has_typed_reader=has_typed_reader(mediation, node),
            sits_on_len=spine.sits_on_len,
            asked_for=spine.removed_call_name,
            inner=inner,
        )
        edit = request._mediate_unop()
        if mediation.is_test:
            edit = DiscardTestConversion(edit)
        elif mediation.is_index:
            if (
                isinstance(edit, ConstrEdit)
                and readable_type_name(edit.type) == "int64"
            ):
                edit = edit.inner or NoEdit(edit.request)
            edit = DiscardIndexNarrowing(edit)
        for derived, value_type in spine.derived_types.items():
            edit.bind(derived, value_type, value_type)
        edit.bind(edit.node, edit.type, type_constraint)
        return edit

    @staticmethod
    def resolve_multiop(
        mediation,
        candidate,
        operand_edits,
        type_constraint,
    ):
        request = EditRequest(
            mediation=mediation,
            spine=UnopSpine.zero_depth(mediation.node, mediation.types),
            type=mediation.multiop_result_type(candidate),
            type_constraint=type_constraint,
        )
        return MultiOpEdit(request, operand_edits)

    @property
    def dynamic(self):
        return self.mediation.dynamic

    @property
    def node(self):
        return self.inner.node if self.inner is not None else self.spine.top

    @property
    def operand(self):
        return self.spine.operand

    @property
    def is_zero_depth(self):
        return len(self.spine.nodes) == 1

    def with_operand(self, operand):
        return self.spine.with_operand(operand)

    @property
    def inline_arg(self):
        return self.mediation.is_inline_arg

    @property
    def is_comprehension_payload(self):
        return self.mediation.is_comprehension_payload

    @property
    def is_checked_slice(self):
        return (
            isinstance(self.node, Subscript)
            and isinstance(self.node.slice, Slice)
            and readable_type_name(self.type_constraint).startswith(
                CHECKED_NAMES
            )
        )

    @property
    def typed_payload_constraint(self):
        if (
            self.is_comprehension_payload
            and self.type is self.dynamic
            and self.type_constraint is not self.dynamic
        ):
            return self.type_constraint

    @property
    def nothing_needed(self):
        return self.type == self.type_constraint or (
            not self.inline_arg
            and self.mediation.valid_pair(
                self.type,
                self.type_constraint,
                self.node,
            )
        )

    def inside_len(self, primitive):
        result_type = (
            self.dynamic.klass.type_env.int64.instance
            if primitive
            else self.dynamic
        )
        return replace(
            self,
            type=result_type,
            sits_on_len=False,
            asked_for=None,
        )

    @property
    def should_use_primitive_len(self):
        value_type = self.mediation.types.get(self.node.args[0])
        return (
            value_type is not None
            and value_type is not self.dynamic
            and value_type.get_fast_len_type() is not None
            and (
                self.mediation.is_test
                or self.asked_for in PRIMITIVE_NAMES
            )
        )

    def _still_required(self):
        """Return the representation a removed coercion must restore."""
        if (
            self.produced is None
            or self.produced is self.type
            or self.produced is self.dynamic
        ):
            return None
        if is_primitive(self.produced) and self.type_constraint is self.dynamic:
            return None
        if is_optional_of(self.type, self.produced):
            return self.produced
        return self.produced if self.has_typed_reader else None

    def _mediate_len(self):
        if not self.sits_on_len:
            return None
        primitive = self.should_use_primitive_len
        inner = self.inside_len(primitive)._mediate_unop()
        return LenEdit(inner, primitive)

    def _restore_removed_representation(self):
        required = self._still_required()
        if required is None:
            return None
        if is_primitive(self.type) and not is_primitive(required):
            return BoxEdit(self, exact=self.inline_arg)
        if is_primitive(required) or readable_type_name(required) in CONVERTIBLE:
            return ConstrEdit(self, required)
        return CastEdit.create(self, required)

    def _repair_typed_payload(self):
        if type_constraint := self.typed_payload_constraint:
            return CastEdit.create(self, type_constraint)

    def _restore_optional_narrowing(self):
        if is_optional_of(self.type, self.type_constraint):
            return CastEdit.create(self, self.type_constraint)

    def _box_for_dynamic_constraint(self):
        if self.type_constraint is self.dynamic and is_primitive(self.type):
            return BoxEdit.create(self, implicit_type=self.dynamic)

    def _rebuild_checked_slice(self):
        if self.is_checked_slice:
            return ConstrEdit(self, self.type_constraint)

    def _no_edit_required(self):
        if self.nothing_needed:
            return NoEdit(self)

    def _satisfy_constraint(self):
        if is_primitive(self.type):
            return BoxEdit.create(self)
        if is_primitive(self.type_constraint):
            return ConstrEdit(self, self.type_constraint)
        return CastEdit.create(self, self.type_constraint)

    def _mediate_unop(self):
        """Resolve one ordinary or zero-depth unary mediation request."""
        return first(
            rule()
            for rule in (
                self._mediate_len,
                self._restore_removed_representation,
                self._repair_typed_payload,
                self._restore_optional_narrowing,
                self._box_for_dynamic_constraint,
                self._rebuild_checked_slice,
                self._no_edit_required,
                self._satisfy_constraint,
            )
        )

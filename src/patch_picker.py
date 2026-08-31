from ast import BinOp, BoolOp, Call, Compare, Load, Name, NodeTransformer, Not
from ast import Slice, Subscript, UnaryOp, copy_location
from copy import copy
from dataclasses import dataclass, replace

from .cinderx_binding import get_ctx, is_primative, is_const
from .edits import (BoxEdit, CastEdit, ClenEdit, ConstrEdit,
                    DiscardIndexNarrowing, DiscardTestConversion, LenEdit,
                    MultiOpEdit, NoEdit, is_option_of, operands,
                    readable_name)


def simple_call_name(node):
    """Return the name of a unary bare-name call, or None."""
    if (isinstance(node, Call) and isinstance(node.func, Name)
            and len(node.args) == 1):
        return node.func.id
    return None


def is_simple_call(names, node):
    """Whether `node` is the specified tower of unary bare-name calls."""
    for name in names:
        if simple_call_name(node) != name:
            return False
        node = node.args[0]
    return True


# Only these primitive types have writable constructor names.
PRIMITIVE_NAMES = {"double", "cbool", "int8", "int16", "int32", "int64",
                   "uint8", "uint16", "uint32", "uint64"}

# These boxed scalars convert values; cast only asserts a type.
CONVERTIBLE = ('float', 'int', 'bool')
# containers whose slice comes back as a plain list
CHECKED_NAMES = ('chklist', 'CheckedList', 'chkdict', 'CheckedDict')
def _still_required(produced, held, type_ctx, readers, dyn):
    """Return the type that must be restored after stripping a coercion tower.

    A missing reader type denotes a consumer not yet reached in postorder.
    """
    if produced is None or produced is held or produced is dyn:
        return None
    if is_primative(produced) and type_ctx is dyn:
        return None
    if is_option_of(held, produced):
        return produced
    return produced if any(read is not dyn for read in readers) else None


def _primitive_len(call, asked_for, is_test, types, dyn):
    measured = types.get(call.args[0])
    return (measured is not None and measured is not dyn
            and measured.get_fast_len_type() is not None
            and (is_test or asked_for in PRIMITIVE_NAMES))


@dataclass(frozen=True)
class MediationRequest:
    node: object
    types: dict
    type_contexts: dict
    dynamic: object
    valid_pair: object
    graph: object
    payloads: set
    is_test: bool = False
    is_index: bool = False
    is_inline_arg: bool = False
    index_container: object = None


@dataclass(frozen=True)
class EditRequest:
    mediation: MediationRequest
    tower: tuple
    type: object
    type_ctx: object
    produced: object = None
    readers: tuple = ()
    sits_on_len: bool = False
    asked_for: str | None = None
    inner: object = None

    @property
    def dynamic(self):
        return self.mediation.dynamic

    @property
    def node(self):
        return self.inner.node if self.inner is not None else self.tower[0]

    @property
    def operand(self):
        return self.tower[-1]

    @property
    def inline_arg(self):
        return self.mediation.is_inline_arg

    @property
    def payload(self):
        return self.mediation.node in self.mediation.payloads

def _keep_without_type_pair(request):
    if not request.type or not request.type_ctx:
        return NoEdit(request)
    return None


def _choose_len(request):
    if not request.sits_on_len:
        return None
    primitive = _primitive_len(request.node, request.asked_for,
                               request.mediation.is_test,
                               request.mediation.types,
                               request.dynamic)
    produced = (request.dynamic.klass.type_env.int64.instance
                if primitive else request.dynamic)
    inner = _choose_core(replace(request, type=produced, sits_on_len=False,
                                 asked_for=None))
    return (ClenEdit(inner, request.dynamic)
            if primitive
            else LenEdit(inner, request.dynamic))


def _restore_stripped_representation(request):
    required = _still_required(request.produced, request.type,
                               request.type_ctx, request.readers,
                               request.dynamic)
    if required is not None:
        # Restore a required representation after stripping the original tower.
        if is_primative(request.type) and not is_primative(required):
            return BoxEdit(request, request.inline_arg)
        if is_primative(required) or readable_name(required) in CONVERTIBLE:
            return ConstrEdit(request, required)
        return CastEdit(request, required)
    return None


def _repair_typed_payload(request):
    if (request.payload and request.type is request.dynamic
            and request.type_ctx is not request.dynamic):
        # A dynamic comprehension payload cannot satisfy a typed container.
        return CastEdit(request, request.type_ctx)
    return None


def _restore_optional_narrowing(request):
    if is_option_of(request.type, request.type_ctx):
        # the cast is what narrows: drop it and the None reaches the use site.
        return CastEdit(request, request.type_ctx)
    return None


def _box_for_dynamic_context(request):
    if (request.dynamic is not None and request.type_ctx is request.dynamic
            and is_primative(request.type)):
        # Literals box implicitly; other machine values require an edit.
        return (NoEdit(request, request.dynamic) if is_const(request.node)
                else BoxEdit(request, request.inline_arg))
    return None


def _rebuild_checked_slice(request):
    if (request.node.__class__ is Subscript
            and request.node.slice.__class__ is Slice
            and readable_name(request.type_ctx).startswith(CHECKED_NAMES)):
        # Checked-container slices return lists; reconstruct before valid_pair.
        return ConstrEdit(request, request.type_ctx)
    return None


def _satisfy_context(request):
    if (not request.inline_arg
            and request.mediation.valid_pair(
                request.type, request.type_ctx, request.node)):
        return NoEdit(request)
    if request.type == request.type_ctx:
        return NoEdit(request)
    if is_primative(request.type):
        return (NoEdit(request) if is_const(request.node)
                else BoxEdit(request))
    if is_primative(request.type_ctx):
        return ConstrEdit(request, request.type_ctx)
    # Inline substitution requires disagreeing arguments to meet as dynamic.
    return CastEdit(request, request.type_ctx)


def _choose_core(request):
    if edit := _keep_without_type_pair(request):
        return edit
    if edit := _choose_len(request):
        return edit
    if edit := _restore_stripped_representation(request):
        return edit
    if edit := _repair_typed_payload(request):
        return edit
    if edit := _restore_optional_narrowing(request):
        return edit
    if edit := _box_for_dynamic_context(request):
        return edit
    if edit := _rebuild_checked_slice(request):
        return edit
    return _satisfy_context(request)


def _unop_choose(request):
    """Choose the lazy edit for one expression position."""
    edit = _choose_core(request)
    if request.mediation.is_test:
        return DiscardTestConversion(edit)
    if request.mediation.is_index:
        return DiscardIndexNarrowing(edit)
    return edit

# Single-argument conversions Tower may peel; cast is handled separately.
UNWRAPPABLE = {"box"} | PRIMITIVE_NAMES


class Tower(NodeTransformer):
    """Strip coercion calls without descending into unrelated positions."""

    def __init__(self, types=None, env=None):
        self.types, self.env, self.derived = types, env, {}

    def type_of(self, node):
        return self.derived.get(node, self.types.get(node))

    def peel(self, node):
        """What a single coercion holds, or None if it is not a coercion."""
        if not (isinstance(node, Call) and isinstance(node.func, Name)):
            return None
        if node.func.id == "cast" and len(node.args) == 2:
            return node.args[1]
        if simple_call_name(node) in UNWRAPPABLE:
            return node.args[0]
        return None

    def visit(self, node):
        if inner := self.peel(node):
            return self.visit(inner)
        return super().visit(node)

    def ends(self, node):
        """Return the stripped unary spine and whether its top is len."""
        top = self.visit(node)
        tower = [top]
        while isinstance(tower[-1], UnaryOp):
            tower.append(tower[-1].operand)
        sits_on_len = simple_call_name(top) in ("clen", "len")
        return tuple(tower), sits_on_len

    def visit_UnaryOp(self, node):
        """Preserve unary operators; identity signals that nothing was stripped."""
        operand = self.visit(node.operand)
        if operand is node.operand:
            return node
        result = copy_location(UnaryOp(op=node.op, operand=operand), node)
        produced = self.type_of(operand)
        if isinstance(node.op, Not):
            self.derived[result] = (self.env.cbool.instance
                                    if produced is not None
                                    and is_primative(produced)
                                    else self.env.bool.instance)
        elif produced is not None:
            self.derived[result] = produced
        return result

    def generic_visit(self, node):
        return node


def plan_mediation(mediation, inner=None, type_ctx=None,
                   request_for=None, prepare=None):
    """Choose an edit without changing the AST or binding tables."""
    node = mediation.node
    if inner is None and request_for is not None:
        if operands(node):
            return multiop_choose(mediation, request_for, prepare, type_ctx)
        prepare(node)
    types = mediation.types
    dyn = mediation.dynamic
    ctx = get_ctx(node)
    if isinstance(node, Slice) or (ctx is not None
                                   and not isinstance(ctx, Load)):
        # Stores, deletions, and slice objects are not value positions.
        request = EditRequest(mediation, (node,), types.get(node),
                              type_ctx if type_ctx is not None
                              else mediation.type_contexts.get(node),
                              inner=inner)
        return NoEdit(request)

    stripped = Tower(types, dyn.klass.type_env)
    if inner is None:
        tower, sits_on_len = stripped.ends(node)
        target = tower[0]
        t = stripped.type_of(target)
        produced = None if target is node else types.get(node)
        asked_for = (node.func.id if target is not node
                     and isinstance(node, Call) and isinstance(node.func, Name)
                     else None)
    else:
        tower, target = (inner.node,), inner.node
        sits_on_len = simple_call_name(target) in ("clen", "len")
        t, produced, asked_for = inner.type, None, None
    # Tower records the stripped type, including the effect of unary operators.
    tc = (mediation.type_contexts.get(node)
          if type_ctx is None else type_ctx)
    request = EditRequest(
        mediation=mediation,
        tower=tower,
        type=t,
        type_ctx=tc,
        produced=produced,
        readers=tuple(
            types.get(where) for where, slot in
            mediation.graph.outgoing().get(
                mediation.graph.cell(node, "type"), ())
            if slot == "type"),
        sits_on_len=sits_on_len,
        asked_for=asked_for,
        inner=inner,
    )
    edit = _unop_choose(request)
    for derived, type in stripped.derived.items():
        edit.bind(derived, type, type)
    edit.bind(edit.node, edit.type, tc)
    return edit


def _common(values):
    first = values[0] if values else None
    return (first if first is not None
            and all(value is first for value in values) else None)


def _original_multiop_candidate(mediation):
    return tuple(mediation.type_contexts.get(operand)
                 for operand in operands(mediation.node))


def candidate_compatible(mediation, candidate, operand_plans):
    """Whether planned operands can inhabit a candidate representation."""
    context = _common(candidate)
    if context is None or candidate == _original_multiop_candidate(mediation):
        return True
    return all(
        plan.type is not None
        and mediation.valid_pair(plan.type, context, plan.node)
        for plan in operand_plans)


def own_type_from_candidate(mediation, candidate):
    """Derive the multi-operator's type from an operand representation."""
    node = mediation.node
    context = _common(candidate)
    if context is None:
        own_type = mediation.types.get(node)
    elif context is mediation.dynamic:
        own_type = mediation.dynamic
    elif isinstance(node, BoolOp):
        own_type = context
    else:
        own_type = mediation.graph.types.get(node, mediation.types.get(node))
    if (own_type is None
            and candidate == _original_multiop_candidate(mediation)):
        return mediation.dynamic
    return own_type


def plan_multiop_candidate(
        mediation, candidate, outer_context, request_for, prepare):
    node = mediation.node
    operand_plans = tuple(
        plan_mediation(request_for(operand), type_ctx=context,
                       request_for=request_for, prepare=prepare)
        for operand, context in zip(operands(node), candidate))
    if not candidate_compatible(mediation, candidate, operand_plans):
        return None
    produced = own_type_from_candidate(mediation, candidate)
    if produced is None:
        return None
    seed = EditRequest(mediation, (node,), produced, outer_context)
    operation = MultiOpEdit(seed, node, operand_plans, produced)
    return plan_mediation(mediation, operation, outer_context)


def all_multiop_candidates(mediation, node):
    """Enumerate distinct operand representations worth backtracking over."""
    values = operands(node)
    original = _original_multiop_candidate(mediation)
    yield original

    candidate_types = {
        mediation.types.get(value) for value in values
    } | set(original)
    candidate_types = {
        candidate_type for candidate_type in candidate_types
        if candidate_type is not None and is_primative(candidate_type)
    }
    candidate_types.discard(_common(original))
    for candidate_type in candidate_types:
        yield (candidate_type,) * len(values)

    if _common(original) is not mediation.dynamic:
        yield (mediation.dynamic,) * len(values)


def multiop_choose(mediation, request_for, prepare, outer_context=None):
    """Backtrack over shared representations and return the cheapest plan."""
    node = mediation.node
    if outer_context is None:
        outer_context = mediation.type_contexts.get(node)
    plans = (plan_multiop_candidate(
                 mediation, candidate, outer_context, request_for, prepare)
             for candidate in all_multiop_candidates(mediation, node))
    return min((plan for plan in plans if plan is not None),
               key=lambda edit: edit.cost)


def mediate(mediation, request_for, prepare):
    """Choose and materialize the edit for one expression position."""
    return plan_mediation(
        mediation, request_for=request_for, prepare=prepare).run()

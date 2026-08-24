from ast import If, IfExp, Name, Load, Attribute, Call, copy_location, walk
from ast import NodeTransformer, Not, Slice, Subscript, UnaryOp
from ast import While, unparse
from dataclasses import dataclass, replace

from .cinderx_binding import get_ctx, is_primative, is_const

def _type_expr(name):
    parts = name.split(".")
    node = Name(id=parts[0], ctx=Load())
    for attr in parts[1:]:
        node = Attribute(value=node, attr=attr, ctx=Load())
    return node


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


# cbool is a CIntType, but its boxed representation is bool.
def boxed_instance(t):
    env = t.klass.type_env
    return env.bool.instance if t.klass is env.cbool else t.klass.boxed.instance

# Only these primitive types have writable constructor names.
PRIMITIVE_NAMES = {"double", "cbool", "int8", "int16", "int32", "int64",
                   "uint8", "uint16", "uint32", "uint64"}

def readable_name(t):
    if (inner := option_of(t)) is not None:
        # cinder rejects `Optional[X]`, accepts `X | None`
        return f"{readable_name(inner)} | None"
    name: str = t.klass.type_name.readable_name
    for k,v in {"chklist": "CheckedList", "chkdict": "CheckedDict", "chkset": "CheckedSet"}.items():
        name = name.replace(k, v)
    return name


def option_of(t):
    """Return CinderX's canonical Optional member type, if present."""
    inner = getattr(t.klass, "opt_type", None) if t is not None else None
    return None if inner is None else inner.instance


def is_option_of(maybe, inner):
    """Is `maybe` exactly `inner`, or None?"""
    assert maybe is not None and inner is not None
    return (held := option_of(maybe)) is not None and held.klass is inner.klass

class Edit:
    T = None

    def __init__(self, request):
        self.request = request
        self.node = self.next_root = request.node

    def run(self):
        return self.next_root

class NoEdit(Edit):
    """An explicit decision to leave this expression unchanged."""

class BoxEdit(Edit):
    def __init__(self, request, exact=False):
        super().__init__(request)
        node, t = request.node, request.type
        self.T = boxed_instance(t)
        value = (copy_location(Call(_type_expr(readable_name(t)), [node], []), node)
                 if exact else node)
        self.next_root = copy_location(Call(Name("box", Load()), [value], []), node)

class ConstrEdit(Edit):
    def __init__(self, request, T):
        super().__init__(request)
        self.T = T

    def run(self):
        operand = self.request.tower[-1]
        converted = copy_location(
            Call(_type_expr(readable_name(self.T)), [operand], []), operand)
        if len(self.request.tower) == 1:
            return converted
        self.request.tower[-2].operand = converted
        return self.request.tower[0]

DONT_CAST = ('int',)
# These boxed scalars convert values; cast only asserts a type.
CONVERTIBLE = ('float', 'int', 'bool')
# containers whose slice comes back as a plain list
CHECKED_NAMES = ('chklist', 'CheckedList', 'chkdict', 'CheckedDict')
class CastEdit(Edit):
    def __init__(self, request, T):
        super().__init__(request)
        node = request.node
        if readable_name(T) in DONT_CAST:
            # An inexact int is accepted directly, so this records no edit.
            return
        self.T = T
        self.next_root = copy_location(
            Call(Name("cast", Load()), [_type_expr(readable_name(T)), node], []), node)


class LenEdit(Edit):
    def __init__(self, edit, types, dyn):
        super().__init__(edit.request)
        self.edit, self.call = edit, edit.request.node
        self.types, self.dyn = types, dyn

    def run(self):
        self.call.func.id = "len"
        self.types[self.call] = self.dyn
        return self.edit.run()

class ClenEdit(Edit):
    def __init__(self, edit, types, dyn):
        super().__init__(edit.request)
        self.edit, self.call = edit, edit.request.node
        self.types, self.dyn = types, dyn

    def run(self):
        self.call.func.id = "clen"
        self.types[self.call] = self.dyn.klass.type_env.int64.instance
        return self.edit.run()

class DiscardTestConversion(Edit):
    def __init__(self, edit):
        super().__init__(edit.request)
        self.edit = edit

    def run(self):
        return self.request.node


class DiscardIndexNarrowing(Edit):
    def __init__(self, edit):
        super().__init__(edit.request)
        self.edit = edit

    def run(self):
        if (isinstance(self.edit, ConstrEdit)
                and readable_name(self.edit.T) == "int64"):
            return self.request.node
        return self.edit.run()

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

    @property
    def dynamic(self):
        return self.mediation.dynamic

    @property
    def node(self):
        return self.tower[0]

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
    return (ClenEdit(inner, request.mediation.types, request.dynamic)
            if primitive
            else LenEdit(inner, request.mediation.types, request.dynamic))


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
            and is_primative(request.type) and not is_const(request.node)):
        # Machine values must box in dynamic slots; literals box implicitly.
        return BoxEdit(request, request.inline_arg)
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


def _choose(request):
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
        self.types, self.env = types, env

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
        produced = self.types.get(operand)
        if isinstance(node.op, Not):
            self.types[result] = (self.env.cbool.instance
                                  if produced is not None
                                  and is_primative(produced)
                                  else self.env.bool.instance)
        elif produced is not None:
            self.types[result] = produced
        return result

    def generic_visit(self, node):
        return node


def mediate(mediation):
    """Choose and apply the edit required at one expression position.

    Towers are stripped before comparing their underlying type with the
    position's demand. Position flags come from the parent-aware walk.
    """
    node = mediation.node
    types = mediation.types
    dyn = mediation.dynamic
    ctx = get_ctx(node)
    if isinstance(node, Slice) or (ctx is not None
                                   and not isinstance(ctx, Load)):
        # Stores, deletions, and slice objects are not value positions.
        return node

    tower, sits_on_len = Tower(types, dyn.klass.type_env).ends(node)
    target = tower[0]
    asked_for = (node.func.id if target is not node
                 and isinstance(node, Call) and isinstance(node.func, Name)
                 else None)
    # Tower records the stripped type, including the effect of unary operators.
    t, tc = types.get(target), mediation.type_contexts.get(node)
    request = EditRequest(
        mediation=mediation,
        tower=tower,
        type=t,
        type_ctx=tc,
        produced=None if target is node else types.get(node),
        readers=tuple(
            types.get(where) for where, slot in
            mediation.graph.outgoing().get(
                mediation.graph.cell(node, "type"), ())
            if slot == "type"),
        sits_on_len=sits_on_len,
        asked_for=asked_for,
    )
    return _choose(request).run()

"""Reusable rules about CinderX types and representations."""

from ast import Not

from cinderx.compiler.static.types import CType

PRIMITIVE_NAMES = {
    "double",
    "cbool",
    "int8",
    "int16",
    "int32",
    "int64",
    "uint8",
    "uint16",
    "uint32",
    "uint64",
}

REMOVABLE_CALLS = {"box"} | PRIMITIVE_NAMES


def exact_inline_args(call, types):
    """Inline-call arguments that require exact mediation."""
    return [
        argument
        for argument in call.args
        if (value_type := types.get(argument)) is None
        or value_type.klass.type_name.readable_name != "int"
    ]


def is_primitive(value_type):
    return isinstance(value_type.klass, CType)


def can_narrow(declared, assigned, dynamic):
    """Whether CinderX keeps an assigned type instead of the declaration."""
    return (
        assigned is not None
        and assigned is not dynamic
        and declared is not None
        and declared.klass.can_be_narrowed
    )


def optional_member(value_type):
    """Return CinderX's canonical Optional member type, if present."""
    inner = (
        getattr(value_type.klass, "opt_type", None)
        if value_type is not None
        else None
    )
    return None if inner is None else inner.instance


def is_optional_of(maybe_optional, member):
    """Whether ``maybe_optional`` is exactly ``member | None``."""
    assert maybe_optional is not None and member is not None
    optional = optional_member(maybe_optional)
    return optional is not None and optional.klass is member.klass


def readable_type_name(value_type):
    if (inner := optional_member(value_type)) is not None:
        return f"{readable_type_name(inner)} | None"
    name = value_type.klass.type_name.readable_name
    for internal, public in {
        "chklist": "CheckedList",
        "chkdict": "CheckedDict",
        "chkset": "CheckedSet",
    }.items():
        name = name.replace(internal, public)
    return name


def boxed_instance(value_type):
    """Return the boxed representation of a primitive instance."""
    environment = value_type.klass.type_env
    if value_type.klass is environment.cbool:
        return environment.bool.instance
    return value_type.klass.boxed.instance


def unop_result_type(operator, operand_type, environment):
    """Return the type produced by a unary operator."""
    if isinstance(operator, Not):
        return (
            environment.cbool.instance
            if operand_type is not None and is_primitive(operand_type)
            else environment.bool.instance
        )
    return operand_type

from sys import path
from ast import FunctionDef, unparse

path.insert(0, "_cinderx/cinderx/PythonLib")
from cinderx.compiler.static.types import CType

def _is_int(t):
    """An int, exact or not.

    There are two `int` instances in play -- cinderx hands back a NumInstance
    where the slot holds a NumExactInstance -- so this asks the name rather
    than the identity.
    """
    return t is not None and t.klass.type_name.readable_name == "int"


def is_inline_call(call, reverse_outflow):
    """Does this call resolve to an `@inline` function?

    cinderx substitutes the body at the call site with the types the arguments
    actually have, so two arguments that no longer agree -- one still typed,
    one erased -- meet inside it as `int64 < dynamic`. The mediator brings the
    typed one down with a cast to `object`; marking the arguments is what lets
    it, by keeping `_choose` off the `valid_pair` branch that would pass the
    argument through untouched. Six deltablue masks turn on it.
    """
    source = reverse_outflow.get(call)
    return (isinstance(source, FunctionDef)
            and "inline" in set(map(unparse, source.decorator_list)))


def inline_args(call, types):
    """The arguments of an inline call that need marking.

    Never an int. CheckedList[int] and CheckedDict[int, int] both take a bool
    -- an int subclass -- without complaint, and every slot we tried accepts an
    inexact int, so an int argument is left alone.

    Two other things used to be marked, both dropped and measured. A returned
    expression is read once, by whatever the call feeds, and that consumer
    picks its own coercion. List comprehension elements went the same way:
    marking the element exact kept `_choose` off `valid_pair`, which is what
    dropping it cost -- six held_karp masks with `Literal[2] received for
    positional arg` -- and once `CastWrapper` stopped emitting a cast to `int`
    those held on their own, leaving the casts as pure cost. pystone alone lost
    25 of them.
    """
    return [argument for argument in call.args
            if not _is_int(types.get(argument))]

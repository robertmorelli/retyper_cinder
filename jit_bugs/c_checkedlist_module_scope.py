# Bug C -- NOT the JIT: a CheckedList[UserClass] filled with const-pool garbage.
#
#   Segmentation fault (rc=-11), and with a print in front of it:
#     spl0.points[0] -> list [('builtins', 'int')]
#     AttributeError: 'list' object has no attribute 'x'
#
# Trigger: constructing an object at *module level* and passing a plain list
# display to a parameter annotated CheckedList[SomeUserClass]. The list arrives
# holding internal type-descriptor data rather than the elements passed. Same
# code inside a function is fine, an explicit CheckedList[GVector]([...]) is
# fine, CheckedList[int] is fine, and a plain function (rather than a
# constructor) taking the same annotation is fine.
#
# Reproduces with --no-jit too, so this one is the static compiler, not the JIT.
# It is what chaos hits once its `__main__` block is hoisted to module level.
# See jit_bug.md.
import __static__
from __static__ import CheckedList


class GVector(object):
    def __init__(self, x: float) -> None:
        self.x: float = x


class Spline(object):
    def __init__(self, points: CheckedList[GVector]) -> None:
        self.points: CheckedList[GVector] = points


print('types', [type(p).__name__ for p in Spline([GVector(1.0)]).points], flush=True)

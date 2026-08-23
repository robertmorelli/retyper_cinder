import __static__
from __static__ import int64, cbool, cast

import cinderx.jit
cinderx.jit.compile_after_n_calls(0)

GLOBAL_COUNT: int = 1


def helper(x: int64, y: int64, scale: int64) -> int64:
    local: int64 = x
    local = local + y
    return local + scale


def kw_helper(left: int64, right: int64) -> int64:
    return left + right


class Box:
    class_value: int = 10

    def __init__(self, value: int64) -> None:
        self.value: int64 = value
        self.flag: cbool = cbool(True)
        self.other = "box"

    def bump(self, amount: int64) -> int64:
        self.value = self.value + amount
        self.value += int64(1)
        return self.value

    @classmethod
    def take(cls, my_box: "Box") -> "Box":
        return my_box

    @staticmethod
    def add(left: int64, right: int64) -> int64:
        return left + right


def smoke(a: int64) -> int64:
    global GLOBAL_COUNT
    my_box: Box = Box(a)
    first: int64 = helper(my_box.value, int64(GLOBAL_COUNT), int64(4))
    second: int64 = my_box.bump(first)
    third: int64 = Box.add(first, kw_helper(left=first, right=second))
    made: Box = Box.take(my_box)
    GLOBAL_COUNT = GLOBAL_COUNT + 1
    alias: int64 = int64(GLOBAL_COUNT)
    alias += made.value
    return helper(alias, made.value, int64(0))


smoke(int64(5))

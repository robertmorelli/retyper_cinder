import __static__
from __static__ import inline


@inline
def double_it(x: int) -> int:
    return x * 2


def use() -> int:
    return double_it(21)

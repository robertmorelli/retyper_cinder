# Probing whether the chklist -> list degradation is actually unsound.
#
# `xs: CheckedList[int] = []` compiles the bare literal INTO a CheckedList.
# Erase that annotation and the literal binds as a plain `list` instead. If a
# consumer keeps its CheckedList annotation, the detyper bridges the boundary
# with cast(CheckedList[int], ...), which is a real isinstance check at runtime
# and a plain list should fail it.
import __static__
from __static__ import CheckedList, cast

import cinderx.jit
cinderx.jit.compile_after_n_calls(0)


def produce() -> CheckedList[int]:
    xs: CheckedList[int] = []
    xs.append(1)
    xs.append(2)
    return xs


def consume(ys: CheckedList[int]) -> int:
    return ys[0] + len(ys)


def round_trip(zs) -> int:
    ws: CheckedList[int] = cast(CheckedList[int], zs)
    ws.append(3)
    return len(ws)


def main() -> int:
    a: CheckedList[int] = produce()
    total: int = consume(a)
    total = total + round_trip(a)
    return total


if __name__ == "__main__":
    print(main())

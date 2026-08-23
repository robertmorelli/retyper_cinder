# import __static__
# from __static__ import box, cbool, int64, CheckedList, cast, Array
# from typing import Any, cast as _cast

# class Shape:
#     def __init__(self):
#         self.size: int64 = 5
#         b: CheckedList[int64] = CheckedList[int64]()
#         b.push(4)
#         c: int64 = b[int64(0)]


# class Circle(Shape):
#     def __init__(self):
#         super().__init__()
#         self.size: int64 = 9

# def foo() -> int64:
#     return int64(1)

# foo()

# a: int = 9
# b = a
# a = 7

#CheckedList[int64]
# def bar(a):
#     a.push(4)
#     i: int64 = 0
#     for i in cast(CheckedList[int64], a):
#         b: int64 = i

# def bar2(a: CheckedList[int64]):
#     # for i in a:
#     #     b: int64 = int64(i)
#     #     print(box(b))
#     c: CheckedList[int64] | None = a
#     if cast(CheckedList[int64], c) is not None:
#         i: int64 = c.pop()

# if __name__ == "__main__":
#     pass
    # a = CheckedList[int64]()
    # a.append(int64(2))
    # bar2(a)

    # c: CheckedList[int] | None = a
    # i: int = (c is not None) and c.pop() or 0

    # c = a
    # i: int = (cast(CheckedList[int], c) is not None) and c.pop() or 0


    # c: CheckedList[int64] | None = a
    # i: int64 = cbool(c is not None) and c.pop() or 0

    # c = a
    # i: int64 = cbool(cast(CheckedList[int64], c) is not None) and c.pop() or 0

    

    # c = a
    # if c is not None:
    #     i: int64 = int64(c.pop())


    # i: int = c.pop()

# def a():
#     b: Any = _cast(Any, Array[int64](2))
#     c: Any = 2
#     d: Any = c
#     b[1] = 4
#     d = b[1]
#     return d


# if __name__ == "__main__":
#     print(a())


# Bootstrap: when run as a script, install the frame
# evaluator and re-exec this file through cinderx's static compiler so the
# body below is actually statically compiled (int64 ops, Array, etc.).

import __static__
from __static__ import int64, box, Array


def f(a: int64, b: int64) -> Array[int64]:
    size: int64 = a / b        # static: integer division -> int
    return Array[int64](box(size))


def main():
    print(box(f(int64(6), int64(2))[0]))

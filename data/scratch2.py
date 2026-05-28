import cinderx
from __static__ import CheckedList, box, cast, cbool, clen, int64, inline

m: CheckedList[int64] = CheckedList[int64]()

class baz:
    def __init__(self):
        self.a: int64 = 1


def foo(b: baz, e) -> int64:
    c: int64 = b.a
    g = e
    f: CheckedList[int64] = cast(CheckedList[int64], g)
    f.append(c)
    return c


def gh(e: CheckedList[int64]) -> CheckedList[int64]:
    return e

def empty_list():
    h: CheckedList[int64] = CheckedList[int64]()
    h.append(int64(1))
    g: CheckedList[int64] = gh(h)
    g = m
    foo(baz(), g)


# if __name__ == "__main__":
#     main()


# def empty_list() -> CheckedList[int64]:
#     a: CheckedList[int64]  = CheckedList[int64]()
#     b: CheckedList[int64] = cast(CheckedList[int64], a)
#     return b


# def foo() -> int:
#     a: CheckedList[int] = CheckedList[int]()
#     for i in range(10):
#         a.push(i)
#     b = 5
#     a[b] = 4
#     return a[2]
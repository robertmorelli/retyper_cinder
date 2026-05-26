import cinderx
from __static__ import CheckedList, box, cast, cbool, clen, int64, inline

class baz:
    def __init__(self):
        self.a: int64 = "hi"


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
    foo(baz(), g)


# if __name__ == "__main__":
#     main()


# def empty_list() -> CheckedList[int64]:
#     a: CheckedList[int64]  = CheckedList[int64]()
#     b: CheckedList[int64] = cast(CheckedList[int64], a)
#     return b


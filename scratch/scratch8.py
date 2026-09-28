import __static__
from __static__ import int64, crange, box
from typing import Any

def add_one(d: int64) -> int64:
    return d + 1

def add_two(d: int64) -> int64:
    a: int64 = add_one(d)
    b: int64 = add_one(a)
    return b



def foo():
    i: Any
    i = int(0)
    for i in range(0, 100):
        print(i)
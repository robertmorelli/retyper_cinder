import __static__
from __static__ import int64


class Shape:
    def __init__(self):
        self.size: int64 = 5


class Circle(Shape):
    def __init__(self):
        super().__init__()
        self.size: int64 = 9

def foo() -> int64:
    return int64(1)

foo()

a: int = 9
b = a
a = 7

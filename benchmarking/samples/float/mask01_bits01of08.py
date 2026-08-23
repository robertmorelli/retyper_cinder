# float/advanced  granularity=benchmark
# mask=1  (1/8 units erased)

from __future__ import annotations
import __static__
from typing import Any
from math import sin, cos, sqrt
from __static__ import CheckedList, inline
from typing import final
import time
import cinderx.jit
cinderx.jit.compile_after_n_calls(0)
'\nbg:\n- add `ITERATIONS` constant\n- remove `main` function\n- add missing type annotations\n- replace `xrange` with `range`\n- remove unused imports\n'

@final
class Point(object):

    def __init__(self, i: Any) -> None:
        x: Any = sin(i)
        self.x: Any = x
        self.y: Any = cos(i) * 3
        self.z: Any = x * x / 2

    def normalize(self) -> None:
        x: float = self.x
        y: float = self.y
        z: float = self.z
        norm: float = sqrt(x * x + y * y + z * z)
        self.x /= norm
        self.y /= norm
        self.z /= norm

    def maximize(self, other: Point) -> Point:
        self.x = self.x if self.x > other.x else other.x
        self.y = self.y if self.y > other.y else other.y
        self.z = self.z if self.z > other.z else other.z
        return self

def maximize_all(points: CheckedList[Point]) -> Point:
    next: Point = points[0]
    for p in CheckedList[Point](points[1:]):
        next = next.maximize(p)
    return next

def normal_point(i: int) -> Point:
    p: Point = Point(float(i))
    p.normalize()
    return p

@inline
def benchmark(n: int) -> Point:
    return maximize_all(CheckedList[Point](map(normal_point, range(n))))
POINTS: int = 200000

def main():
    start_time = time.time()
    benchmark(POINTS)
    end_time = time.time()
    runtime = end_time - start_time
    print(runtime)
if __name__ == '__main__':
    main()

# chaos/advanced  granularity=benchmark
# mask=2913  (6/12 units erased)

"""create chaosgame-like fractals

bg:
- removed `timer` argument, using Timer class instead
- changed `times` return value (returning Void instead of List(Dyn) ... hmm)
- params made mandatory:
  - `GVector.__init__`, all arguments
  - `GVector.linear_combination` `l2`
  - `Spline.__init__`, `degree` and `knots`
- params removed removed:
  - `Chaosgame.transform_point` optional argument `trafo`, was always None
- inlined main function
- replace `reduce(operator.add, self.num_trafos, 0)` with `sum(self.num_trafos)`
- add `ITERATIONS` constant
- replace `xrange` with `range`
- remove unused imports
- CONTROVERSIAL:
  - inlined GetIndex
  - inlined truncate
  - inlined create_image_chaos
  - inlined GetKnots
"""
from __future__ import annotations
import __static__
from typing import Any
from typing import List
from __static__ import CheckedList
import random
import time
random.seed(1234)
ITERATIONS: Any = 1
import math
import cinderx.jit
cinderx.jit.compile_after_n_calls(0)

class GVector(object):

    def __init__(self, x: float, y: float, z: float) -> None:
        self.x: float = x
        self.y: float = y
        self.z: float = z

    def Mag(self) -> Any:
        return math.sqrt(self.x ** 2 + self.y ** 2 + self.z ** 2)

    def dist(self, other: GVector) -> Any:
        return math.sqrt((self.x - other.x) ** 2 + (self.y - other.y) ** 2 + (self.z - other.z) ** 2)

    def __add__(self, other: GVector) -> Any:
        if not isinstance(other, GVector):
            raise ValueError("Can't add GVector to " + str(type(other)))
        v: GVector = GVector(self.x + other.x, self.y + other.y, self.z + other.z)
        return v

    def __sub__(self, other: GVector) -> Any:
        return self + other * -1

    def __mul__(self, other: Any) -> Any:
        v: Any = GVector(self.x * other, self.y * other, self.z * other)
        return v
    __rmul__ = __mul__

    def linear_combination(self, other: Any, l1: Any, l2: Any) -> Any:
        v: Any = GVector(self.x * l1 + other.x * l2, self.y * l1 + other.y * l2, self.z * l1 + other.z * l2)
        return v

class Spline(object):

    def __init__(self, points: CheckedList[GVector], degree: int, knots: CheckedList[int]) -> None:
        if knots == None:
            knots = [0] * degree + range(1, len(points) - degree)
            knots += CheckedList[int]([len(points) - degree]) * degree
            self.knots = knots
        else:
            if len(points) > len(knots) - degree + 1:
                raise ValueError('too many control points')
            elif len(points) < len(knots) - degree + 1:
                raise ValueError('not enough control points')
            last = knots[0]
            for cur in CheckedList[int](knots[1:]):
                if cur < last:
                    raise ValueError('knots not strictly increasing')
                last = cur
            self.knots = knots
        self.points = points
        self.degree = degree

    def GetDomain(self) -> Any:
        return (self.knots[self.degree - 1], self.knots[len(self.knots) - self.degree])

    def __call__(self, u: Any) -> Any:
        dom = self.GetDomain()
        if u < dom[0] or u > dom[1]:
            raise ValueError('Function value not in domain')
        if u == dom[0]:
            return self.points[0]
        if u == dom[1]:
            return self.points[-1]
        I = None
        GetIndexdom = self.GetDomain()
        for ii in range(self.degree - 1, len(self.knots) - self.degree):
            if u >= self.knots[ii] and u < self.knots[ii + 1]:
                I = ii
                break
        else:
            I = GetIndexdom[1] - 1
        d: Any = [self.points[I - self.degree + 1 + ii] for ii in range(self.degree + 1)]
        U = self.knots
        for ik in range(1, self.degree + 1):
            for ii in range(I - self.degree + ik + 1, I + 2):
                ua = U[ii + self.degree - ik]
                ub = U[ii - 1]
                co1 = (ua - u) / (ua - ub)
                co2 = (u - ub) / (ua - ub)
                index = ii - I + self.degree - ik - 1
                d[index] = d[index].linear_combination(d[index + 1], co1, co2)
        return d[0]

class Chaosgame(object):

    def __init__(self, splines: Any, thickness: Any, w: Any, h: Any, n: Any) -> None:
        self.splines: Any = CheckedList[Spline](splines)
        self.thickness = thickness
        self.minx = min([p.x for spl in splines for p in spl.points])
        self.miny = min([p.y for spl in splines for p in spl.points])
        self.maxx = max([p.x for spl in splines for p in spl.points])
        self.maxy = max([p.y for spl in splines for p in spl.points])
        self.height = self.maxy - self.miny
        self.width = self.maxx - self.minx
        self.num_trafos = []
        maxlength = thickness * self.width / self.height
        for spl in splines:
            length: Any = 0
            curr = spl(0)
            for i in range(1, 1000):
                last = curr
                t = 0.001001001001001001 * i
                curr = spl(t)
                length += curr.dist(last)
            self.num_trafos.append(max(1, int(length / maxlength * 1.5)))
        self.num_total = sum(self.num_trafos)
        im: Any = [[1] * h for i in range(w)]
        point = GVector((self.maxx + self.minx) / 2, (self.maxy + self.miny) / 2, 0)
        colored: Any = 0
        for _ in range(n):
            for i in range(5000):
                point = self.transform_point(point)
                x = (point.x - self.minx) / self.width * w
                y = (point.y - self.miny) / self.height * h
                x = int(x)
                y = int(y)
                if x == w:
                    x -= 1
                if y == h:
                    y -= 1
                im[x][h - y - 1] = 0

    def transform_point(self, point: GVector) -> Any:
        x = (point.x - self.minx) / self.width
        y = (point.y - self.miny) / self.height
        rrr = random.randrange(int(self.num_total) + 1)
        lll: int = 0
        for iii in range(len(self.num_trafos)):
            if rrr >= lll and rrr < lll + self.num_trafos[iii]:
                trafo = (iii, random.randrange(self.num_trafos[iii]))
            lll += self.num_trafos[iii]
        trafo = (len(self.num_trafos) - 1, random.randrange(self.num_trafos[-1]))
        start, end = self.splines[trafo[0]].GetDomain()
        length = end - start
        seg_length = length / self.num_trafos[trafo[0]]
        t = start + seg_length * trafo[1] + seg_length * x
        basepoint = self.splines[trafo[0]](t)
        if t + 2e-05 > end:
            neighbour = self.splines[trafo[0]](t - 2e-05)
            derivative = neighbour - basepoint
        else:
            neighbour = self.splines[trafo[0]](t + 2e-05)
            derivative = basepoint - neighbour
        if derivative.Mag() != 0:
            basepoint.x += derivative.y / derivative.Mag() * (y - 0.5) * self.thickness
            basepoint.y += -derivative.x / derivative.Mag() * (y - 0.5) * self.thickness
        else:
            print('r', end='')
        if basepoint.x >= self.maxx:
            basepoint.x = self.maxx
        if basepoint.y >= self.maxy:
            basepoint.y = self.maxy
        if basepoint.x < self.minx:
            basepoint.x = self.minx
        if basepoint.y < self.miny:
            basepoint.y = self.miny
        return basepoint

def main() -> Any:
    splines: Any = [Spline([GVector(1.59735, 3.30446, 0.0), GVector(1.57581, 4.12326, 0.0), GVector(1.31321, 5.28835, 0.0), GVector(1.6189, 5.32991, 0.0), GVector(2.88994, 5.5027, 0.0), GVector(2.37306, 4.38183, 0.0), GVector(1.662, 4.36028, 0.0)], 3, [0, 0, 0, 1, 1, 1, 2, 2, 2]), Spline([GVector(2.8045, 4.01735, 0.0), GVector(2.5505, 3.52523, 0.0), GVector(1.97901, 2.62036, 0.0), GVector(1.97901, 2.62036, 0.0)], 3, [0, 0, 0, 1, 1, 1]), Spline([GVector(2.00167, 4.01132, 0.0), GVector(2.33504, 3.31283, 0.0), GVector(2.3668, 3.23346, 0.0), GVector(2.3668, 3.23346, 0.0)], 3, [0, 0, 0, 1, 1, 1])]
    startTime = time.time()
    c: Any = Chaosgame(splines, 0.25, 1000, 1200, ITERATIONS)
    endTime = time.time()
    runtime = endTime - startTime
    print(runtime)
if __name__ == '__main__':
    main()

# nbody/advanced  granularity=benchmark
# mask=510  (8/10 units erased)

"""
N-body benchmark from the Computer Language Benchmarks Game.

This is intended to support Unladen Swallow's pyperf.py. Accordingly, it has been
modified from the Shootout version:
- Accept standard Unladen Swallow benchmark options.
- Run report_energy()/advance() in a loop.
- Reimplement itertools.combinations() to work with older Python versions.

Pulled from:
http://benchmarksgame.alioth.debian.org/u64q/program.php?test=nbody&lang=python3&id=1

Contributed by Kevin Carson.
Modified by Tupteq, Fredrik Johansson, and Daniel Nanz.
"""
import __static__
from typing import Any
from __static__ import double, CheckedList, CheckedDict, box
import time
import sys
import cinderx.jit
cinderx.jit.compile_after_n_calls(0)
__contact__: str = 'collinwinter@google.com (Collin Winter)'
DEFAULT_ITERATIONS: int = 20000
DEFAULT_REFERENCE: str = 'sun'
PI: float = 3.141592653589793
SOLAR_MASS = 4 * PI * PI
DAYS_PER_YEAR: float = 365.24

class Vector:

    def __init__(self, x: Any, y: Any, z: Any):
        self.x: Any = x
        self.y: Any = y
        self.z: Any = z

    def __repr__(self):
        return f'Vector({(self.x, self.y, self.z)})'

class Body:

    def __init__(self, pos: Any, v: Any, mass: Any):
        self.pos: Any = pos
        self.v: Any = v
        self.mass: Any = mass

    def __repr__(self):
        return f'Body({self.pos}, {self.v}, {self.mass})'

def combinations(l: Any) -> list[tuple[Body, Body]]:
    """Pure-Python implementation of itertools.combinations(l, 2)."""
    result: Any = []
    y: Any
    for x in range(len(l) - 1):
        ls: Any = l[x + 1:]
        for y in ls:
            result.append((l[x], y))
    return result
BODIES = CheckedDict[str, Body]({'sun': Body(Vector(0.0, 0.0, 0.0), Vector(0.0, 0.0, 0.0), SOLAR_MASS), 'jupiter': Body(Vector(4.841431442464721, -1.1603200440274284, -0.10362204447112311), Vector(0.001660076642744037 * DAYS_PER_YEAR, 0.007699011184197404 * DAYS_PER_YEAR, -6.90460016972063e-05 * DAYS_PER_YEAR), 0.0009547919384243266 * SOLAR_MASS), 'saturn': Body(Vector(8.34336671824458, 4.124798564124305, -0.4035234171143214), Vector(-0.002767425107268624 * DAYS_PER_YEAR, 0.004998528012349172 * DAYS_PER_YEAR, 2.3041729757376393e-05 * DAYS_PER_YEAR), 0.0002858859806661308 * SOLAR_MASS), 'uranus': Body(Vector(12.894369562139131, -15.111151401698631, -0.22330757889265573), Vector(0.002964601375647616 * DAYS_PER_YEAR, 0.0023784717395948095 * DAYS_PER_YEAR, -2.9658956854023756e-05 * DAYS_PER_YEAR), 4.366244043351563e-05 * SOLAR_MASS), 'neptune': Body(Vector(15.379697114850917, -25.919314609987964, 0.17925877295037118), Vector(0.0026806777249038932 * DAYS_PER_YEAR, 0.001628241700382423 * DAYS_PER_YEAR, -9.515922545197159e-05 * DAYS_PER_YEAR), 5.1513890204661145e-05 * SOLAR_MASS)})
SYSTEM: CheckedList[Body] = CheckedList[Body](BODIES.values())
PAIRS: list[tuple[Body, Body]] = combinations(SYSTEM)

def advance(dt: Any, n: Any, bodies: Any=SYSTEM, pairs: Any=PAIRS):
    for i in range(n):
        b1: Any
        b2: Any
        for b1, b2 in pairs:
            pos1: Any = b1.pos
            pos2: Any = b2.pos
            dx: Any = pos1.x - pos2.x
            dy: Any = pos1.y - pos2.y
            dz: Any = pos1.z - pos2.z
            mag: Any = dt * box((double(dx) * double(dx) + double(dy) * double(dy) + double(dz) * double(dz)) ** -1.5)
            b1m: Any = b1.mass * mag
            b2m: Any = b2.mass * mag
            v1: Any = b1.v
            v2: Any = b2.v
            v1.x -= dx * b2m
            v1.y -= dy * b2m
            v1.z -= dz * b2m
            v2.x += dx * b1m
            v2.y += dy * b1m
            v2.z += dz * b1m
        for body in bodies:
            r: Any = body.pos
            v: Any = body.v
            r.x += dt * v.x
            r.y += dt * v.y
            r.z += dt * v.z

def report_energy(bodies: Any=SYSTEM, pairs: Any=PAIRS, e: Any=0.0) -> double:
    b1: Any
    b2: Any
    body: Any
    e1: Any
    for b1, b2 in pairs:
        pos1: Any = b1.pos
        pos2: Any = b2.pos
        dx: Any = pos1.x - pos2.x
        dy: Any = pos1.y - pos2.y
        dz: Any = pos1.z - pos2.z
        e -= b1.mass * b2.mass / box((double(dx) * double(dx) + double(dy) * double(dy) + double(dz) * double(dz)) ** 0.5)
    for body in bodies:
        v: Any = body.v
        e1 = v.x * v.x + v.y * v.y + v.z * v.z
        e1 *= 0.5
        e += body.mass * e1
    return double(e)

def offset_momentum(ref: Any, bodies: Any, px: Any=0.0, py: Any=0.0, pz: Any=0.0):
    body: Any
    for body in bodies:
        v: Any = body.v
        m: Any = body.mass
        px -= v.x * m
        py -= v.y * m
        pz -= v.z * m
    m = ref.mass
    v = ref.v
    v.x = px / m
    v.y = py / m
    v.z = pz / m

def bench_nbody(loops: Any, reference: Any, iterations: Any):
    offset_momentum(BODIES[reference], SYSTEM)
    range_it: Any = range(loops)
    for _ in range_it:
        report_energy(SYSTEM, PAIRS)
        advance(0.01, iterations, SYSTEM, PAIRS)
        report_energy(SYSTEM, PAIRS)

def run():
    num_loops: Any = 5
    bench_nbody(num_loops, DEFAULT_REFERENCE, DEFAULT_ITERATIONS)

def main():
    num_loops: int = 5
    startTime = time.time()
    bench_nbody(num_loops, DEFAULT_REFERENCE, DEFAULT_ITERATIONS)
    endTime = time.time()
    runtime = endTime - startTime
    print(runtime)
if __name__ == '__main__':
    main()

# nbody/advanced  granularity=annotation
# mask=2371154248676886446899  (19/75 units erased)

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
import cinderx.jit
cinderx.jit.compile_after_n_calls(0)
__contact__: Any = 'collinwinter@google.com (Collin Winter)'
DEFAULT_ITERATIONS: Any = 20000
DEFAULT_REFERENCE: str = 'sun'
PI: float = 3.141592653589793
SOLAR_MASS = 4 * PI * PI
DAYS_PER_YEAR: Any = 365.24

class Vector:

    def __init__(self, x: double, y: double, z: Any):
        self.x: Any = box(x)
        self.y: double = y
        self.z: double = double(z)

    def __repr__(self):
        return f'Vector({(self.x, box(self.y), box(self.z))})'

class Body:

    def __init__(self, pos: Vector, v: Vector, mass: double):
        self.pos: Vector = pos
        self.v: Vector = v
        self.mass: double = mass

    def __repr__(self):
        return f'Body({self.pos}, {self.v}, {box(self.mass)})'

def combinations(l: CheckedList[Body]) -> Any:
    """Pure-Python implementation of itertools.combinations(l, 2)."""
    result: list[tuple[Body, Body]] = []
    y: Body
    for x in range(len(l) - 1):
        ls: CheckedList[Body] = CheckedList[Body](l[x + 1:])
        for y in ls:
            result.append((l[x], y))
    return result
BODIES = CheckedDict[str, Body]({'sun': Body(Vector(0.0, 0.0, 0.0), Vector(0.0, 0.0, 0.0), double(SOLAR_MASS)), 'jupiter': Body(Vector(4.841431442464721, -1.1603200440274284, -0.10362204447112311), Vector(0.001660076642744037 * double(DAYS_PER_YEAR), 0.007699011184197404 * double(DAYS_PER_YEAR), -6.90460016972063e-05 * DAYS_PER_YEAR), 0.0009547919384243266 * double(SOLAR_MASS)), 'saturn': Body(Vector(8.34336671824458, 4.124798564124305, -0.4035234171143214), Vector(-0.002767425107268624 * double(DAYS_PER_YEAR), 0.004998528012349172 * double(DAYS_PER_YEAR), 2.3041729757376393e-05 * DAYS_PER_YEAR), 0.0002858859806661308 * double(SOLAR_MASS)), 'uranus': Body(Vector(12.894369562139131, -15.111151401698631, -0.22330757889265573), Vector(0.002964601375647616 * double(DAYS_PER_YEAR), 0.0023784717395948095 * double(DAYS_PER_YEAR), -2.9658956854023756e-05 * DAYS_PER_YEAR), 4.366244043351563e-05 * double(SOLAR_MASS)), 'neptune': Body(Vector(15.379697114850917, -25.919314609987964, 0.17925877295037118), Vector(0.0026806777249038932 * double(DAYS_PER_YEAR), 0.001628241700382423 * double(DAYS_PER_YEAR), -9.515922545197159e-05 * DAYS_PER_YEAR), 5.1513890204661145e-05 * double(SOLAR_MASS))})
SYSTEM: Any = CheckedList[Body](BODIES.values())
PAIRS: Any = combinations(SYSTEM)

def advance(dt: double, n, bodies: CheckedList[Body]=SYSTEM, pairs: list[tuple[Body, Body]]=PAIRS):
    for i in range(n):
        b1: Body
        b2: Body
        for b1, b2 in pairs:
            pos1: Vector = b1.pos
            pos2: Vector = b2.pos
            dx: double = double(pos1.x - pos2.x)
            dy: double = pos1.y - pos2.y
            dz: Any = box(pos1.z - pos2.z)
            mag: double = dt * (dx * dx + dy * dy + double(dz * dz)) ** -1.5
            b1m: double = b1.mass * mag
            b2m: Any = box(b2.mass * mag)
            v1: Vector = b1.v
            v2: Vector = b2.v
            v1.x -= box(dx) * b2m
            v1.y -= dy * double(b2m)
            v1.z -= double(dz * b2m)
            v2.x += box(dx * b1m)
            v2.y += dy * b1m
            v2.z += double(dz) * b1m
        for body in bodies:
            r: Vector = body.pos
            v: Any = body.v
            r.x += box(dt) * v.x
            r.y += dt * v.y
            r.z += dt * v.z

def report_energy(bodies: CheckedList[Body]=SYSTEM, pairs: list[tuple[Body, Body]]=PAIRS, e: double=0.0) -> double:
    b1: Body
    b2: Body
    body: Body
    for b1, b2 in pairs:
        pos1: Vector = b1.pos
        pos2: Vector = b2.pos
        dx: double = double(pos1.x - pos2.x)
        dy: Any = box(pos1.y - pos2.y)
        dz: Any = box(pos1.z - pos2.z)
        e -= b1.mass * b2.mass / (dx * dx + double(dy * dy) + double(dz * dz)) ** 0.5
    for body in bodies:
        v: Vector = body.v
        e += body.mass * (double(v.x * v.x) + v.y * v.y + v.z * v.z) / 2.0
    return e

def offset_momentum(ref: Any, bodies: CheckedList[Body], px: Any=0.0, py: double=0.0, pz: double=0.0):
    body: Body
    for body in bodies:
        v: Vector = body.v
        m: Any = box(body.mass)
        px -= v.x * m
        py -= v.y * double(m)
        pz -= v.z * double(m)
    m = ref.mass
    v = ref.v
    v.x = px / m
    v.y = py / double(m)
    v.z = pz / double(m)

def bench_nbody(loops: int, reference: str, iterations: int):
    offset_momentum(BODIES[reference], SYSTEM)
    range_it: range = range(loops)
    for _ in range_it:
        report_energy(SYSTEM, PAIRS)
        advance(0.01, iterations, SYSTEM, PAIRS)
        report_energy(SYSTEM, PAIRS)

def run():
    num_loops: int = 5
    bench_nbody(num_loops, DEFAULT_REFERENCE, DEFAULT_ITERATIONS)

def main():
    num_loops: Any = 5
    startTime = time.time()
    bench_nbody(num_loops, DEFAULT_REFERENCE, DEFAULT_ITERATIONS)
    endTime = time.time()
    runtime = endTime - startTime
    print(runtime)
if __name__ == '__main__':
    main()

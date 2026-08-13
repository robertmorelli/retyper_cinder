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
DEFAULT_REFERENCE: Any = 'sun'
PI: Any = 3.141592653589793
SOLAR_MASS = 4 * PI * PI
DAYS_PER_YEAR: Any = 365.24

class Vector:

    def __init__(self, x: Any, y: Any, z: Any):
        self.x: Any = x
        self.y: Any = y
        self.z: Any = z

    def __repr__(self) -> Any:
        return f'Vector({(box(double(self.x)), box(double(self.y)), box(double(self.z)))})'

class Body:

    def __init__(self, pos: Any, v: Any, mass: Any):
        self.pos: Any = pos
        self.v: Any = v
        self.mass: Any = mass

    def __repr__(self) -> Any:
        return f'Body({self.pos}, {self.v}, {self.mass})'

def combinations(l: CheckedList[Body]) -> Any:
    """Pure-Python implementation of itertools.combinations(l, 2)."""
    result: list[tuple[Body, Body]] = []
    y: Body
    for x in range(len(l) - 1):
        ls: CheckedList[Body] = l[x + 1:]
        for y in ls:
            result.append((l[x], y))
    return result
BODIES = CheckedDict[str, Body]({'sun': Body(Vector(0.0, 0.0, 0.0), Vector(0.0, 0.0, 0.0), SOLAR_MASS), 'jupiter': Body(Vector(4.841431442464721, -1.1603200440274284, -0.10362204447112311), Vector(box(0.001660076642744037 * double(DAYS_PER_YEAR)), box(0.007699011184197404 * double(DAYS_PER_YEAR)), box(-6.90460016972063e-05 * double(DAYS_PER_YEAR))), box(0.0009547919384243266 * double(SOLAR_MASS))), 'saturn': Body(Vector(8.34336671824458, 4.124798564124305, -0.4035234171143214), Vector(box(-0.002767425107268624 * double(DAYS_PER_YEAR)), box(0.004998528012349172 * double(DAYS_PER_YEAR)), box(2.3041729757376393e-05 * double(DAYS_PER_YEAR))), box(0.0002858859806661308 * double(SOLAR_MASS))), 'uranus': Body(Vector(12.894369562139131, -15.111151401698631, -0.22330757889265573), Vector(box(0.002964601375647616 * double(DAYS_PER_YEAR)), box(0.0023784717395948095 * double(DAYS_PER_YEAR)), box(-2.9658956854023756e-05 * double(DAYS_PER_YEAR))), box(4.366244043351563e-05 * double(SOLAR_MASS))), 'neptune': Body(Vector(15.379697114850917, -25.919314609987964, 0.17925877295037118), Vector(box(0.0026806777249038932 * double(DAYS_PER_YEAR)), box(0.001628241700382423 * double(DAYS_PER_YEAR)), box(-9.515922545197159e-05 * double(DAYS_PER_YEAR))), box(5.1513890204661145e-05 * double(SOLAR_MASS)))})
SYSTEM: Any = CheckedList[Body](BODIES.values())
PAIRS: Any = combinations(SYSTEM)

def advance(dt: double, n, bodies: CheckedList[Body]=SYSTEM, pairs: list[tuple[Body, Body]]=PAIRS) -> Any:
    for i in range(n):
        b1: Body
        b2: Body
        for b1, b2 in pairs:
            pos1: Vector = b1.pos
            pos2: Vector = b2.pos
            dx: double = double(pos1.x - pos2.x)
            dy: double = double(pos1.y - pos2.y)
            dz: double = double(pos1.z - pos2.z)
            mag: double = dt * (dx * dx + dy * dy + dz * dz) ** -1.5
            b1m: double = double(b1.mass * box(mag))
            b2m: double = double(b2.mass * box(mag))
            v1: Vector = b1.v
            v2: Vector = b2.v
            v1.x -= box(dx * b2m)
            v1.y -= box(dy * b2m)
            v1.z -= box(dz * b2m)
            v2.x += box(dx * b1m)
            v2.y += box(dy * b1m)
            v2.z += box(dz * b1m)
        for body in bodies:
            r: Vector = body.pos
            v: Vector = body.v
            r.x += box(dt) * v.x
            r.y += box(dt) * v.y
            r.z += box(dt) * v.z

def report_energy(bodies: CheckedList[Body]=SYSTEM, pairs: list[tuple[Body, Body]]=PAIRS, e: double=0.0) -> Any:
    b1: Body
    b2: Body
    body: Body
    for b1, b2 in pairs:
        pos1: Vector = b1.pos
        pos2: Vector = b2.pos
        dx: double = double(pos1.x - pos2.x)
        dy: double = double(pos1.y - pos2.y)
        dz: double = double(pos1.z - pos2.z)
        e -= double(b1.mass * b2.mass / box((dx * dx + dy * dy + dz * dz) ** 0.5))
    for body in bodies:
        v: Vector = body.v
        e += double(body.mass * (v.x * v.x + v.y * v.y + v.z * v.z) / 2.0)
    return box(e)

def offset_momentum(ref: Body, bodies: CheckedList[Body], px: double=0.0, py: double=0.0, pz: double=0.0) -> Any:
    body: Body
    for body in bodies:
        v: Vector = body.v
        m: double = double(body.mass)
        px -= double(v.x * box(m))
        py -= double(v.y * box(m))
        pz -= double(v.z * box(m))
    m = double(ref.mass)
    v = ref.v
    v.x = box(px / m)
    v.y = box(py / m)
    v.z = box(pz / m)

def bench_nbody(loops: int, reference: str, iterations: int) -> Any:
    offset_momentum(BODIES[reference], SYSTEM)
    range_it: range = range(loops)
    for _ in range_it:
        double(report_energy(SYSTEM, PAIRS))
        advance(0.01, iterations, SYSTEM, PAIRS)
        double(report_energy(SYSTEM, PAIRS))

def run():
    num_loops: int = 5
    bench_nbody(num_loops, DEFAULT_REFERENCE, DEFAULT_ITERATIONS)

def main() -> Any:
    import sys
    num_loops: int = 5
    startTime = time.time()
    bench_nbody(num_loops, DEFAULT_REFERENCE, DEFAULT_ITERATIONS)
    endTime = time.time()
    runtime = endTime - startTime
    print(runtime)
if __name__ == '__main__':
    main()

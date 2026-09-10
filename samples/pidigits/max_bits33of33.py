# pidigits/advanced  granularity=annotation
# mask=8589934591  (33/33 units erased)

"""Calculating (some of) the digits of pi.  This stresses big integer
arithmetic."""
import __static__
from typing import Any
'\nbg\n- remove toplevel function, add `ITERATIONS` constant\n- replace `timer` with `Timer`\n- replaced iterators with lists\n- increased NDIGITS to `5000`\n'
from __static__ import CheckedList
import time
NDIGITS = 5000

def gen_x(k: Any) -> Any:
    return CheckedList[int]([k, 4 * k + 2, 0, 2 * k + 1])

def compose(a: Any, b: Any) -> Any:
    aq = a[0]
    ar = a[1]
    as_ = a[2]
    at = a[3]
    bq = b[0]
    br = b[1]
    bs = b[2]
    bt = b[3]
    return CheckedList[int]([aq * bq, aq * br + ar * bt, as_ * bq + at * bs, as_ * br + at * bt])

def extract(z: Any, j: Any) -> Any:
    q = z[0]
    r = z[1]
    s = z[2]
    t = z[3]
    return (q * j + r) // (s * j + t)

def pi_digits(limit: Any) -> Any:
    z: Any = CheckedList[int]([1, 0, 0, 1])
    x: Any = 1
    y: Any
    result: Any = CheckedList[int]([])
    while x <= limit:
        y = extract(z, 3)
        while y != extract(z, 4):
            z = compose(z, gen_x(x))
            y = extract(z, 3)
        z = compose(CheckedList[int]([10, -10 * y, 0, 1]), z)
        x += 1
        result.append(y)
    return result

def calc_ndigits(n: Any) -> Any:
    return pi_digits(n)

def main() -> Any:
    startTime = time.time()
    calc_ndigits(NDIGITS)
    endTime = time.time()
    runtime = endTime - startTime
    print(runtime)
if __name__ == '__main__':
    main()

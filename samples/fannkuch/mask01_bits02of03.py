# fannkuch/advanced  granularity=benchmark
# mask=3  (2/3 units erased)

"""
The Computer Language Benchmarks Game
http://benchmarksgame.alioth.debian.org/
Contributed by Sokolov Yura, modified by Tupteq.
"""
from __future__ import annotations
import __static__
from typing import Any
from __static__ import box, int64, Array
import sys
import time
import cinderx.jit
cinderx.jit.compile_after_n_calls(0)
DEFAULT_ARG: Any = 9

def fannkuch(nb: Any) -> Any:
    n: Any = nb
    count: Any = Array[int64](nb)
    i: Any = 0
    while i < n:
        count[i] = int64(i) + 1
        i += 1
    max_flips: Any = 0
    m: Any = n - 1
    r: Any = n
    perm1: Any = Array[int64](nb)
    perm: Any = Array[int64](nb)
    i = 0
    while i < n:
        perm1[i] = int64(i)
        perm[i] = int64(i)
        i += 1
    perm0: Any = Array[int64](nb)
    while 1:
        while r != 1:
            count[r - 1] = int64(r)
            r -= 1
        if perm1[0] != 0 and perm1[m] != int64(m):
            i = 0
            while i < n:
                perm[i] = perm1[i]
                i += 1
            flips_count: Any = 0
            k: Any = box(perm[0])
            while k:
                i = k
                while i >= 0:
                    perm0[i] = perm[k - i]
                    i -= 1
                i = k
                while i >= 0:
                    perm[i] = perm0[i]
                    i -= 1
                flips_count += 1
                k = box(perm[0])
            if flips_count > max_flips:
                max_flips = flips_count
        while r != n:
            first: Any = box(perm1[0])
            i = 1
            while i <= r:
                perm1[i - 1] = perm1[i]
                i += 1
            perm1[r] = int64(first)
            count[r] -= 1
            if count[r] > 0:
                break
            r += 1
        else:
            return max_flips
    return 0

def main() -> Any:
    num_iterations = 1
    if len(sys.argv) > 1:
        num_iterations = int(sys.argv[1])
    start_time = time.time()
    for _ in range(num_iterations):
        res: int = fannkuch(DEFAULT_ARG)
        assert res == 30
    end_time = time.time()
    runtime = end_time - start_time
    print(runtime / num_iterations)
if __name__ == '__main__':
    main()

import __static__
from __static__ import crange, int64, Array, box


def main():
    a: Array[int64] = Array[int64](len(range(0,100)))
    i: int64 = 0
    for e in crange(int64(0),int64(100)):
        a[i] = e
        i += 1
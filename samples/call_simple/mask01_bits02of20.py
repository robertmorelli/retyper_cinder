# call_simple/advanced  granularity=annotation
# mask=131136  (2/20 units erased)

"""Microbenchmark for function call overhead.

This measures simple function calls that are not methods, do not use varargs or
kwargs, and do not use tuple unpacking.

bg:
- annotated all parameters + return types
  the first parameter of ALL functions was untyped
- using Timer
- fixed num iterations (see bottom of file)
- removed command-line parsing
"""
import __static__
from typing import Any
from __static__ import int64, box
import time
import cinderx.jit
cinderx.jit.compile_after_n_calls(0)

def foo(a: int64, b: int64, c: int64, d: int64) -> None:
    bar(box(a), b, c)
    bar(box(a), b, c)
    bar(box(a), b, c)
    bar(box(a), b, c)
    bar(box(a), b, c)
    bar(box(a), b, c)
    bar(box(a), b, c)
    bar(box(a), b, c)
    bar(box(a), b, c)
    bar(box(a), b, c)
    bar(box(a), b, c)
    bar(box(a), b, c)
    bar(box(a), b, c)
    bar(box(a), b, c)
    bar(box(a), b, c)
    bar(box(a), b, c)
    bar(box(a), b, c)
    bar(box(a), b, c)
    bar(box(a), b, c)
    bar(box(a), b, c)

def bar(a: Any, b: int64, c: int64) -> None:
    baz(int64(a), b)
    baz(int64(a), b)
    baz(int64(a), b)
    baz(int64(a), b)
    baz(int64(a), b)
    baz(int64(a), b)
    baz(int64(a), b)
    baz(int64(a), b)
    baz(int64(a), b)
    baz(int64(a), b)
    baz(int64(a), b)
    baz(int64(a), b)
    baz(int64(a), b)
    baz(int64(a), b)
    baz(int64(a), b)
    baz(int64(a), b)
    baz(int64(a), b)
    baz(int64(a), b)
    baz(int64(a), b)
    baz(int64(a), b)

def baz(a: int64, b: int64) -> None:
    quux(a)
    quux(a)
    quux(a)
    quux(a)
    quux(a)
    quux(a)
    quux(a)
    quux(a)
    quux(a)
    quux(a)
    quux(a)
    quux(a)
    quux(a)
    quux(a)
    quux(a)
    quux(a)
    quux(a)
    quux(a)
    quux(a)
    quux(a)

def quux(a: int64) -> None:
    qux()
    qux()
    qux()
    qux()
    qux()
    qux()
    qux()
    qux()
    qux()
    qux()
    qux()
    qux()
    qux()
    qux()
    qux()
    qux()
    qux()
    qux()
    qux()
    qux()

def qux() -> None:
    pass

def test_calls() -> None:
    foo(1, 2, 3, 4)
    foo(1, 2, 3, 4)
    foo(1, 2, 3, 4)
    foo(1, 2, 3, 4)
    foo(1, 2, 3, 4)
    foo(1, 2, 3, 4)
    foo(1, 2, 3, 4)
    foo(1, 2, 3, 4)
    foo(1, 2, 3, 4)
    foo(1, 2, 3, 4)
    foo(1, 2, 3, 4)
    foo(1, 2, 3, 4)
    foo(1, 2, 3, 4)
    foo(1, 2, 3, 4)
    foo(1, 2, 3, 4)
    foo(1, 2, 3, 4)
    foo(1, 2, 3, 4)
    foo(1, 2, 3, 4)
    foo(1, 2, 3, 4)
    foo(1, 2, 3, 4)
    return

def main():
    startTime = time.time()
    test_calls()
    endTime = time.time()
    runtime = endTime - startTime
    print(runtime)
if __name__ == '__main__':
    main()

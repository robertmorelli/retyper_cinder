# call_simple/advanced  granularity=benchmark
# mask=51  (4/6 units erased)

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

def foo(a: Any, b: Any, c: Any, d: Any) -> Any:
    bar(int64(a), int64(b), int64(c))
    bar(int64(a), int64(b), int64(c))
    bar(int64(a), int64(b), int64(c))
    bar(int64(a), int64(b), int64(c))
    bar(int64(a), int64(b), int64(c))
    bar(int64(a), int64(b), int64(c))
    bar(int64(a), int64(b), int64(c))
    bar(int64(a), int64(b), int64(c))
    bar(int64(a), int64(b), int64(c))
    bar(int64(a), int64(b), int64(c))
    bar(int64(a), int64(b), int64(c))
    bar(int64(a), int64(b), int64(c))
    bar(int64(a), int64(b), int64(c))
    bar(int64(a), int64(b), int64(c))
    bar(int64(a), int64(b), int64(c))
    bar(int64(a), int64(b), int64(c))
    bar(int64(a), int64(b), int64(c))
    bar(int64(a), int64(b), int64(c))
    bar(int64(a), int64(b), int64(c))
    bar(int64(a), int64(b), int64(c))

def bar(a: int64, b: int64, c: int64) -> Any:
    baz(a, b)
    baz(a, b)
    baz(a, b)
    baz(a, b)
    baz(a, b)
    baz(a, b)
    baz(a, b)
    baz(a, b)
    baz(a, b)
    baz(a, b)
    baz(a, b)
    baz(a, b)
    baz(a, b)
    baz(a, b)
    baz(a, b)
    baz(a, b)
    baz(a, b)
    baz(a, b)
    baz(a, b)
    baz(a, b)

def baz(a: int64, b: int64) -> Any:
    quux(box(a))
    quux(box(a))
    quux(box(a))
    quux(box(a))
    quux(box(a))
    quux(box(a))
    quux(box(a))
    quux(box(a))
    quux(box(a))
    quux(box(a))
    quux(box(a))
    quux(box(a))
    quux(box(a))
    quux(box(a))
    quux(box(a))
    quux(box(a))
    quux(box(a))
    quux(box(a))
    quux(box(a))
    quux(box(a))

def quux(a: Any) -> Any:
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

def qux() -> Any:
    pass

def test_calls() -> Any:
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

def main() -> Any:
    startTime = time.time()
    test_calls()
    endTime = time.time()
    runtime = endTime - startTime
    print(runtime)
if __name__ == '__main__':
    main()

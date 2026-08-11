# call_simple/advanced  granularity=benchmark
# mask=4  (1/5 units erased)

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
    bar(box(a), box(b), box(c))
    bar(box(a), box(b), box(c))
    bar(box(a), box(b), box(c))
    bar(box(a), box(b), box(c))
    bar(box(a), box(b), box(c))
    bar(box(a), box(b), box(c))
    bar(box(a), box(b), box(c))
    bar(box(a), box(b), box(c))
    bar(box(a), box(b), box(c))
    bar(box(a), box(b), box(c))
    bar(box(a), box(b), box(c))
    bar(box(a), box(b), box(c))
    bar(box(a), box(b), box(c))
    bar(box(a), box(b), box(c))
    bar(box(a), box(b), box(c))
    bar(box(a), box(b), box(c))
    bar(box(a), box(b), box(c))
    bar(box(a), box(b), box(c))
    bar(box(a), box(b), box(c))
    bar(box(a), box(b), box(c))

def bar(a: Any, b: Any, c: Any) -> None:
    baz(int64(a), int64(b))
    baz(int64(a), int64(b))
    baz(int64(a), int64(b))
    baz(int64(a), int64(b))
    baz(int64(a), int64(b))
    baz(int64(a), int64(b))
    baz(int64(a), int64(b))
    baz(int64(a), int64(b))
    baz(int64(a), int64(b))
    baz(int64(a), int64(b))
    baz(int64(a), int64(b))
    baz(int64(a), int64(b))
    baz(int64(a), int64(b))
    baz(int64(a), int64(b))
    baz(int64(a), int64(b))
    baz(int64(a), int64(b))
    baz(int64(a), int64(b))
    baz(int64(a), int64(b))
    baz(int64(a), int64(b))
    baz(int64(a), int64(b))

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
if __name__ == '__main__':
    startTime = time.time()
    test_calls()
    endTime = time.time()
    runtime = endTime - startTime
    print(runtime)

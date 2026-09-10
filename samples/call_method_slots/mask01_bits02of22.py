# call_method_slots/advanced  granularity=annotation
# mask=516  (2/22 units erased)

"""Microbenchmark for method call overhead on types that define __slots__.

This measures simple method calls for objects with no dicts that are
predictable, do not use varargs or kwargs, and do not use tuple unpacking.
When an object has no __dict__ attribute, the JIT can optimize away most of the
attribute lookup.  This benchmark measures how well it can do that.

bg:
- annotated all parameters + return types
- using Timer
- removed command-line parsing
"""
import __static__
from typing import Any
from __static__ import int64, box
import time
import cinderx.jit
cinderx.jit.compile_after_n_calls(0)

class Foo(object):
    __slots__ = ()

    def foo(self, a: Any, b: int64, c: int64, d: int64) -> None:
        self.bar(int64(a), b, box(c))
        self.bar(int64(a), b, box(c))
        self.bar(int64(a), b, box(c))
        self.bar(int64(a), b, box(c))
        self.bar(int64(a), b, box(c))
        self.bar(int64(a), b, box(c))
        self.bar(int64(a), b, box(c))
        self.bar(int64(a), b, box(c))
        self.bar(int64(a), b, box(c))
        self.bar(int64(a), b, box(c))
        self.bar(int64(a), b, box(c))
        self.bar(int64(a), b, box(c))
        self.bar(int64(a), b, box(c))
        self.bar(int64(a), b, box(c))
        self.bar(int64(a), b, box(c))
        self.bar(int64(a), b, box(c))
        self.bar(int64(a), b, box(c))
        self.bar(int64(a), b, box(c))
        self.bar(int64(a), b, box(c))
        self.bar(int64(a), b, box(c))

    def bar(self, a: int64, b: int64, c: Any) -> None:
        self.baz(a, b)
        self.baz(a, b)
        self.baz(a, b)
        self.baz(a, b)
        self.baz(a, b)
        self.baz(a, b)
        self.baz(a, b)
        self.baz(a, b)
        self.baz(a, b)
        self.baz(a, b)
        self.baz(a, b)
        self.baz(a, b)
        self.baz(a, b)
        self.baz(a, b)
        self.baz(a, b)
        self.baz(a, b)
        self.baz(a, b)
        self.baz(a, b)
        self.baz(a, b)
        self.baz(a, b)

    def baz(self, a: int64, b: int64) -> None:
        self.quux(a)
        self.quux(a)
        self.quux(a)
        self.quux(a)
        self.quux(a)
        self.quux(a)
        self.quux(a)
        self.quux(a)
        self.quux(a)
        self.quux(a)
        self.quux(a)
        self.quux(a)
        self.quux(a)
        self.quux(a)
        self.quux(a)
        self.quux(a)
        self.quux(a)
        self.quux(a)
        self.quux(a)
        self.quux(a)

    def quux(self, a: int64) -> None:
        self.qux()
        self.qux()
        self.qux()
        self.qux()
        self.qux()
        self.qux()
        self.qux()
        self.qux()
        self.qux()
        self.qux()
        self.qux()
        self.qux()
        self.qux()
        self.qux()
        self.qux()
        self.qux()
        self.qux()
        self.qux()
        self.qux()
        self.qux()

    def qux(self) -> None:
        pass

def test_calls() -> None:
    f: Foo = Foo()
    f.foo(1, 2, 3, 4)
    f.foo(1, 2, 3, 4)
    f.foo(1, 2, 3, 4)
    f.foo(1, 2, 3, 4)
    f.foo(1, 2, 3, 4)
    f.foo(1, 2, 3, 4)
    f.foo(1, 2, 3, 4)
    f.foo(1, 2, 3, 4)
    f.foo(1, 2, 3, 4)
    f.foo(1, 2, 3, 4)
    f.foo(1, 2, 3, 4)
    f.foo(1, 2, 3, 4)
    f.foo(1, 2, 3, 4)
    f.foo(1, 2, 3, 4)
    f.foo(1, 2, 3, 4)
    f.foo(1, 2, 3, 4)
    f.foo(1, 2, 3, 4)
    f.foo(1, 2, 3, 4)
    f.foo(1, 2, 3, 4)
    f.foo(1, 2, 3, 4)
    return

def main():
    startTime = time.time()
    test_calls()
    endTime = time.time()
    runtime = endTime - startTime
    print(runtime)
if __name__ == '__main__':
    main()

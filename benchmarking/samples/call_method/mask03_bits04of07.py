# call_method/advanced  granularity=benchmark
# mask=54  (4/7 units erased)

import __static__
from typing import Any
from __static__ import int64, box
import time
import cinderx.jit
cinderx.jit.compile_after_n_calls(0)
'Microbenchmark for method call overhead.\n\nThis measures simple method calls that are predictable, do not use varargs or\nkwargs, and do not use tuple unpacking.\n\nbg:\n- annotated all parameters + return types\n- using Timer\n- removed command-line parsing\n'

class Foo(object):

    def foo(self, a: Any, b: Any, c: Any, d: Any) -> None:
        self.bar(a, b, c)
        self.bar(a, b, c)
        self.bar(a, b, c)
        self.bar(a, b, c)
        self.bar(a, b, c)
        self.bar(a, b, c)
        self.bar(a, b, c)
        self.bar(a, b, c)
        self.bar(a, b, c)
        self.bar(a, b, c)
        self.bar(a, b, c)
        self.bar(a, b, c)
        self.bar(a, b, c)
        self.bar(a, b, c)
        self.bar(a, b, c)
        self.bar(a, b, c)
        self.bar(a, b, c)
        self.bar(a, b, c)
        self.bar(a, b, c)
        self.bar(a, b, c)

    def bar(self, a: Any, b: Any, c: Any) -> None:
        self.baz(int64(a), int64(b))
        self.baz(int64(a), int64(b))
        self.baz(int64(a), int64(b))
        self.baz(int64(a), int64(b))
        self.baz(int64(a), int64(b))
        self.baz(int64(a), int64(b))
        self.baz(int64(a), int64(b))
        self.baz(int64(a), int64(b))
        self.baz(int64(a), int64(b))
        self.baz(int64(a), int64(b))
        self.baz(int64(a), int64(b))
        self.baz(int64(a), int64(b))
        self.baz(int64(a), int64(b))
        self.baz(int64(a), int64(b))
        self.baz(int64(a), int64(b))
        self.baz(int64(a), int64(b))
        self.baz(int64(a), int64(b))
        self.baz(int64(a), int64(b))
        self.baz(int64(a), int64(b))
        self.baz(int64(a), int64(b))

    def baz(self, a: int64, b: int64) -> None:
        self.quux(box(a))
        self.quux(box(a))
        self.quux(box(a))
        self.quux(box(a))
        self.quux(box(a))
        self.quux(box(a))
        self.quux(box(a))
        self.quux(box(a))
        self.quux(box(a))
        self.quux(box(a))
        self.quux(box(a))
        self.quux(box(a))
        self.quux(box(a))
        self.quux(box(a))
        self.quux(box(a))
        self.quux(box(a))
        self.quux(box(a))
        self.quux(box(a))
        self.quux(box(a))
        self.quux(box(a))

    def quux(self, a: Any) -> None:
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
    f: Any = Foo()
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

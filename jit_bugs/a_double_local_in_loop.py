# Bug A -- JIT codegen abort: "incorrect register type."
#
#   JIT: cinderx/Jit/codegen/autogen.h:49 -- Abort
#   incorrect register type.
#   (SIGABRT, rc=-6)
#
# Trigger: a `double` local *declared* inside a loop body. int64 and int are
# fine, the same declaration outside a loop is fine, and it does not matter
# whether the value is ever read. Not the inliner -- --no-inliner still aborts.
# See jit_bug.md.
import __static__
from __static__ import double


def f() -> None:
    for i in range(3):
        m: double = 2.5


f()
print('ok')

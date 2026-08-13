# Bug B -- HIR inliner hands the callee a bogus frame: SIGSEGV, or a wrong
# object where a value should be.
#
#   Segmentation fault (rc=-11), or, depending on the surrounding code:
#     AttributeError: 'int' object has no attribute 'get'
#     AttributeError: 'range_iterator' object has no attribute 'get'
#     cinderx.StaticTypeError: <method> expected 'X' for argument self, got 'tuple'
#
# Trigger: a function that (a) declares a module global with `global`, (b) stores
# to it, (c) returns it, and (d) has a return annotation, called from another
# function the JIT compiles. The global's declared type has to be dynamic, which
# is what `counter = None` *below* the function produces -- above it, the static
# compiler rejects the program with a type mismatch instead.
#
# Goes away with --no-inliner or --no-jit. This is what makes deltablue fail at
# every proportion of typedness. See jit_bug.md.
import __static__


def bump() -> int:
    global counter
    counter = 1
    return counter


def drive() -> None:
    bump()


counter = None
drive()
print('ok', counter)

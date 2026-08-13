"""Execute one benchmark module under Static Python and let it time itself.

static_runner.py imports the module and calls main(), which for most of these
benchmarks runs nothing at all -- the workload and the timing print live in the
`if __name__ == "__main__":` block, and an imported module is never __main__.
typedness_sweep.py hoists that block to module level before writing the file,
so the workload runs during the import and the benchmark's own printed number
comes back on stdout. Nothing here needs to call main().

Usage: python timing_runner.py <module path> [--require-static] [--no-jit]
"""
from importlib import import_module
from os import path
from sys import argv, exit, path as sys_path
import sys

import cinderx.jit
from cinderx.compiler.strict import loader as static_python_loader

import __static__

if '--no-jit' in argv:
    cinderx.jit.disable()
else:
    # --no-inliner is what tells the two JIT bugs in jit_bug.md apart: the
    # inliner one goes away without it, the codegen one does not.
    if '--no-inliner' in argv:
        cinderx.jit.disable_hir_inliner()
    cinderx.jit.compile_after_n_calls(0)
static_python_loader.install()

module_path = argv[1]
require_static = '--require-static' in argv

# Several benchmarks take an iteration count from sys.argv[1]. The runner's own
# arguments would be read as one, so the module gets a bare argv instead.
sys.argv = [module_path]

sys_path.insert(0, path.dirname(path.abspath(module_path)))
module = import_module(path.splitext(path.basename(module_path))[0])

if require_static and not __static__.is_static_module(module):
    print("not statically compiled", file=sys.stderr)
    exit(2)

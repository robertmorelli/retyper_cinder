"""Execute one benchmark module under the Static Python loader.

The loader installs itself as a path hook, so it only ever sees *imported*
modules -- a file run as a script or piped in on stdin is __main__ and gets
plain CPython compilation instead. Everything here exists to make the source
arrive through an import.

Usage: python testing/static_runner.py <module path> [--require-static]
"""
from importlib import import_module
from os import path
from sys import argv, exit, path as sys_path

import cinderx.jit
from cinderx.compiler.strict import loader as static_python_loader

import __static__

cinderx.jit.compile_after_n_calls(0)
static_python_loader.install()

module_path = argv[1]
require_static = '--require-static' in argv
compile_only = '--compile-only' in argv

# Several benchmarks read an iteration count from sys.argv[1]. The runner's own
# arguments would be parsed as one -- int('/var/folders/.../bench_module.py') --
# so the module has to see a bare argv.
import sys
sys.argv = [module_path]

sys_path.insert(0, path.dirname(path.abspath(module_path)))
module = import_module(path.splitext(path.basename(module_path))[0])

if require_static and not __static__.is_static_module(module):
    print("not statically compiled", file=__import__('sys').stderr)
    exit(2)

main = getattr(module, 'main', None)
if main is not None and not compile_only:
    main()

"""python run_plain.py <file.py> [--no-inliner] -- run it as a plain script.

The counterpart to run_compiled.py: same JIT settings, no static compilation, so
a pair of runs differs only in whether the module went through the Static Python
loader.
"""
import runpy
import sys
from os import path

import cinderx.jit

if '--no-inliner' in sys.argv:
    cinderx.jit.disable_hir_inliner()
cinderx.jit.compile_after_n_calls(0)

file = path.abspath(sys.argv[1])
sys.argv = [file]
runpy.run_path(file, run_name='__main__')

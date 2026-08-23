"""Run a file as a plain script, optionally without the HIR inliner.

The counterpart to `run_compiled.py` uses the same JIT settings without Static
Python compilation.
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

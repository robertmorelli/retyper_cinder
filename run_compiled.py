"""python run_compiled.py <file.py> -- run it statically compiled, as __main__."""
import importlib.util
import sys
from os import path

import cinderx.jit
from cinderx.compiler.strict.loader import StrictSourceFileLoader, install

if '--no-inliner' in sys.argv:
    cinderx.jit.disable_hir_inliner()
cinderx.jit.compile_after_n_calls(0)
install()

file = path.abspath(sys.argv[1])
sys.argv = [file]
sys.path.insert(0, path.dirname(file))

loader = StrictSourceFileLoader('__main__', file)
spec = importlib.util.spec_from_file_location('__main__', file, loader=loader)
module = importlib.util.module_from_spec(spec)
sys.modules['__main__'] = module
loader.exec_module(module)

"""Run a benchmark module repeatedly through CinderX's static compiler.

This is intended for hardware-counter profiling: module import and JIT warmup
happen before the fixed-work measurement loop.
"""
import importlib.util
from os import path
import sys
import time

import cinderx.jit
from cinderx.compiler.strict.loader import StrictSourceFileLoader, install


def main() -> None:
    module_path = path.abspath(sys.argv[1])
    repetitions = int(sys.argv[2])
    start_delay = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    function_name = sys.argv[4] if len(sys.argv) > 4 else "main"
    sys.argv = [module_path]

    cinderx.jit.compile_after_n_calls(0)
    install()

    module_name = "__main__"
    sys.path.insert(0, path.dirname(module_path))
    loader = StrictSourceFileLoader(module_name, module_path)
    spec = importlib.util.spec_from_file_location(
        module_name,
        module_path,
        loader=loader,
    )
    if spec is None:
        raise RuntimeError("could not create benchmark module spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    loader.exec_module(module)
    module = sys.modules[module_name]

    benchmark = getattr(module, function_name)
    # Warm the exact function before the fixed-work region.
    benchmark()
    if start_delay:
        time.sleep(start_delay)

    iteration = 0
    while iteration < repetitions:
        benchmark()
        iteration += 1


if __name__ == "__main__":
    main()

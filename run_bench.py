from os import path
from subprocess import run as run_subprocess
from sys import executable
from tempfile import TemporaryDirectory

from detyper import detype

RUNNER = path.join(path.dirname(path.abspath(__file__)), "static_runner.py")

def _tag(label, mask):
    if mask is None:
        return label
    return f"{label} mask={mask}" if label else f"mask={mask}"

def run(bench, variant, source, mask, label, failures, bench_mode=False,
        require_static=False):
    try:
        detyped = source if mask is None else detype(source, mask=mask, bench=bench_mode)
        # Static Python only compiles imported modules, so the source has to be
        # written out and imported rather than piped to the interpreter.
        with TemporaryDirectory() as tmp:
            module_path = path.join(tmp, "bench_module.py")
            with open(module_path, "w") as f:
                f.write(detyped)
            cmd = [executable, RUNNER, module_path]
            if require_static:
                cmd.append("--require-static")
            r = run_subprocess(cmd, capture_output=True, text=True)
        if r.returncode:
            last = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "exit %d" % r.returncode
            failures.append(f"{bench}/{variant} {_tag(label, mask)}  {last}")
    except Exception as e:
        failures.append(f"{bench}/{variant} {_tag(label, mask)}  {type(e).__name__}: {e}")

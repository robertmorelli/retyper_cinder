from subprocess import run
from list_benchmarks import get_bench_list
from load_source import load_bench
from annotator import annotate_source

failures = []
for bench, variant, path in get_bench_list():
    try:
        annotated = annotate_source(load_bench(bench, variant))
        r = run(["python3", "-"], input=annotated,
                           capture_output=True, text=True)
        if r.returncode:
            last = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "exit %d" % r.returncode
            failures.append(f"{bench}/{variant}  {last}")
    except Exception as e:
        failures.append(f"{bench}/{variant}  {type(e).__name__}: {e}")

print("\n".join(failures) if failures else "all benchmarks typecheck after annotation")

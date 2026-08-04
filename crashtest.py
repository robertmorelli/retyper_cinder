from list_benchmarks import get_bench_list
from load_source import load_bench
from detyper import detype

failures = []
for bench, variant, path in get_bench_list():
    try:
        detype(load_bench(bench, variant))
    except Exception as e:
        failures.append(f"{bench}/{variant}  {type(e).__name__}: {e}")

print("\n".join(failures))

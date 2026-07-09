from json import load
from load_source import load_bench
from detyper import detype

with open("data/benchmark_locations.json") as f:
    sources = load(f)

failures = []
for bench, variants in sources.items():
    for variant in variants:
        try:
            detype(load_bench(bench, variant))
        except Exception as e:
            failures.append(f"{bench}/{variant}  {type(e).__name__}: {e}")

print("\n".join(failures))

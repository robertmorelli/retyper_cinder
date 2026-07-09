from json import load
from random import sample, randint
from load_source import load_bench
from detyper import detype
from root_count import count_roots

with open("data/benchmark_locations.json") as f:
    sources = load(f)

failures = []
for bench, variants in sources.items():
    for variant in variants:
        if variant == "untyped":
            continue
        source = load_bench(bench, variant)
        n = count_roots(source)
        for _ in range(5):
            sample = sample(range(n), randint(1, n))
            mask = sum(1 << i for i in sample)
            try:
                detype(source, mask=mask)
            except Exception as e:
                failures.append(f"{bench}/{variant} mask={mask}  {type(e).__name__}: {e}")

print("\n".join(failures))

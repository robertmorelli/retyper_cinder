from random import sample, randint
from list_benchmarks import get_bench_list
from load_source import load_bench
from root_count import count_bench_roots
from run_bench import run

failures = []
for bench, variant, path in get_bench_list():
    if variant == "untyped":
        continue
    source = load_bench(bench, variant)
    n = count_bench_roots(source)
    if n == 0:
        continue
    for _ in range(500):
        picks = sample(range(n), randint(1, n))
        mask = sum(1 << i for i in picks)
        run(bench, variant, source, mask, "", failures, bench_mode=True)

print("\n".join(failures))

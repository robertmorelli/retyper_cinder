from list_benchmarks import get_bench_list
from load_source import load_bench
from root_count import count_roots
from run_bench import run

failures = []
for bench, variant, path in get_bench_list():
    if variant == "untyped":
        continue
    source = load_bench(bench, variant)
    n = count_roots(source)
    full = (1 << n) - 1

    run(bench, variant, source, full, "max", failures)
    run(bench, variant, source, None, "none", failures)

    for i in range(n):
        run(bench, variant, source, 1 << i, "single", failures)
    for i in range(n):
        run(bench, variant, source, full ^ (1 << i), "inverse-single", failures)

print("\n".join(failures))

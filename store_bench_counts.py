# TODO: rewrite
from json import dump
from list_benchmarks import get_bench_list
from load_source import load_bench
from ast import parse
from get_ast_data import get_ast_data

counts = {}
for bench, variant, path in get_bench_list():
    counts.setdefault(bench, {})
    try:
        anno_roots = get_ast_data(parse(load_bench(bench, variant)))[12]
        n = len(anno_roots)
    except Exception:
        n = 0  # untyped sources don't bind under Static Python
    counts[bench][variant] = n
    print(f"{bench}/{variant}: {n}")

with open("data/counts.generated.json", "w") as f:
    dump(counts, f, indent=2)
    f.write("\n")

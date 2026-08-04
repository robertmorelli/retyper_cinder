from json import load

def get_bench_list():
    with open("data/benchmark_locations.json") as f:
        sources = load(f)
    return [(bench, variant, path)
            for bench, variants in sources.items()
            for variant, path in variants.items()]

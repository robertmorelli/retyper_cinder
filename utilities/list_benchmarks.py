from json import load
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "data" / "benchmark_locations.json"

def get_benchmark_locations():
    with CONFIG.open() as file:
        return load(file)

def get_bench_list():
    sources = get_benchmark_locations()
    return [(bench, variant, path)
            for bench, variants in sources.items()
            for variant, path in variants.items()]

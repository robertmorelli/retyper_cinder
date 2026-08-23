from json import load
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "data" / "benchmark_locations.json"

def get_bench_list():
    with CONFIG.open() as f:
        sources = load(f)
    return [(bench, variant, path)
            for bench, variants in sources.items()
            for variant, path in variants.items()]

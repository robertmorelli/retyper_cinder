from json import load
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path(__file__).resolve().parent.parent / "data" / "benchmark_locations.json"

def load_bench(bench, variant):
    with CONFIG.open() as f:
        sources = load(f)
    return (ROOT / sources[bench][variant]).read_text()

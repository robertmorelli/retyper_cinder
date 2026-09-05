from ast import unparse
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from utilities.list_benchmarks import get_benchmark_locations
from src import detype

ROOT = Path(__file__).resolve().parents[1]

def load_bench(bench, variant):
    sources = get_benchmark_locations()
    return (ROOT / sources[bench][variant]).read_text()


def detyped_source(source, mask, granularity):
    if mask == 0:
        return source
    return unparse(detype(
        source,
        mask=mask,
        bench=granularity in {"function", "benchmark"},
    ))


def detyped_benchmark_source(benchmark, variant, mask, granularity):
    return detyped_source(
        load_bench(benchmark, variant), mask, granularity
    )


@contextmanager
def temporary_module(source):
    with TemporaryDirectory() as directory:
        module = Path(directory) / "bench_module.py"
        module.write_text(source)
        yield module

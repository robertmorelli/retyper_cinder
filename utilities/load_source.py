from ast import unparse
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from utilities.list_benchmarks import get_benchmark_locations
from utilities.experiments import granularity_map
from src import detype

ROOT = Path(__file__).resolve().parents[1]

def load_bench(bench, variant):
    sources = get_benchmark_locations()
    return (ROOT / sources[bench][variant]).read_text()


def resolve_granularity(granularity, benchmark=None):
    if granularity == "benchmark":
        if benchmark is None:
            raise ValueError("benchmark granularity requires a benchmark name")
        granularity = granularity_map()[benchmark]
    if granularity not in {"annotation", "function"}:
        raise ValueError(f"invalid granularity: {granularity}")
    return granularity


def detyped_source(source, mask, granularity, benchmark=None):
    granularity = resolve_granularity(granularity, benchmark)
    if mask == 0:
        return source
    return unparse(detype(
        source,
        mask=mask,
        bench=granularity == "function",
    ))


def detyped_benchmark_source(benchmark, variant, mask, granularity):
    return detyped_source(
        load_bench(benchmark, variant), mask, granularity, benchmark
    )


@contextmanager
def temporary_module(source):
    with TemporaryDirectory() as directory:
        module = Path(directory) / "bench_module.py"
        module.write_text(source)
        yield module

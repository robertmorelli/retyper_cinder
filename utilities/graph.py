from ast import parse
from src.cinderx_binding import get_ast_data
from src.type_graph import TypeGraph
from utilities.load_source import load_bench

def graph(source):
    return TypeGraph(get_ast_data(parse(source)))

def count_units(source, granularity="annotation"):
    return len(graph(source).units(granularity))


def count_benchmark_units(benchmark, variant, granularity):
    """Count selectable units, returning zero when a source cannot bind."""
    graph_granularity = (
        "benchmark" if granularity in {"function", "benchmark"}
        else "annotation"
    )
    try:
        return count_units(
            load_bench(benchmark, variant), graph_granularity
        )
    except Exception:
        return 0


def count_roots(source):
    return len(graph(source).roots)


def count_function_units(source):
    return count_units(source, "benchmark")

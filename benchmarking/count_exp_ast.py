"""Count non-import AST nodes for every mask in an experiment plan.

Workers may process disjoint round-robin shards.  Shard files can later be
merged into a complete JSON document with the same benchmark/variant/mask
shape as ``sample_results.json``.
"""

from argparse import ArgumentParser
from ast import parse
from concurrent.futures import ProcessPoolExecutor
from os import cpu_count
from pathlib import Path
from sys import path as import_path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in import_path:
    import_path.insert(0, str(ROOT))

from utilities.count_ast_nodes_tool import count_nodes_excluding_imports
from utilities.experiments import (
    experiment_for,
    load_experiment,
    read_json,
    validate_experiment,
    write_json_atomic,
)
from utilities.load_source import detyped_benchmark_source


def planned_jobs(experiment):
    return [
        (benchmark, variant, mask, experiment.granularities[benchmark])
        for benchmark, variants in experiment.plan.items()
        for variant, masks in variants.items()
        for mask in masks
    ]


def count_mask(job):
    benchmark, variant, mask, granularity = job
    source = detyped_benchmark_source(benchmark, variant, mask, granularity)
    count = count_nodes_excluding_imports(parse(source))
    return benchmark, variant, str(mask), count


def count_shard(experiment, shard_index, shard_count, workers):
    jobs = planned_jobs(experiment)[shard_index::shard_count]
    result = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for benchmark, variant, mask, count in pool.map(
            count_mask, jobs, chunksize=16
        ):
            result.setdefault(benchmark, {}).setdefault(variant, {})[mask] = count
    return result, len(jobs)


def merge_shards(experiment, paths):
    merged = {
        benchmark: {variant: {} for variant in variants}
        for benchmark, variants in experiment.plan.items()
    }
    for path in paths:
        shard = read_json(path)
        for benchmark, variants in shard.items():
            for variant, masks in variants.items():
                for mask, count in masks.items():
                    if mask in merged[benchmark][variant]:
                        raise ValueError(
                            f"duplicate AST count for {benchmark}/{variant}/{mask}"
                        )
                    merged[benchmark][variant][mask] = count
    for benchmark, variants in experiment.plan.items():
        for variant, masks in variants.items():
            expected = {str(mask) for mask in masks}
            actual = set(merged[benchmark][variant])
            if actual != expected:
                raise ValueError(
                    f"AST masks differ for {benchmark}/{variant}: "
                    f"missing {len(expected - actual)}, extra {len(actual - expected)}"
                )
    return merged


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("experiment")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shard-index", type=int)
    parser.add_argument("--shard-count", type=int)
    parser.add_argument("--workers", type=int, default=cpu_count() or 1)
    parser.add_argument("--merge", nargs="+", type=Path)
    args = parser.parse_args()

    experiment = load_experiment(experiment_for(args.experiment))
    validate_experiment(experiment)
    if args.merge:
        result = merge_shards(experiment, args.merge)
        count = sum(
            len(masks) for variants in result.values() for masks in variants.values()
        )
    else:
        if args.shard_index is None or args.shard_count is None:
            parser.error("counting requires --shard-index and --shard-count")
        if not 0 <= args.shard_index < args.shard_count:
            parser.error("shard index must satisfy 0 <= index < count")
        result, count = count_shard(
            experiment, args.shard_index, args.shard_count, args.workers
        )
    write_json_atomic(args.output, result)
    print(f"{args.output}: {count} masks")


if __name__ == "__main__":
    main()

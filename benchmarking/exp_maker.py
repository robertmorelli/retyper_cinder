"""Create a timestamped benchmark experiment plan.

Usage:
    python benchmarking/exp_maker.py MAX_MASKS_PER_LEVEL

For every benchmark and each of its advanced, shallow, and untyped variants,
this samples up to the requested number of unique masks at every possible
detype level.  A level is the number of mask bits set.  The fully typed and
fully detyped levels consequently contain one mask each.
"""

from argparse import ArgumentParser
from pathlib import Path
from random import SystemRandom
from sys import path as import_path


ROOT = Path(__file__).resolve().parent.parent
VARIANTS = ("advanced", "shallow", "untyped")
SKIP = {"scratch"}

for directory in (ROOT / "src", ROOT):
    directory_string = str(directory)
    if directory_string not in import_path:
        import_path.insert(0, directory_string)

from utilities.experiments import (
    create_experiment_directory,
    granularity_map,
    initialize_experiment_state,
    save_new_experiment,
)
from utilities.graph import count_benchmark_units
from utilities.list_benchmarks import get_benchmark_locations


def load_benchmark_definitions():
    """Return the benchmarks and the information needed to plan them."""
    locations = get_benchmark_locations()
    granularities = granularity_map()
    return [
        {
            "name": name,
            "variants": tuple(
                variant for variant in VARIANTS if variant in locations[name]
            ),
            "granularity": granularities[name],
        }
        for name in locations
        if name not in SKIP
    ]


def find_supported_variants(benchmark):
    return benchmark["variants"]


def identify_detyping_levels(benchmark, variant):
    units = count_benchmark_units(
        benchmark["name"], variant, benchmark["granularity"]
    )
    return range(units + 1)


def sample_level_masks(unit_total, level, limit, rng):
    """Sample unique masks having exactly ``level`` of ``unit_total`` bits."""
    if level == 0:
        return [0]
    if level == unit_total:
        return [(1 << unit_total) - 1]

    masks = set()
    while len(masks) < limit:
        bits = rng.sample(range(unit_total), level)
        masks.add(sum(1 << bit for bit in bits))
    return sorted(masks)


def sample_masks_across_levels(detyping_levels, limit, rng=None):
    """Sample up to ``limit`` masks at each detyping level."""
    rng = rng or SystemRandom()
    unit_total = len(detyping_levels) - 1
    masks = []
    for level in detyping_levels:
        # Near the ends there may be fewer than `limit` possible masks.  The
        # only such case needed with today's limit sizes is the endpoints,
        # but computing the exact count keeps the contract true for any input.
        possible = combinations_count(unit_total, level)
        masks.extend(sample_level_masks(
            unit_total, level, min(limit, possible), rng
        ))
    return masks


def combinations_count(n, k):
    """Compute n choose k without requiring a particular Python version."""
    k = min(k, n - k)
    result = 1
    for divisor in range(1, k + 1):
        result = result * (n - k + divisor) // divisor
    return result


def add_to_plan(plan, benchmark, variant, masks):
    plan.setdefault(benchmark["name"], {})[variant] = masks


def save_experiment(experiment, plan, state):
    save_new_experiment(
        experiment, plan, state["typechecks"], state["results"]
    )


def make_experiment(max_masks_per_level):
    benchmarks = load_benchmark_definitions()
    experiment_plan = {}

    for benchmark in benchmarks:
        variants = find_supported_variants(benchmark)

        for variant in variants:
            detyping_levels = identify_detyping_levels(benchmark, variant)
            masks = sample_masks_across_levels(
                detyping_levels, max_masks_per_level
            )
            add_to_plan(experiment_plan, benchmark, variant, masks)

    experiment_state = initialize_experiment_state(experiment_plan)
    experiment = create_experiment_directory()
    save_experiment(experiment, experiment_plan, experiment_state)

    return experiment


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "max_masks_per_level",
        type=int,
        help="maximum number of unique masks sampled at each detype level",
    )
    args = parser.parse_args()
    if args.max_masks_per_level < 1:
        parser.error("MAX_MASKS_PER_LEVEL must be at least 1")

    experiment = make_experiment(args.max_masks_per_level)
    print(experiment)


if __name__ == "__main__":
    main()

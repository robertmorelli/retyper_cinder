"""Estimate how long a complete benchmark experiment will take.

Usage:
    python3 exp_time_tool.py N [--workers WORKERS]

N is the maximum number of masks sampled at each detype level, matching the
argument accepted by ``benchmarking/exp_maker.py``.  The estimate assumes each
mask takes 29.4 seconds of worker time, based on the first distributed
Python 3.14+CinderX run.
"""

from argparse import ArgumentParser

from benchmarking.exp_maker import (
    combinations_count,
    find_supported_variants,
    identify_detyping_levels,
    load_benchmark_definitions,
)


SECONDS_PER_MASK = 29.4
SECONDS_PER_HOUR = 60 * 60


def masks_for_levels(detyping_levels, masks_per_level):
    """Count the masks exp_maker would sample across these detype levels."""
    unit_total = len(detyping_levels) - 1
    return sum(
        min(masks_per_level, combinations_count(unit_total, level))
        for level in detyping_levels
    )


def benchmark_mask_counts(masks_per_level):
    """Return each benchmark's mask count across all supported variants."""
    counts = {}
    for benchmark in load_benchmark_definitions():
        total = 0
        for variant in find_supported_variants(benchmark):
            levels = identify_detyping_levels(benchmark, variant)
            total += masks_for_levels(levels, masks_per_level)
        counts[benchmark["name"]] = total
    return counts


def experiment_mask_count(masks_per_level):
    """Return the total number of masks in a complete experiment plan."""
    return sum(benchmark_mask_counts(masks_per_level).values())


def estimated_hours(masks_per_level, workers=1):
    masks = experiment_mask_count(masks_per_level)
    return masks * SECONDS_PER_MASK / SECONDS_PER_HOUR / workers


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "N",
        type=int,
        help="maximum number of masks sampled at each detype level",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="number of timing workers (default: 1)",
    )
    args = parser.parse_args()
    if args.N < 1:
        parser.error("N must be at least 1")
    if args.workers < 1:
        parser.error("--workers must be at least 1")

    benchmark_counts = benchmark_mask_counts(args.N)
    masks = sum(benchmark_counts.values())
    hours = masks * SECONDS_PER_MASK / SECONDS_PER_HOUR / args.workers

    print("Masks per benchmark:")
    width = max(len(name) for name in benchmark_counts)
    for benchmark, count in benchmark_counts.items():
        print(f"  {benchmark:<{width}}  {count:,}")
    print(f"  {'TOTAL':<{width}}  {masks:,}")
    print()
    print(f"Estimated experiment time with {args.workers} worker(s): {hours:.2f} hours")
    print(
        f"({masks:,} masks x {SECONDS_PER_MASK:g} seconds per mask "
        f"/ {args.workers} workers)"
    )


if __name__ == "__main__":
    main()

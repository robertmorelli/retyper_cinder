"""Grow an existing experiment to a target number of masks per detype level.

Usage:
    python benchmarking/grow_exp.py MAX_MASKS_PER_LEVEL [TIMESTAMP]

TIMESTAMP accepts the suffix or full exp_<timestamp> directory name and defaults
to the latest experiment. Existing masks, typechecks, and timings are preserved.
"""

from argparse import ArgumentParser
from itertools import combinations
from math import comb
from pathlib import Path
from random import SystemRandom
from sys import path as import_path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in import_path:
    import_path.insert(0, str(ROOT))

from utilities.experiments import (
    experiment_for,
    load_experiment,
    validate_experiment,
    write_json_atomic,
)


def additional_level_masks(unit_total, level, existing, target, rng):
    """Sample without duplicates, enumerating small, nearly full levels."""
    possible = comb(unit_total, level)
    needed = min(target, possible) - len(existing)
    if needed <= 0:
        return []
    if possible <= 2 * (len(existing) + needed):
        available = (
            sum(1 << bit for bit in bits)
            for bits in combinations(range(unit_total), level)
        )
        return sorted(rng.sample(
            [mask for mask in available if mask not in existing], needed
        ))
    added = set()
    while len(added) < needed:
        mask = sum(1 << bit for bit in rng.sample(range(unit_total), level))
        if mask not in existing:
            added.add(mask)
    return sorted(added)


def grow_experiment(experiment_path, max_masks_per_level, rng=None):
    if max_masks_per_level < 1:
        raise ValueError("MAX_MASKS_PER_LEVEL must be at least 1")
    experiment = load_experiment(experiment_path)
    validate_experiment(experiment)
    rng = rng or SystemRandom()
    added_count = 0
    for benchmark, variants in experiment.plan.items():
        for variant, masks in variants.items():
            label = f"{benchmark}/{variant}"
            if not masks or any(type(mask) is not int or mask < 0 for mask in masks):
                raise ValueError(f"invalid masks for {label}")
            if len(set(masks)) != len(masks):
                raise ValueError(f"duplicate masks for {label}")
            # exp_maker always includes both endpoints. Recover the original
            # width from the plan so changes to benchmark sources cannot alter it.
            unit_total = max(masks).bit_length()
            if 0 not in masks or (1 << unit_total) - 1 not in masks:
                raise ValueError(f"missing endpoint masks for {label}")
            levels = [set() for _ in range(unit_total + 1)]
            for mask in masks:
                levels[bin(mask).count("1")].add(mask)
            additions = []
            for level, existing in enumerate(levels):
                additions.extend(additional_level_masks(
                    unit_total, level, existing, max_masks_per_level, rng
                ))
            masks.extend(additions)
            for mask in additions:
                experiment.typechecks[benchmark][variant][str(mask)] = False
                experiment.results[benchmark][variant][str(mask)] = []
            added_count += len(additions)

    validate_experiment(experiment)
    if added_count:
        write_json_atomic(experiment.typechecks_path, experiment.typechecks)
        write_json_atomic(experiment.results_path, experiment.results)
        write_json_atomic(experiment.path / "sample_plan.json", experiment.plan)
    return added_count


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("max_masks_per_level", type=int,
                        help="target number of unique masks per detype level")
    parser.add_argument("timestamp", nargs="?",
                        help="experiment timestamp or exp_<timestamp> name")
    args = parser.parse_args()
    try:
        experiment = experiment_for(args.timestamp)
        added = grow_experiment(experiment, args.max_masks_per_level)
    except (OSError, ValueError) as exception:
        parser.error(str(exception))
    print(f"{experiment}: added {added} masks")


if __name__ == "__main__":
    main()

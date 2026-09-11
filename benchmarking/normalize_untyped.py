"""Collapse every untyped experiment variant to its unchanged mask-zero baseline."""

from argparse import ArgumentParser
from pathlib import Path
from sys import path as import_path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in import_path:
    import_path.insert(0, str(ROOT))

from utilities.experiments import experiment_for, load_experiment, write_json_atomic


def normalize(experiment_path):
    experiment = load_experiment(experiment_path)
    removed = 0
    for benchmark, variants in experiment.plan.items():
        if "untyped" not in variants:
            continue
        masks = variants["untyped"]
        if 0 not in masks:
            raise ValueError(f"{benchmark}/untyped is missing mask 0")
        removed += len(masks) - 1
        variants["untyped"] = [0]
        experiment.typechecks[benchmark]["untyped"] = {
            "0": experiment.typechecks[benchmark]["untyped"]["0"]
        }
        experiment.results[benchmark]["untyped"] = {
            "0": experiment.results[benchmark]["untyped"]["0"]
        }
    write_json_atomic(experiment.path / "sample_plan.json", experiment.plan)
    write_json_atomic(experiment.typechecks_path, experiment.typechecks)
    write_json_atomic(experiment.results_path, experiment.results)
    return removed


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("experiment")
    args = parser.parse_args()
    try:
        experiment = experiment_for(args.experiment)
        removed = normalize(experiment)
    except (OSError, ValueError) as exception:
        parser.error(str(exception))
    print(f"{experiment}: removed {removed} redundant untyped masks")


if __name__ == "__main__":
    main()

"""Clear all collected data from an experiment while preserving its plan."""

from argparse import ArgumentParser
from pathlib import Path
from sys import path as import_path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in import_path:
    import_path.insert(0, str(ROOT))

from utilities.experiments import (
    experiment_for,
    initialize_experiment_state,
    load_experiment,
    write_json_atomic,
)


def reset_experiment(value):
    experiment = load_experiment(experiment_for(value))
    state = initialize_experiment_state(experiment.plan)
    write_json_atomic(experiment.typechecks_path, state["typechecks"])
    write_json_atomic(experiment.results_path, state["results"])
    return experiment.path


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("experiment")
    args = parser.parse_args()
    try:
        path = reset_experiment(args.experiment)
    except (OSError, RuntimeError, ValueError) as exception:
        parser.error(str(exception))
    print(path)


if __name__ == "__main__":
    main()

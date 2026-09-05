"""Storage and validation for generated benchmark experiments."""

from dataclasses import dataclass
from datetime import datetime
from json import dump, load
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = Path(__file__).resolve().parent.parent / "data"


def read_json(path):
    with Path(path).open() as file:
        return load(file)


def write_json_new(path, value):
    with Path(path).open("x") as file:
        dump(value, file, indent=2)
        file.write("\n")


def write_json_atomic(path, value):
    path = Path(path)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w") as file:
        dump(value, file, indent=2)
        file.write("\n")
    temporary.replace(path)


def granularity_map():
    return read_json(DATA / "benchmark_granularity.json")


def experiment_for(timestamp=None):
    """Resolve an explicit experiment timestamp or select the latest one."""
    if timestamp:
        name = timestamp if timestamp.startswith("exp_") else f"exp_{timestamp}"
        if Path(name).name != name:
            raise ValueError("timestamp must not contain a directory path")
        experiment = ROOT / name
        if not experiment.is_dir():
            raise FileNotFoundError(f"experiment does not exist: {experiment}")
        return experiment

    candidates = [
        path for path in ROOT.glob("exp_*")
        if path.is_dir()
        and (path / "sample_plan.json").is_file()
        and (path / "sample_tc.json").is_file()
    ]
    if not candidates:
        raise FileNotFoundError("no generated experiments found")
    return max(candidates, key=lambda path: path.name)


def create_experiment_directory():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    experiment = ROOT / f"exp_{timestamp}"
    experiment.mkdir()
    return experiment


def save_new_experiment(experiment, plan, typechecks, results):
    write_json_new(experiment / "sample_plan.json", plan)
    write_json_new(experiment / "sample_tc.json", typechecks)
    write_json_new(experiment / "sample_results.json", results)


def initialize_experiment_state(plan):
    def initialize_masks(value_factory):
        return {
            benchmark: {
                variant: {
                    str(mask): value_factory() for mask in masks
                }
                for variant, masks in variants.items()
            }
            for benchmark, variants in plan.items()
        }

    return {
        "typechecks": initialize_masks(lambda: False),
        "results": initialize_masks(list),
    }


@dataclass
class Experiment:
    path: Path
    plan: dict
    typechecks: dict
    results: dict
    granularities: dict

    @property
    def typechecks_path(self):
        return self.path / "sample_tc.json"

    @property
    def results_path(self):
        return self.path / "sample_results.json"

    def typechecked_masks(self, benchmark, variant):
        return [
            mask for mask in self.plan[benchmark][variant]
            if self.typechecks[benchmark][variant][str(mask)]
        ]

    def unchecked_masks(self, benchmark, variant):
        return [
            int(mask)
            for mask, valid in self.typechecks[benchmark][variant].items()
            if not valid
        ]

    def mark_typechecked(self, benchmark, variant, mask):
        self.typechecks[benchmark][variant][str(mask)] = True

    def samples_for(self, benchmark, variant, mask):
        return self.results[benchmark][variant][str(mask)]

    def save_typechecks(self):
        write_json_atomic(self.typechecks_path, self.typechecks)

    def save_results(self):
        write_json_atomic(self.results_path, self.results)


def load_experiment(path=None, include_typechecks=True):
    path = experiment_for() if path is None else Path(path)
    return Experiment(
        path=path,
        plan=read_json(path / "sample_plan.json"),
        typechecks=(
            read_json(path / "sample_tc.json") if include_typechecks else None
        ),
        results=read_json(path / "sample_results.json"),
        granularities=granularity_map(),
    )


def validate_experiment(
    experiment,
    benchmark=None,
    required_variants=(),
    include_typechecks=True,
):
    plan = experiment.plan
    typechecks = experiment.typechecks
    results = experiment.results

    if include_typechecks and plan.keys() != typechecks.keys():
        raise ValueError("sample_plan.json and sample_tc.json benchmarks differ")
    if plan.keys() != results.keys():
        raise ValueError("sample_plan.json and sample_results.json benchmarks differ")
    if benchmark is not None and benchmark not in plan:
        choices = ", ".join(plan)
        raise ValueError(f"unknown benchmark {benchmark!r}; choose from: {choices}")

    benchmarks = [benchmark] if benchmark is not None else plan
    for name in benchmarks:
        if name not in experiment.granularities:
            raise ValueError(f"missing granularity for benchmark: {name}")
        if include_typechecks and plan[name].keys() != typechecks[name].keys():
            raise ValueError(
                f"sample_plan.json and sample_tc.json variants differ for {name}"
            )
        if plan[name].keys() != results[name].keys():
            raise ValueError(
                f"sample_plan.json and sample_results.json variants differ for {name}"
            )
        for variant in required_variants:
            if variant not in plan[name]:
                raise ValueError(f"{name} has no {variant} plan")
        for variant, masks in plan[name].items():
            expected = [str(mask) for mask in masks]
            if (
                include_typechecks
                and list(typechecks[name][variant]) != expected
            ):
                raise ValueError(f"typecheck masks differ for {name}/{variant}")
            if list(results[name][variant]) != expected:
                raise ValueError(f"result masks differ for {name}/{variant}")

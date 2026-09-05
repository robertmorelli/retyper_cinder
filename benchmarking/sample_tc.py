"""Typecheck the masks in a generated experiment.

Usage:
    python benchmarking/sample_tc.py [TIMESTAMP]

TIMESTAMP may be either the suffix printed by ``exp_maker.py`` or the complete
``exp_<timestamp>`` directory name.  When it is omitted, the latest experiment
is used.  Successful masks are changed from false to true in sample_tc.json;
failed masks remain false.
"""

from argparse import ArgumentParser
from concurrent.futures import ProcessPoolExecutor, as_completed
from os import cpu_count
from pathlib import Path
from subprocess import run
from sys import path as import_path, stderr


ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "testing" / "static_runner.py"
TIMEOUT_SECONDS = 120

for directory in (ROOT / "src", ROOT):
    directory_string = str(directory)
    if directory_string not in import_path:
        import_path.insert(0, directory_string)

from utilities.experiments import (
    experiment_for,
    load_experiment,
    validate_experiment,
)
from utilities.load_source import (
    detyped_benchmark_source,
    temporary_module,
)
from utilities.static_runtime import static_python


def compile_strict(source, require_static):
    """Return whether source imports successfully through the strict loader."""
    with temporary_module(source) as module:
        command = [
            static_python(),
            str(RUNNER),
            str(module),
            "--compile-only",
        ]
        if require_static:
            command.append("--require-static")
        completed = run(
            command,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    if completed.returncode == 0:
        return True, ""
    message = completed.stderr.strip().splitlines()
    return False, message[-1] if message else f"exit code {completed.returncode}"


def typecheck(job):
    benchmark, variant, mask, granularity = job
    try:
        output = detyped_benchmark_source(
            benchmark, variant, mask, granularity
        )
        valid, error = compile_strict(output, require_static=variant != "untyped")
    except Exception as exception:
        valid = False
        error = f"{type(exception).__name__}: {exception}"
    return benchmark, variant, mask, valid, error


def make_typecheck_jobs(experiment):
    validate_experiment(experiment)
    jobs = []
    for benchmark, variants in experiment.plan.items():
        for variant in variants:
            jobs.extend(
                (
                    benchmark,
                    variant,
                    mask,
                    experiment.granularities[benchmark],
                )
                for mask in experiment.unchecked_masks(benchmark, variant)
            )
    return jobs


def run_experiment(experiment_path):
    experiment = load_experiment(experiment_path)
    jobs = make_typecheck_jobs(experiment)
    if not jobs:
        print(f"all masks already typecheck in {experiment.path.name}")
        return 0, 0

    runtime = static_python()
    workers = cpu_count() or 1
    print(
        f"typechecking {len(jobs)} masks from {experiment.path.name} "
        f"with {workers} workers using {runtime}",
        file=stderr,
    )
    completed_count = valid_count = 0
    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(typecheck, job) for job in jobs]
            for future in as_completed(futures):
                benchmark, variant, mask, valid, error = future.result()
                completed_count += 1
                if valid:
                    experiment.mark_typechecked(benchmark, variant, mask)
                    valid_count += 1
                elif error:
                    print(
                        f"{benchmark}/{variant} mask={mask}: {error}",
                        file=stderr,
                    )

                if completed_count % 25 == 0:
                    experiment.save_typechecks()
                if completed_count % 100 == 0 or completed_count == len(jobs):
                    print(
                        f"{completed_count}/{len(jobs)} checked; "
                        f"{valid_count} newly valid",
                        file=stderr,
                    )
    finally:
        experiment.save_typechecks()

    return valid_count, len(jobs)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "timestamp",
        nargs="?",
        help="experiment timestamp or exp_<timestamp> directory name",
    )
    args = parser.parse_args()
    try:
        experiment = experiment_for(args.timestamp)
        valid, checked = run_experiment(experiment)
    except (FileNotFoundError, RuntimeError, ValueError) as exception:
        parser.error(str(exception))
    print(f"{experiment}: {valid}/{checked} newly typechecked")


if __name__ == "__main__":
    main()

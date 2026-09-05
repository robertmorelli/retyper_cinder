"""Run a capped set of masks for one benchmark in the latest experiment.

Usage:
    python benchmarking/run_exp.py BENCHMARK MAX_MASKS

MAX_MASKS limits the masks run for each of the advanced, shallow, and untyped
variants; it does not limit timing repetitions. Masks are spread across the
experiment plan's detype levels. Each mask is sampled in batches until its 95%
BCa bootstrap interval fits within 10% of its mean, or 160 samples are reached.
"""

from argparse import ArgumentParser
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from re import compile as compile_pattern
from subprocess import run
from sys import path as import_path, stderr
from warnings import catch_warnings, simplefilter


ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "utilities" / "run_compiled_tool.py"
VARIANTS = ("advanced", "shallow", "untyped")
BATCH_SIZE = 8
MAX_SAMPLES = 160
N_RESAMPLES = 10_000
CONFIDENCE_LEVEL = 0.95
RELATIVE_MARGIN = 0.10
TIMEOUT_SECONDS = 120
FLOAT_AT_END = compile_pattern(
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*$"
)

for directory in (ROOT / "src", ROOT):
    directory_string = str(directory)
    if directory_string not in import_path:
        import_path.insert(0, directory_string)

try:
    import numpy as np
    from scipy.stats import bootstrap
except ModuleNotFoundError as exception:
    raise SystemExit(
        "run_exp.py requires SciPy; run setup.sh or `python -m pip install scipy`"
    ) from exception

from utilities.experiments import (
    load_experiment,
    validate_experiment as validate_experiment_data,
)
from utilities.load_source import (
    detyped_benchmark_source,
    temporary_module,
)
from utilities.static_runtime import static_python


@dataclass
class SampleAnalysis:
    stable: bool
    mean: float | None
    interval: tuple[float, float] | None


def load_latest_experiment():
    return load_experiment(include_typechecks=False)


def select_representative_masks(values, limit):
    """Choose at most limit ordered values while retaining both endpoints."""
    if len(values) <= limit:
        return list(values)
    if limit == 1:
        return [values[0]]
    indexes = [round(i * (len(values) - 1) / (limit - 1))
               for i in range(limit)]
    return [values[index] for index in indexes]


def confidence_interval(samples):
    """Return a deterministic 95% BCa interval for the arithmetic mean."""
    values = np.asarray(samples, dtype=float)
    if np.all(values == values[0]):
        value = float(values[0])
        return value, value
    with catch_warnings():
        simplefilter("ignore")
        result = bootstrap(
            (values,),
            np.mean,
            confidence_level=CONFIDENCE_LEVEL,
            n_resamples=N_RESAMPLES,
            method="BCa",
            rng=np.random.default_rng(20260831),
        )
    interval = result.confidence_interval
    return float(interval.low), float(interval.high)


def stability(samples):
    """Analyze whether the samples have produced a stable result."""
    if len(samples) < BATCH_SIZE:
        return SampleAnalysis(False, None, None)
    mean = float(np.mean(samples))
    low, high = confidence_interval(samples)
    if not all(isfinite(value) for value in (mean, low, high)):
        return SampleAnalysis(False, mean, (low, high))
    tolerance = RELATIVE_MARGIN * abs(mean)
    stable = low >= mean - tolerance and high <= mean + tolerance
    return SampleAnalysis(stable, mean, (low, high))


def at_sample_limit(samples):
    return len(samples) >= MAX_SAMPLES


def remaining_sample_capacity(samples):
    return min(BATCH_SIZE, MAX_SAMPLES - len(samples))


def run_once(module):
    completed = run(
        [static_python(), str(RUNNER), str(module)],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
    )
    if completed.returncode:
        error = completed.stderr.strip().splitlines()
        raise RuntimeError(
            error[-1] if error else f"benchmark exited {completed.returncode}"
        )
    match = FLOAT_AT_END.search(completed.stdout)
    if match is None:
        raise RuntimeError("benchmark did not print a final numeric runtime")
    return float(match.group(1))


def measure_benchmark(experiment, benchmark, variant, mask, sample_count):
    source = detyped_benchmark_source(
        benchmark, variant, mask, experiment.granularities[benchmark]
    )
    with temporary_module(source) as module:
        for _ in range(sample_count):
            yield run_once(module)


def validate_experiment(experiment, benchmark):
    validate_experiment_data(
        experiment,
        benchmark=benchmark,
        required_variants=VARIANTS,
        include_typechecks=False,
    )


def find_planned_masks(experiment, benchmark, variant):
    return list(experiment.plan[benchmark][variant])


def load_existing_samples(experiment, benchmark, variant, mask):
    return experiment.samples_for(benchmark, variant, mask)


def save_samples(experiment):
    experiment.save_results()


def report_progress(benchmark, variant, mask, samples, analysis):
    state = "stable" if analysis.stable else "not stable"
    print(
        f"{benchmark}/{variant} mask={mask}: {len(samples)} samples, "
        f"{state}, mean={analysis.mean:.9g}, ci={analysis.interval}",
        file=stderr,
    )


def report_result(benchmark, variant, mask, samples, analysis, was_stable):
    if was_stable:
        state = "already stable"
    else:
        state = "stable" if analysis.stable else "sample cap reached"
    print(
        f"{benchmark}/{variant} mask={mask}: {state}; "
        f"samples={len(samples)} mean={analysis.mean:.9g} "
        f"ci={analysis.interval}",
        file=stderr,
    )


def report_failure(benchmark, variant, mask, exception):
    print(
        f"{benchmark}/{variant} mask={mask}: "
        f"{type(exception).__name__}: {exception}",
        file=stderr,
    )


def sample_until_stable(experiment, benchmark, variant, mask, samples):
    analysis = stability(samples)
    was_stable = analysis.stable

    while not analysis.stable and not at_sample_limit(samples):
        new_samples = measure_benchmark(
            experiment,
            benchmark,
            variant,
            mask,
            remaining_sample_capacity(samples),
        )
        samples.extend(new_samples)
        save_samples(experiment)
        analysis = stability(samples)
        report_progress(benchmark, variant, mask, samples, analysis)

    return analysis, was_stable


def run_benchmark(benchmark, max_masks):
    experiment = load_latest_experiment()
    validate_experiment(experiment, benchmark)

    total = 0
    for variant in VARIANTS:
        planned_masks = find_planned_masks(experiment, benchmark, variant)
        selected_masks = select_representative_masks(planned_masks, max_masks)
        total += len(selected_masks)

        if not selected_masks:
            print(f"{benchmark}/{variant}: no planned masks", file=stderr)
            continue

        for mask in selected_masks:
            samples = load_existing_samples(
                experiment, benchmark, variant, mask
            )

            try:
                analysis, was_stable = sample_until_stable(
                    experiment, benchmark, variant, mask, samples
                )
                report_result(
                    benchmark, variant, mask, samples, analysis, was_stable
                )
            except Exception as exception:
                save_samples(experiment)
                report_failure(benchmark, variant, mask, exception)

    return experiment.path, total


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("benchmark", help="benchmark name from sample_plan.json")
    parser.add_argument(
        "max_masks",
        metavar="MAX_MASKS",
        type=int,
        help="maximum planned masks to run for each variant",
    )
    args = parser.parse_args()
    if args.max_masks < 1:
        parser.error("MAX_MASKS must be at least 1")
    try:
        experiment, total = run_benchmark(args.benchmark, args.max_masks)
    except (FileNotFoundError, RuntimeError, ValueError) as exception:
        parser.error(str(exception))
    print(f"{experiment}: processed {total} masks for {args.benchmark}")


if __name__ == "__main__":
    main()

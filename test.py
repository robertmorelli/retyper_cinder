"""The run button. No options -- `python test.py` and read the bars.

Every mode, every golden benchmark, every level mask, plus fuzz. Levels are the
masks worth checking on every run: nothing erased, everything erased, each unit
alone, and each unit spared. Fuzz adds random masks at mixed densities.

Failures land in problem_masks_<mode>.json; masks that were in that file and
now pass move to fixed_problem_masks_<mode>.json, so the pair is a running
record of what broke and what got repaired. A mask that comes back after being
fixed is a regression and fails the run.
"""
from ast import parse, unparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from json import dump, dumps, load
from os import cpu_count, path
from random import Random
from sys import argv, stderr
from time import perf_counter

from dataclasses import asdict, dataclass
from statistics import median
from subprocess import run
from sys import executable
from tempfile import TemporaryDirectory
from time import perf_counter_ns

from detyper import detype
from get_ast_data import get_ast_data
from list_benchmarks import get_bench_list
from load_source import load_bench
from simple_type_graph import build_binding_graph

RUNNER = path.join(path.dirname(path.abspath(__file__)), "static_runner.py")
MODES = ("compile", "runtime")
GRANULARITY = "benchmark"
VARIANT = "advanced"
SKIP = {"scratch"}
FUZZ = 50
SEED = 8675309
REPETITIONS = 1
# `python test.py --less-any` erases to a deleted annotation rather than to
# `Any`. It is a different program, so it keeps its own record of what breaks.
LESS_ANY = "--less-any" in argv
SUFFIX = "_less_any" if LESS_ANY else ""
WORKERS = cpu_count() or 1
HERE = path.dirname(path.abspath(__file__))
BAR = 28



@dataclass(frozen=True, order=True)
class Case:
    benchmark: str
    variant: str
    granularity: str
    mask: int
    seed: int = 0


def _error(exc):
    text = str(exc).strip().splitlines()
    return f"{type(exc).__name__}: {text[-1] if text else exc}"


def _run_module(source, compile_only=False):
    with TemporaryDirectory() as tmp:
        module_path = path.join(tmp, "bench_module.py")
        with open(module_path, "w") as f:
            f.write(source)
        cmd = [executable, RUNNER, module_path, "--require-static"]
        if compile_only:
            cmd.append("--compile-only")
        start = perf_counter_ns()
        proc = run(cmd, capture_output=True, text=True, timeout=120)
        elapsed = perf_counter_ns() - start
    return proc, elapsed


def compile_case(case, source, repetitions):
    start = perf_counter_ns()
    try:
        output = source if case.mask == 0 else unparse(detype(
            source, mask=case.mask, bench=case.granularity == "benchmark",
            less_any=LESS_ANY))
    except Exception as exc:
        return {"status": "detype_failure", "error": _error(exc),
                "metrics": {}}
    transform_ns = perf_counter_ns() - start
    typed_samples, detyped_samples = [], []
    for i in range(repetitions):
        order = (("typed", source, typed_samples),
                 ("detyped", output, detyped_samples))
        if i % 2:
            order = tuple(reversed(order))
        for label, artifact, samples in order:
            proc, elapsed = _run_module(artifact, compile_only=True)
            if proc.returncode:
                return {"status": f"{label}_compile_failure",
                        "error": proc.stderr.strip().splitlines()[-1],
                        "metrics": {"transform_ns": transform_ns}}
            samples.append(elapsed)
    typed_med, detyped_med = median(typed_samples), median(detyped_samples)
    return {"status": "ok", "metrics": {
        "transform_ns": transform_ns,
        "typed_compile_ns_median": int(typed_med),
        "detyped_compile_ns_median": int(detyped_med),
        "compile_ratio": detyped_med / typed_med if typed_med else None,
        "typed_compile_samples_ns": typed_samples,
        "detyped_compile_samples_ns": detyped_samples}}


def runtime_case(case, source, repetitions):
    try:
        output = source if case.mask == 0 else unparse(detype(
            source, mask=case.mask, bench=case.granularity == "benchmark",
            less_any=LESS_ANY))
    except Exception as exc:
        return {"status": "detype_failure", "error": _error(exc), "metrics": {}}
    # Correctness and timing are paired in each worker. These are cold-process
    # samples; steady-state benchmark-specific loops can use the same schema.
    typed_samples, detyped_samples = [], []
    expected = None
    for i in range(repetitions):
        order = (("typed", source, typed_samples),
                 ("detyped", output, detyped_samples))
        if i % 2:
            order = tuple(reversed(order))
        observed = {}
        for label, artifact, samples in order:
            proc, elapsed = _run_module(artifact)
            if proc.returncode:
                return {"status": "runtime_failure",
                        "error": proc.stderr.strip().splitlines()[-1], "metrics": {}}
            observed[label] = proc.stdout
            samples.append(elapsed)
        if observed["typed"] != observed["detyped"]:
            # Some benchmarks print their own elapsed time, so the typed source
            # already differs from itself run to run. Only a benchmark that is
            # stable against itself can report a semantic failure.
            again, _ = _run_module(source)
            if again.returncode == 0 and again.stdout == observed["typed"]:
                return {"status": "semantic_failure",
                        "error": "typed and detyped stdout differ", "metrics": {}}
        expected = observed["typed"]
    typed_med, detyped_med = median(typed_samples), median(detyped_samples)
    return {"status": "ok", "metrics": {
        "typed_runtime_ns_median": int(typed_med),
        "detyped_runtime_ns_median": int(detyped_med),
        "runtime_ratio": detyped_med / typed_med if typed_med else None,
        "typed_samples_ns": typed_samples,
        "detyped_samples_ns": detyped_samples}}


def execute(payload):
    mode, case, repetitions = payload
    started = perf_counter_ns()
    try:
        source = load_bench(case.benchmark, case.variant)
        result = (compile_case if mode == "compile" else runtime_case)(
            case, source, repetitions)
    except Exception as exc:
        result = {"status": "error", "error": _error(exc), "metrics": {}}
    result.update(case=asdict(case), phase=mode,
                  elapsed_ns=perf_counter_ns() - started)
    return result


def unit_count(bench, variant):
    # A benchmark whose original source will not bind has no units, and the
    # single mask=0 case is what keeps that failure visible in the results.
    try:
        data = get_ast_data(parse(load_bench(bench, variant)))
        return len(build_binding_graph(data).units(GRANULARITY))
    except Exception:
        return 0


def level_masks(n):
    if n == 0:
        return {0}
    full = (1 << n) - 1
    return ({0, full} | {1 << i for i in range(n)}
            | {full ^ (1 << i) for i in range(n)})


def fuzz_masks(n, seed, exclude):
    if n == 0:
        return set()
    rng = Random(seed)
    masks, attempts = set(), 0
    while len(masks) < FUZZ and attempts < FUZZ * 50:
        attempts += 1
        density = rng.choice((.1, .25, .5, .75, .9))
        k = max(1, min(n, round(n * density)))
        mask = sum(1 << i for i in rng.sample(range(n), k))
        if mask not in exclude:
            masks.add(mask)
    return masks


def plan_cases():
    cases, kinds, shapes = [], {}, []
    for index, (bench, variant, _) in enumerate(get_bench_list()):
        if bench in SKIP or variant != VARIANT:
            continue
        n = unit_count(bench, variant)
        seed = SEED + index
        levels = level_masks(n)
        fuzzed = fuzz_masks(n, seed, levels)
        for mask in sorted(levels | fuzzed):
            cases.append(Case(bench, variant, GRANULARITY, mask, seed))
            kinds[(bench, variant, mask)] = "level" if mask in levels else "fuzz"
        shapes.append((f"{bench}/{variant}", n, len(levels), len(fuzzed)))
    return cases, kinds, shapes


def clock(seconds):
    seconds = int(seconds)
    return f"{seconds // 60:d}:{seconds % 60:02d}"


class Progress:
    def __init__(self, total, label):
        self.total, self.label = total, label
        self.done = self.ok = self.bad = 0
        self.start = perf_counter()
        self.current = ""
        self.live = stderr.isatty()

    def update(self, result):
        self.done += 1
        if result["status"] == "ok":
            self.ok += 1
        else:
            self.bad += 1
        case = result["case"]
        self.current = f"{case['benchmark']} mask={case['mask']}"
        self.draw()

    def draw(self, end=False):
        elapsed = perf_counter() - self.start
        rate = self.done / elapsed if elapsed else 0
        remaining = (self.total - self.done) / rate if rate else 0
        filled = round(BAR * self.done / self.total) if self.total else BAR
        line = (f"{self.label:<8}[{'#' * filled}{'.' * (BAR - filled)}] "
                f"{self.done}/{self.total} ok={self.ok} fail={self.bad} "
                f"{clock(elapsed)}<{clock(remaining)}  {self.current}")
        # Without a terminal the carriage return is invisible, so fall back to
        # occasional whole lines rather than one unreadable smear.
        if self.live:
            print(f"\r\033[K{line[:150]}", end="\n" if end else "", file=stderr,
                  flush=True)
        elif end or self.done % 25 == 0:
            print(line, file=stderr, flush=True)


def run_mode(mode, cases, kinds):
    progress = Progress(len(cases), mode)
    progress.draw()
    results = []
    with ProcessPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(execute, (mode, case, REPETITIONS))
                   for case in cases]
        for future in as_completed(futures):
            results.append(future.result())
            progress.update(results[-1])
    progress.current = ""
    progress.draw(end=True)
    for result in results:
        case = result["case"]
        result["kind"] = kinds[(case["benchmark"], case["variant"], case["mask"])]
    return results


def read_json(name):
    if path.exists(name):
        with open(name) as f:
            return load(f)
    return {}


def write_json(name, value):
    with open(name, "w") as f:
        dump(value, f, indent=2, sort_keys=True)
        f.write("\n")


def track_problems(mode, cases, results):
    problems = {}
    for result in results:
        if result["status"] == "ok":
            continue
        case = result["case"]
        problems.setdefault(f"{case['benchmark']}/{case['variant']}", {})[
            str(case["mask"])] = {
                "status": result["status"], "error": result.get("error", ""),
                "kind": result["kind"], "granularity": case["granularity"]}

    tested = {}
    for case in cases:
        tested.setdefault(f"{case.benchmark}/{case.variant}", set()).add(
            str(case.mask))

    problem_file = path.join(HERE, f"problem_masks_{mode}{SUFFIX}.json")
    fixed_file = path.join(HERE, f"fixed_problem_masks_{mode}{SUFFIX}.json")
    known, fixed = read_json(problem_file), read_json(fixed_file)

    # The files are a permanent record, so this merges rather than replaces.
    # A mask that was not retried this run keeps its entry; only one that was
    # retried and now passes moves to the fixed file.
    newly_fixed, regressions = [], []
    for key, masks in known.items():
        for mask, record in masks.items():
            if mask not in tested.get(key, ()):
                problems.setdefault(key, {}).setdefault(mask, record)
            elif mask not in problems.get(key, {}):
                fixed.setdefault(key, {})[mask] = record
                newly_fixed.append(f"{key} mask={mask}")
    for key, masks in problems.items():
        for mask in masks:
            if fixed.get(key, {}).pop(mask, None) is not None:
                regressions.append(f"{key} mask={mask}")
            masks[mask]["known"] = mask in known.get(key, {})
    fixed = {key: masks for key, masks in fixed.items() if masks}

    write_json(problem_file, problems)
    write_json(fixed_file, fixed)
    return problems, newly_fixed, regressions


def report(mode, results, problems, newly_fixed, regressions):
    print(f"\n=== {mode} ===")
    statuses, by_kind = {}, {}
    for result in results:
        statuses[result["status"]] = statuses.get(result["status"], 0) + 1
        kind = by_kind.setdefault(result["kind"], {})
        kind[result["status"]] = kind.get(result["status"], 0) + 1
    print("cases:", len(results), "status:", dumps(statuses, sort_keys=True))
    for kind in sorted(by_kind):
        print(f"  {kind:<6}", dumps(by_kind[kind], sort_keys=True))

    errors = {}
    for masks in problems.values():
        for record in masks.values():
            key = (record["status"], record["error"][:110])
            errors[key] = errors.get(key, 0) + 1
    if errors:
        print("error types:")
        for (status, error), count in sorted(errors.items(), key=lambda kv: -kv[1]):
            print(f"  {count:5d}  {status}: {error}")
    print(f"newly fixed: {len(newly_fixed)}   regressions: {len(regressions)}")
    for entry in regressions[:20]:
        print("  regression:", entry)


def main():
    cases, kinds, shapes = plan_cases()
    print(f"plan: {len(cases)} cases x {len(MODES)} modes, "
          f"{GRANULARITY} granularity, {WORKERS} workers"
          f"{', less-any' if LESS_ANY else ''}", file=stderr)
    for key, units, levels, fuzzed in shapes:
        print(f"  {key:<28} units={units:<4} levels={levels:<4} fuzz={fuzzed}",
              file=stderr)

    failed = False
    for mode in MODES:
        results = run_mode(mode, cases, kinds)
        problems, newly_fixed, regressions = track_problems(mode, cases, results)
        report(mode, results, problems, newly_fixed, regressions)
        failed = failed or bool(regressions)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

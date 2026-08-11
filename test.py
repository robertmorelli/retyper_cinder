"""The run button. No options -- `python test.py` and read the bars.

Every mode, every golden benchmark, every level mask, plus fuzz. Levels are the
masks worth checking on every run: nothing erased, everything erased, each unit
alone, and each unit spared. Fuzz adds random masks at mixed densities.

Failures land in problem_masks_<mode>.json; masks that were in that file and
now pass move to fixed_problem_masks_<mode>.json, so the pair is a running
record of what broke and what got repaired. A mask that comes back after being
fixed is a regression and fails the run.
"""
from ast import parse
from concurrent.futures import ProcessPoolExecutor, as_completed
from json import dump, dumps, load
from os import cpu_count, path
from random import Random
from sys import stderr
from time import perf_counter

from get_ast_data import get_ast_data
from list_benchmarks import get_bench_list
from load_source import load_bench
from simple_type_graph import build_binding_graph
from test_graph import Case, execute

MODES = ("compile", "runtime")
GRANULARITY = "benchmark"
VARIANT = "advanced"
SKIP = {"scratch"}
FUZZ = 50
SEED = 8675309
REPETITIONS = 1
WORKERS = cpu_count() or 1
HERE = path.dirname(path.abspath(__file__))
BAR = 28


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

    problem_file = path.join(HERE, f"problem_masks_{mode}.json")
    fixed_file = path.join(HERE, f"fixed_problem_masks_{mode}.json")
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
          f"{GRANULARITY} granularity, {WORKERS} workers", file=stderr)
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

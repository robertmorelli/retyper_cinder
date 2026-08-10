"""One parallel test runner for graph prediction and detyper performance.

Examples:
  .venv/bin/python test_graph.py predict --plan smoke --workers 8
  .venv/bin/python test_graph.py compile --plan standard --workers 8
  .venv/bin/python test_graph.py runtime --plan perf --workers 4
"""
from argparse import ArgumentParser
from ast import AST, fix_missing_locations, parse, unparse, walk
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from itertools import combinations
from json import dumps
from os import path
from random import Random
from statistics import median
from subprocess import run
from sys import executable
from tempfile import TemporaryDirectory
from time import perf_counter_ns

from anno_remover import remove_annotations
from detyper import detype
from get_ast_data import get_ast_data
from simple_type_graph import build_binding_graph
from import_adder import add_imports
from list_benchmarks import get_bench_list
from load_source import load_bench

RUNNER = path.join(path.dirname(path.abspath(__file__)), "static_runner.py")


@dataclass(frozen=True, order=True)
class Case:
    benchmark: str
    variant: str
    granularity: str
    mask: int
    seed: int = 0


def masks_for(n, plan, seed):
    if n == 0:
        return [0]
    full = (1 << n) - 1
    masks = {0, full}
    masks.update(1 << i for i in range(n))
    if plan in ("standard", "high", "perf"):
        masks.update(full ^ (1 << i) for i in range(n))
    if plan in ("standard", "high") and n <= 40:
        masks.update((1 << a) | (1 << b) for a, b in combinations(range(n), 2))
    exhaustive_limit = 12 if plan == "high" else 8
    if plan in ("standard", "high") and n <= exhaustive_limit:
        masks.update(range(1 << n))
    if plan in ("standard", "high"):
        rng = Random(seed)
        per_density = 200 if plan == "high" else 20
        for density in (.1, .25, .5, .75, .9):
            k = max(1, min(n, round(n * density)))
            for _ in range(per_density):
                masks.add(sum(1 << i for i in rng.sample(range(n), k)))
    if plan == "perf":
        rng = Random(seed)
        for density in (.25, .5, .75):
            k = max(1, min(n, round(n * density)))
            masks.add(sum(1 << i for i in rng.sample(range(n), k)))
    return sorted(masks)


def cases_for(plan, granularity, seed, selected=()):
    cases = []
    selected = set(selected)
    for index, (bench, variant, _) in enumerate(get_bench_list()):
        if selected and bench not in selected and f"{bench}/{variant}" not in selected:
            continue
        source = load_bench(bench, variant)
        try:
            data = get_ast_data(parse(source))
            graph = build_binding_graph(data)
            n = len(graph.units(granularity))
        except Exception:
            # Keep one case so original-bind failures remain visible.
            n = 0
        case_seed = seed + index
        cases.extend(Case(bench, variant, granularity, mask, case_seed)
                     for mask in masks_for(n, plan, case_seed))
    return sorted(set(cases))


def _base_key(node):
    return (type(node).__name__, getattr(node, "lineno", None),
            getattr(node, "col_offset", None), getattr(node, "end_lineno", None),
            getattr(node, "end_col_offset", None))


def keyed_nodes(tree):
    counts = {}
    out = {}
    for node in walk(tree):
        if not isinstance(node, AST) or not hasattr(node, "lineno"):
            continue
        base = _base_key(node)
        occurrence = counts.get(base, 0)
        counts[base] = occurrence + 1
        out[base + (occurrence,)] = node
    return out


def type_descr(value, dyn):
    if value is None:
        return None
    if value is dyn:
        return ("dynamic",)
    klass = getattr(value, "klass", None)
    try:
        descr = getattr(klass, "type_descr", None)
    except Exception:
        descr = None
    if descr is not None:
        return tuple(descr)
    name = getattr(getattr(klass, "type_name", None), "readable_name", None)
    return ("name", name)


def predict(case, source):
    data = get_ast_data(parse(source))
    graph = build_binding_graph(data)
    erased = graph.nodes_for_mask(case.mask, case.granularity)
    settlement = graph.settle(data, erased)
    predicted_types, predicted_ctxs = settlement.types, settlement.contexts
    predicted_nodes = keyed_nodes(data.tree)
    predicted = {
        key: (type_descr(predicted_types.get(node), data.dynamic),
              type_descr(predicted_ctxs.get(node), data.dynamic))
        for key, node in predicted_nodes.items()
        if node in predicted_types or node in predicted_ctxs
    }

    erased_tree = remove_annotations(data.tree, erased, predicted_types, predicted_ctxs)
    erased_tree = fix_missing_locations(add_imports(erased_tree))
    try:
        actual = get_ast_data(erased_tree)
    except Exception as exc:
        return {"status": "no_ground_truth", "error": _error(exc),
                "metrics": {"predicted_nodes": len(predicted)}}

    actual_nodes = keyed_nodes(actual.tree)
    actual_values = {
        key: (type_descr(actual.types.get(node), actual.dynamic),
              type_descr(actual.type_contexts.get(node), actual.dynamic))
        for key, node in actual_nodes.items()
        if node in actual.types or node in actual.type_contexts
    }
    common = predicted.keys() & actual_values.keys()
    type_exact = sum(predicted[k][0] == actual_values[k][0] for k in common)
    ctx_exact = sum(predicted[k][1] == actual_values[k][1] for k in common)
    dyn_type = sum((predicted[k][0] == ("dynamic",)) ==
                   (actual_values[k][0] == ("dynamic",)) for k in common)
    dyn_ctx = sum((predicted[k][1] == ("dynamic",)) ==
                  (actual_values[k][1] == ("dynamic",)) for k in common)
    metrics = {
        "compared": len(common), "type_exact": type_exact,
        "context_exact": ctx_exact, "dynamic_type_exact": dyn_type,
        "dynamic_context_exact": dyn_ctx,
        "missing": len(actual_values.keys() - predicted.keys()),
        "extra": len(predicted.keys() - actual_values.keys()),
        "whole_mask_exact": int(bool(common) and all(
            predicted[k] == actual_values[k] for k in common)),
    }
    return {"status": "ok", "metrics": metrics}


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
    phases = {}
    try:
        output = source if case.mask == 0 else detype(
            source, mask=case.mask, bench=case.granularity == "benchmark",
            metrics=phases)
    except Exception as exc:
        return {"status": "detype_failure", "error": _error(exc),
                "metrics": phases}
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
                        "metrics": {"transform_ns": transform_ns, **phases}}
            samples.append(elapsed)
    typed_med, detyped_med = median(typed_samples), median(detyped_samples)
    return {"status": "ok", "metrics": {
        "transform_ns": transform_ns, **phases,
        "typed_compile_ns_median": int(typed_med),
        "detyped_compile_ns_median": int(detyped_med),
        "compile_ratio": detyped_med / typed_med if typed_med else None,
        "typed_compile_samples_ns": typed_samples,
        "detyped_compile_samples_ns": detyped_samples}}


def runtime_case(case, source, repetitions):
    try:
        output = source if case.mask == 0 else detype(
            source, mask=case.mask, bench=case.granularity == "benchmark")
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
        if mode == "predict":
            result = predict(case, source)
        elif mode == "compile":
            result = compile_case(case, source, repetitions)
        else:
            result = runtime_case(case, source, repetitions)
    except Exception as exc:
        result = {"status": "error", "error": _error(exc), "metrics": {}}
    result.update(case=asdict(case), phase=mode,
                  elapsed_ns=perf_counter_ns() - started)
    return result


def summarize(results):
    statuses = {}
    for result in results:
        statuses[result["status"]] = statuses.get(result["status"], 0) + 1
    print("status:", " ".join(f"{k}={v}" for k, v in sorted(statuses.items())))
    comparable = [r for r in results if r["phase"] == "predict" and
                  r["status"] == "ok"]
    if comparable:
        totals = {k: sum(r["metrics"].get(k, 0) for r in comparable)
                  for k in ("compared", "type_exact", "context_exact",
                            "dynamic_type_exact", "dynamic_context_exact")}
        n = totals["compared"] or 1
        print(f"prediction: nodes={totals['compared']} "
              f"type={totals['type_exact']/n:.3%} "
              f"context={totals['context_exact']/n:.3%} "
              f"dyn-type={totals['dynamic_type_exact']/n:.3%} "
              f"dyn-context={totals['dynamic_context_exact']/n:.3%}")
        print(f"coverage: {len(comparable)}/{len(results)} cases rebound")
    compiled = [r for r in results if r["phase"] == "compile" and
                r["status"] == "ok"]
    if compiled:
        ratios = [r["metrics"]["compile_ratio"] for r in compiled
                  if r["metrics"].get("compile_ratio") is not None]
        transforms = [r["metrics"].get("transform_ns", 0) for r in compiled]
        print(f"compile: cases={len(compiled)} "
              f"median-ratio={median(ratios):.3f} "
              f"median-transform-ms={median(transforms)/1e6:.3f}")
    ran = [r for r in results if r["phase"] == "runtime" and
           r["status"] == "ok"]
    if ran:
        ratios = [r["metrics"]["runtime_ratio"] for r in ran
                  if r["metrics"].get("runtime_ratio") is not None]
        print(f"runtime: cases={len(ran)} median-ratio={median(ratios):.3f}")
    failures = [r for r in results if r["status"] != "ok"]
    for result in failures[:50]:
        case = result["case"]
        print(f"{case['benchmark']}/{case['variant']} mask={case['mask']} "
              f"{result['status']}: {result.get('error', '')}")


def main():
    parser = ArgumentParser()
    parser.add_argument("mode", choices=("predict", "compile", "runtime"))
    parser.add_argument("--plan", choices=("smoke", "standard", "high", "perf"),
                        default="smoke")
    parser.add_argument("--granularity", choices=("annotation", "benchmark"),
                        default="benchmark")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--seed", type=int, default=8675309)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--benchmark", action="append", default=[])
    parser.add_argument("--jsonl")
    args = parser.parse_args()
    cases = cases_for(args.plan, args.granularity, args.seed, args.benchmark)
    payloads = [(args.mode, case, args.repetitions) for case in cases]
    results = []
    if args.workers == 1:
        results = [execute(payload) for payload in payloads]
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(execute, payload) for payload in payloads]
            for future in as_completed(futures):
                results.append(future.result())
    results.sort(key=lambda r: (r["case"]["benchmark"], r["case"]["variant"],
                                r["case"]["mask"], r["phase"]))
    if args.jsonl:
        with open(args.jsonl, "w") as f:
            for result in results:
                f.write(dumps(result, sort_keys=True) + "\n")
    summarize(results)


if __name__ == "__main__":
    main()

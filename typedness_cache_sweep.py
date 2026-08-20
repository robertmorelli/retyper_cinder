"""Collect L1D hardware counters for representative compiled typedness masks."""
from argparse import ArgumentParser
from ast import parse, unparse
import json
from math import ceil
from os import path
from statistics import correlation
from subprocess import DEVNULL, PIPE, Popen, run
from tempfile import TemporaryDirectory
from time import sleep

from load_source import load_bench
from typedness_cache_probe import make_l1d_template, metric_totals
from typedness_sweep import _is_main_guard
from typedness_sweep2 import build, coercions

COUNTER_UNSAFE = {"deltablue"}


def merge_results(base, held_karp):
    merged = dict(base)
    merged["points"] = [
        point for point in base["points"] if point["benchmark"] != "held_karp"
    ] + held_karp["points"]
    merged["masks"] = [
        row for row in base["masks"] if row["benchmark"] != "held_karp"
    ] + held_karp["masks"]
    merged["failures"] = [
        failure for failure in base.get("failures", [])
        if failure["benchmark"] != "held_karp"
    ] + held_karp.get("failures", [])
    return merged


def representative(point, rows):
    candidates = [
        row for row in rows
        if row["benchmark"] == point["benchmark"]
        and row["erased"] == point["erased"]
        and row.get("compiled") is not None
    ]
    return min(
        candidates,
        key=lambda row: abs(row["compiled"] - point["compiled_mean"]),
    )


def cache_row(benchmark, source, point, mask_row, temporary, template, seconds):
    emitted, _ = build(source, mask_row["mask"])
    emitted_tree = parse(emitted)
    emitted_tree.body = [
        node for node in emitted_tree.body if not _is_main_guard(node)
    ]
    emitted = unparse(emitted_tree)
    safe_name = benchmark.replace("-", "_")
    module_path = path.join(temporary, f"{safe_name}-{point['erased']}.py")
    trace = path.join(temporary, f"{safe_name}-{point['erased']}.trace")
    exported = path.join(temporary, f"{safe_name}-{point['erased']}.xml")
    with open(module_path, "w") as stream:
        stream.write(emitted)
    repetitions = max(2, ceil(seconds / mask_row["compiled"]))
    target = Popen(
        [
            ".venv/bin/python", "profile_compiled.py", module_path,
            str(repetitions), "5", "main",
        ],
        stdout=DEVNULL,
        stderr=PIPE,
    )
    sleep(1)
    try:
        run(
            [
                "xcrun", "xctrace", "record", "--template", template,
                "--output", trace, "--attach", str(target.pid),
            ],
            check=True,
            stdout=DEVNULL,
            timeout=60,
        )
    except Exception:
        target.kill()
        target.wait()
        raise
    _, target_error = target.communicate()
    if target.returncode != 0:
        tail = target_error.decode(errors="replace").strip().splitlines()
        detail = tail[-1] if tail else f"exit {target.returncode}"
        raise RuntimeError(
            f"profile target failed for {benchmark} mask {mask_row['mask']}: {detail}"
        )
    run(
        [
            "xcrun", "xctrace", "export", "--input", trace,
            "--xpath",
            "/trace-toc/run[@number='1']/data/table[@schema='MetricTable']",
            "--output", exported,
        ],
        check=True,
        stdout=DEVNULL,
    )
    totals = metric_totals(exported)
    per_run = {metric: value / repetitions for metric, value in totals.items()}
    cache_misses = per_run["l1d_miss_ld_spec"] + per_run["l1d_miss_st_spec"]
    return {
        "benchmark": benchmark,
        "units": point["units"],
        "erased": point["erased"],
        "typedness": point["typedness"],
        "mask": mask_row["mask"],
        "compiled_seconds": mask_row["compiled"],
        "repetitions": repetitions,
        **{f"{metric}_per_run": value for metric, value in per_run.items()},
        "l1d_cache_misses_per_run": cache_misses,
        "l1d_cache_misses_per_million_cycles": (
            cache_misses * 1_000_000 / per_run["cycle"]
        ),
    }


def checkpoint(output, data, cache_rows):
    originals = {
        benchmark: coercions(parse(load_bench(benchmark, "advanced")))
        for benchmark in {point["benchmark"] for point in data["points"]}
    }
    cache_by_key = {
        (row["benchmark"], row["erased"]): row for row in cache_rows
    }
    points = []
    for point in data["points"]:
        updated = dict(point)
        written = originals[point["benchmark"]]
        updated["coercions_original"] = written
        updated["coercions_total_mean"] = written + point["coercions_mean"]
        cached = cache_by_key.get((point["benchmark"], point["erased"]))
        if cached:
            updated.update(
                l1d_cache_misses_per_run=cached["l1d_cache_misses_per_run"],
                l1d_miss_ld_spec_per_run=cached["l1d_miss_ld_spec_per_run"],
                l1d_miss_st_spec_per_run=cached["l1d_miss_st_spec_per_run"],
                l1d_writeback_per_run=cached["l1d_writeback_per_run"],
                cycles_per_run=cached["cycle_per_run"],
            )
        points.append(updated)
    result = {
        "variant": "advanced",
        "mode": "compiled with L1D Cache Metrics",
        "samples": data["samples"],
        "proportions": data["proportions"],
        "runs": data["runs"],
        "points": points,
        "cache_masks": cache_rows,
        "failures": data.get("failures", []),
    }
    with open(output, "w") as stream:
        json.dump(result, stream, indent=1)


def main() -> int:
    parser = ArgumentParser()
    parser.add_argument("--base", default="typedness2_results.json")
    parser.add_argument(
        "--held-karp",
        default="held_karp_main3_typedness2_results.json",
    )
    parser.add_argument("--probe", default="held_karp_main3_cache_probe.json")
    parser.add_argument("--out", default="typedness4_results.json")
    parser.add_argument("--seconds", type=float, default=0.5)
    parser.add_argument("--benchmark", action="append", default=[])
    args = parser.parse_args()

    with open(args.base) as stream:
        base = json.load(stream)
    with open(args.held_karp) as stream:
        held_karp = json.load(stream)
    data = merge_results(base, held_karp)
    with open(args.probe) as stream:
        probe = json.load(stream)
    cache_rows = [
        {
            "benchmark": "held_karp",
            "units": 18,
            **row,
            "l1d_cache_misses_per_run": row["l1d_cache_misses_per_solve"],
            "l1d_miss_ld_spec_per_run": row["l1d_miss_ld_spec_per_solve"],
            "l1d_miss_st_spec_per_run": row["l1d_miss_st_spec_per_solve"],
            "l1d_writeback_per_run": row["l1d_writeback_per_solve"],
            "cycle_per_run": row["cycle_per_solve"],
        }
        for row in probe["rows"]
    ]
    if path.exists(args.out):
        with open(args.out) as stream:
            previous = json.load(stream)
        cache_rows.extend(
            row for row in previous.get("cache_masks", [])
            if row["benchmark"] != "held_karp"
        )
    done = {(row["benchmark"], row["erased"]) for row in cache_rows}
    wanted = set(args.benchmark)
    points = sorted(data["points"], key=lambda point: (
        point["benchmark"], point["typedness"]
    ))
    todo = [
        point for point in points
        if point["benchmark"] != "held_karp"
        and point["benchmark"] not in COUNTER_UNSAFE
        and (not wanted or point["benchmark"] in wanted)
        and (point["benchmark"], point["erased"]) not in done
        and point.get("compiled_mean") is not None
    ]
    with TemporaryDirectory() as temporary:
        template = path.join(temporary, "L1D-Cache-Metrics.tracetemplate")
        make_l1d_template(template)
        sources = {}
        for index, point in enumerate(todo, 1):
            benchmark = point["benchmark"]
            source = sources.setdefault(benchmark, load_bench(benchmark, "advanced"))
            mask_row = representative(point, data["masks"])
            print(
                f"[{index}/{len(todo)}] {benchmark} "
                f"typedness={point['typedness']:.2f} mask={mask_row['mask']}",
                flush=True,
            )
            try:
                cache_rows.append(
                    cache_row(
                        benchmark, source, point, mask_row, temporary,
                        template, args.seconds,
                    )
                )
            except Exception as error:
                print(f"  failed: {type(error).__name__}: {error}", flush=True)
            checkpoint(args.out, data, cache_rows)
    checkpoint(args.out, data, cache_rows)

    by_benchmark = {}
    for row in cache_rows:
        by_benchmark.setdefault(row["benchmark"], []).append(row)
    for benchmark, rows in sorted(by_benchmark.items()):
        if len(rows) > 1:
            print(
                benchmark,
                correlation(
                    [row["compiled_seconds"] for row in rows],
                    [row["l1d_cache_misses_per_run"] for row in rows],
                ),
            )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

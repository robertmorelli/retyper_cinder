"""Probe cache-counter correlation across Held-Karp typedness levels."""
from argparse import ArgumentParser
import json
from math import ceil
from os import path
import plistlib
from statistics import correlation
from subprocess import DEVNULL, Popen, run
from tempfile import TemporaryDirectory
from time import sleep
from xml.etree import ElementTree

from load_source import load_bench
from typedness_sweep2 import build


APPLE_TEMPLATE = (
    "/Applications/Xcode.app/Contents/Applications/Instruments.app/Contents/"
    "Resources/templates/CPU Counters.tracetemplate"
)
METRICS = (
    "cycle",
    "ld_uop_spec",
    "st_uop_spec",
    "l1d_miss_ld_spec",
    "l1d_miss_st_spec",
    "l1d_writeback",
)


def make_l1d_template(output: str) -> None:
    with open(APPLE_TEMPLATE, "rb") as stream:
        archive = plistlib.load(stream)
    changed = 0
    for index, value in enumerate(archive["$objects"]):
        if isinstance(value, bytes) and b"selectedCountingMode" in value:
            settings = json.loads(value)
            settings["selectedCountingMode"] = {
                "analysisMode": "metrics",
                "countingMode": "l1d_metrics",
            }
            settings["selectedCountingModeDisplayName"] = "L1D Cache Metrics"
            settings["countingLevel"] = "EL0"
            settings["useDebuggingInformation"] = False
            archive["$objects"][index] = json.dumps(
                settings,
                separators=(",", ":"),
            ).encode()
            changed += 1
    if changed != 1:
        raise RuntimeError(f"found {changed} CPU-counter configurations")
    with open(output, "wb") as stream:
        plistlib.dump(archive, stream, fmt=plistlib.FMT_BINARY, sort_keys=False)


def resolve(element, known):
    reference = element.get("ref")
    if reference is not None:
        return known.get((element.tag, reference), "")
    value = element.text or element.get("fmt") or ""
    identifier = element.get("id")
    if identifier is not None:
        known[(element.tag, identifier)] = value
    return value


def metric_totals(xml_path: str) -> dict[str, float]:
    root = ElementTree.parse(xml_path).getroot()
    known = {}
    totals = {metric: 0.0 for metric in METRICS}
    for row in root.iter("row"):
        values = [resolve(element, known) for element in row]
        if len(values) < 7:
            continue
        metric = values[2]
        if metric in totals:
            totals[metric] += float(values[6])
    return totals


def representative_masks(data):
    points = {point["erased"]: point for point in data["points"]}
    groups = {}
    for row in data["masks"]:
        groups.setdefault(row["erased"], []).append(row)
    chosen = []
    for erased, point in sorted(points.items(), reverse=True):
        target = point["compiled_mean"]
        rows = [row for row in groups[erased] if row.get("compiled") is not None]
        row = min(rows, key=lambda candidate: abs(candidate["compiled"] - target))
        chosen.append((point, row))
    return chosen


def main() -> int:
    parser = ArgumentParser()
    parser.add_argument(
        "--results",
        default="held_karp_main3_typedness2_results.json",
    )
    parser.add_argument("--out", default="held_karp_main3_cache_probe.json")
    parser.add_argument("--seconds", type=float, default=3.0)
    args = parser.parse_args()

    with open(args.results) as stream:
        data = json.load(stream)
    source = load_bench("held_karp", "advanced")
    rows = []
    with TemporaryDirectory() as temporary:
        template = path.join(temporary, "L1D-Miss-Sampling.tracetemplate")
        make_l1d_template(template)
        for position, (point, mask_row) in enumerate(representative_masks(data)):
            emitted, _ = build(source, mask_row["mask"])
            module_path = path.join(temporary, f"mask-{position}.py")
            with open(module_path, "w") as stream:
                stream.write(emitted)
            repetitions = max(5, ceil(args.seconds / mask_row["compiled"]))
            trace = path.join(temporary, f"mask-{position}.trace")
            exported = path.join(temporary, f"mask-{position}.xml")
            print(
                f"[{position + 1}/10] typedness={point['typedness']:.2f} "
                f"mask={mask_row['mask']} repetitions={repetitions}",
                flush=True,
            )
            target = Popen(
                [
                    ".venv/bin/python", "profile_compiled.py", module_path,
                    str(repetitions), "3",
                ],
                stdout=DEVNULL,
            )
            # Attach after static compilation and JIT warmup, while the target
            # is in its synchronization delay. Counters then cover fixed work.
            sleep(1)
            run(
                [
                    "xcrun", "xctrace", "record", "--template", template,
                    "--output", trace, "--attach", str(target.pid),
                ],
                check=True,
            )
            if target.wait() != 0:
                raise RuntimeError(f"profile target failed for mask {mask_row['mask']}")
            run(
                [
                    "xcrun", "xctrace", "export", "--input", trace,
                    "--xpath",
                    "/trace-toc/run[@number='1']/data/table[@schema='MetricTable']",
                    "--output", exported,
                ],
                check=True,
            )
            totals = metric_totals(exported)
            per_solve = {
                metric: value / repetitions for metric, value in totals.items()
            }
            cache_misses = (
                per_solve["l1d_miss_ld_spec"] + per_solve["l1d_miss_st_spec"]
            )
            rows.append(
                {
                    "typedness": point["typedness"],
                    "erased": point["erased"],
                    "mask": mask_row["mask"],
                    "compiled_seconds": mask_row["compiled"],
                    "coercions": mask_row["coercions"],
                    "repetitions": repetitions,
                    **{f"{name}_per_solve": value for name, value in per_solve.items()},
                    "l1d_cache_misses_per_solve": cache_misses,
                    "l1d_cache_misses_per_million_cycles": (
                        cache_misses * 1_000_000 / per_solve["cycle"]
                    ),
                }
            )

    runtime = [row["compiled_seconds"] for row in rows]
    coercions = [row["coercions"] for row in rows]
    candidates = [
        "l1d_miss_ld_spec_per_solve",
        "l1d_miss_st_spec_per_solve",
        "l1d_writeback_per_solve",
        "l1d_cache_misses_per_solve",
        "l1d_cache_misses_per_million_cycles",
        "cycle_per_solve",
    ]
    correlations = {
        metric: {
            "runtime": correlation(runtime, [row[metric] for row in rows]),
            "coercions": correlation(coercions, [row[metric] for row in rows]),
        }
        for metric in candidates
    }
    output = {"rows": rows, "correlations": correlations}
    with open(args.out, "w") as stream:
        json.dump(output, stream, indent=1)
    print(json.dumps(correlations, indent=1))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

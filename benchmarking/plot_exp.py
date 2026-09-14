"""Create dependency-free SVG previews from a benchmark experiment."""

from argparse import ArgumentParser
from collections import defaultdict
from html import escape
from json import load
from pathlib import Path
from statistics import mean
from sys import path as import_path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in import_path:
    import_path.insert(0, str(ROOT))

from utilities.experiments import experiment_for, load_experiment


COLORS = {"advanced": "#2563eb", "shallow": "#dc2626", "untyped": "#16a34a"}
AST_COLORS = {"advanced": "#7c3aed", "shallow": "#f59e0b", "untyped": "#111827"}
WIDTH, HEIGHT = 960, 560
LEFT, RIGHT, TOP, BOTTOM = 84, 28, 46, 70


def band(points, color):
    if len(points) == 1:
        x, low, _, high = points[0]
        return (
            f'<line x1="{x:.1f}" y1="{low:.1f}" x2="{x:.1f}" y2="{high:.1f}" '
            f'stroke="{color}" stroke-width="6" stroke-opacity="0.18"/>'
        )
    outline = (
        [(x, low) for x, low, _, _ in points]
        + [(x, high) for x, _, _, high in reversed(points)]
    )
    coordinates = " ".join(f"{x:.1f},{y:.1f}" for x, y in outline)
    return f'<polygon points="{coordinates}" fill="{color}" fill-opacity="0.18"/>'


def ribbon(points, color, half_width):
    outline = (
        [(x, y - half_width) for x, y in points]
        + [(x, y + half_width) for x, y in reversed(points)]
    )
    coordinates = " ".join(f"{x:.1f},{y:.1f}" for x, y in outline)
    return f'<polygon points="{coordinates}" fill="{color}"/>'


def benchmark_series(experiment, benchmark):
    output = {}
    for variant, masks in experiment.results[benchmark].items():
        if variant == "untyped":
            baseline = masks.get("0", [])
            output[variant] = (
                [(0, min(baseline), mean(baseline), max(baseline)),
                 (100, min(baseline), mean(baseline), max(baseline))]
                if baseline else []
            )
            continue
        units = max(mask.bit_count() for mask in experiment.plan[benchmark][variant])
        levels = defaultdict(list)
        for mask, samples in masks.items():
            if samples:
                typed = 100 * (1 - int(mask).bit_count() / units) if units else 100
                levels[typed].append(mean(samples))
        output[variant] = sorted(
            (typed, min(values), mean(values), max(values))
            for typed, values in levels.items()
        )
    return output


def ast_series(experiment, ast_counts, benchmark):
    output = {}
    for variant, masks in ast_counts[benchmark].items():
        if variant == "untyped":
            baseline = masks.get("0")
            output[variant] = (
                [(0, baseline, baseline, baseline),
                 (100, baseline, baseline, baseline)]
                if baseline is not None else []
            )
            continue
        units = max(mask.bit_count() for mask in experiment.plan[benchmark][variant])
        levels = defaultdict(list)
        for mask, count in masks.items():
            typed = 100 * (1 - int(mask).bit_count() / units) if units else 100
            levels[typed].append(count)
        output[variant] = sorted(
            (typed, min(values), mean(values), max(values))
            for typed, values in levels.items()
        )
    return output


def render_benchmark(
    experiment, benchmark, destination, variants=None, ast_counts=None
):
    series = benchmark_series(experiment, benchmark)
    nodes = ast_series(experiment, ast_counts, benchmark) if ast_counts else {}
    if variants is not None:
        series = {name: points for name, points in series.items() if name in variants}
        nodes = {name: points for name, points in nodes.items() if name in variants}
    series = {name: points for name, points in series.items() if points}
    nodes = {name: points for name, points in nodes.items() if points}
    values = [runtime for points in series.values() for _, low, average, high in points
              for runtime in ((average,) if nodes else (low, high))]
    if not values:
        return False
    low, high = min(values), max(values)
    padding = max((high - low) * 0.08, high * 0.01)
    low, high = max(0, low - padding), high + padding
    if nodes:
        node_values = [average for points in nodes.values()
                       for _, _, average, _ in points]
        node_low, node_high = min(node_values), max(node_values)
        node_padding = max((node_high - node_low) * 0.08, node_high * 0.01, 1)
        node_low, node_high = max(0, node_low - node_padding), node_high + node_padding
    right = 84 if nodes else RIGHT
    bottom = 90 if nodes else BOTTOM
    plot_w, plot_h = WIDTH - LEFT - right, HEIGHT - TOP - bottom
    sx = lambda value: LEFT + value / 100 * plot_w
    sy = lambda value: TOP + (high - value) / (high - low) * plot_h
    if nodes:
        sny = lambda value: TOP + (node_high - value) / (node_high - node_low) * plot_h
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{WIDTH/2}" y="28" text-anchor="middle" font-family="sans-serif" font-size="20">{escape(benchmark)}{(" — " + escape(next(iter(variants)))) if variants and len(variants) == 1 else ""} preview</text>',
    ]
    for tick in range(0, 101, 20):
        x = sx(tick)
        tick_y = HEIGHT - (67 if nodes else 42)
        parts.append(f'<text x="{x}" y="{tick_y}" text-anchor="middle" font-family="sans-serif" font-size="12">{tick}%</text>')
    for index in range(6):
        value = low + (high - low) * index / 5
        y = sy(value)
        parts.append(f'<text x="{LEFT-10}" y="{y+4}" text-anchor="end" font-family="sans-serif" font-size="12">{value:.3f}</text>')
        if nodes:
            node_value = node_low + (node_high - node_low) * index / 5
            node_y = sny(node_value)
            parts.append(
                f'<text x="{LEFT+plot_w+10}" y="{node_y+4}" text-anchor="start" '
                f'font-family="sans-serif" font-size="12">{node_value:.0f}</text>'
            )
    for index, (variant, points) in enumerate(series.items()):
        color = COLORS.get(variant, "#555")
        screen = [(sx(typed), sy(low), sy(average), sy(high))
                  for typed, low, average, high in points]
        if not nodes:
            parts.append(band(screen, color))
        average_line = [(x, average) for x, _, average, _ in screen]
        parts.append(ribbon(average_line, color, 1.5))
        if nodes:
            node_line = [(sx(typed), sny(average))
                         for typed, _, average, _ in nodes[variant]]
            ast_color = AST_COLORS.get(variant, "#111827")
            parts.append(ribbon(node_line, ast_color, 1.5))
            legend_x = LEFT + index * 240
            parts.append(
                f'<text x="{legend_x}" y="{HEIGHT-25}" font-family="sans-serif" '
                f'font-size="11" fill="{color}">■ {escape(variant)} runtime</text>'
            )
            parts.append(
                f'<text x="{legend_x}" y="{HEIGHT-9}" font-family="sans-serif" '
                f'font-size="11" fill="{ast_color}">■ {escape(variant)} AST nodes</text>'
            )
            continue
        else:
            legend = f'■ {escape(variant)} mean + range'
            legend_x = LEFT + index * 190
            legend_size = 13
        parts.append(
            f'<text x="{legend_x}" y="{HEIGHT-12}" font-family="sans-serif" '
            f'font-size="{legend_size}" fill="{color}">{legend}</text>'
        )
    parts.extend([
        f'<rect x="{LEFT}" y="{TOP}" width="{plot_w}" height="1" fill="#111827"/>',
        f'<rect x="{LEFT}" y="{TOP+plot_h-1}" width="{plot_w}" height="1" fill="#111827"/>',
        f'<rect x="{LEFT}" y="{TOP}" width="1" height="{plot_h}" fill="#111827"/>',
        f'<rect x="{LEFT+plot_w-1}" y="{TOP}" width="1" height="{plot_h}" fill="#111827"/>',
        f'<text x="{LEFT+plot_w/2}" y="{HEIGHT-(49 if nodes else 24)}" text-anchor="middle" font-family="sans-serif" font-size="14">Typedness</text>',
        f'<text x="18" y="{TOP+plot_h/2}" transform="rotate(-90 18 {TOP+plot_h/2})" text-anchor="middle" font-family="sans-serif" font-size="14">Mean runtime (seconds)</text>',
        *(
            [f'<text x="{WIDTH-18}" y="{TOP+plot_h/2}" transform="rotate(90 {WIDTH-18} {TOP+plot_h/2})" text-anchor="middle" font-family="sans-serif" font-size="14">AST nodes (excluding imports)</text>']
            if nodes else []
        ),
        '</svg>',
    ])
    destination.write_text("\n".join(parts) + "\n")
    return True


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("experiment")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--ast-counts", type=Path)
    parser.add_argument("--ast-overlay", action="store_true")
    parser.add_argument("--combined-only", action="store_true")
    args = parser.parse_args()
    experiment = load_experiment(experiment_for(args.experiment), include_typechecks=False)
    ast_counts = None
    if args.ast_overlay:
        ast_path = args.ast_counts or experiment.path / "sample_ast_nodes.json"
        with ast_path.open() as file:
            ast_counts = load(file)
    output = args.output or experiment.path / "previews"
    output.mkdir(parents=True, exist_ok=True)
    created = []
    for benchmark in experiment.plan:
        destination = output / f"{benchmark}.svg"
        if render_benchmark(
            experiment, benchmark, destination, ast_counts=ast_counts
        ):
            created.append(destination)
            if args.combined_only:
                continue
            for variant in experiment.plan[benchmark]:
                variant_destination = output / f"{benchmark}_{variant}.svg"
                if render_benchmark(
                    experiment, benchmark, variant_destination, {variant}, ast_counts
                ):
                    created.append(variant_destination)
    print("\n".join(map(str, created)))


if __name__ == "__main__":
    main()

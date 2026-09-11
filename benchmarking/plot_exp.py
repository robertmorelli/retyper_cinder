"""Create dependency-free SVG previews from a benchmark experiment."""

from argparse import ArgumentParser
from collections import defaultdict
from html import escape
from pathlib import Path
from statistics import mean
from sys import path as import_path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in import_path:
    import_path.insert(0, str(ROOT))

from utilities.experiments import experiment_for, load_experiment


COLORS = {"advanced": "#2563eb", "shallow": "#dc2626", "untyped": "#16a34a"}
WIDTH, HEIGHT = 960, 560
LEFT, RIGHT, TOP, BOTTOM = 84, 28, 46, 70


def polyline(points, color):
    coordinates = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    return f'<polyline points="{coordinates}" fill="none" stroke="{color}" stroke-width="2"/>'


def benchmark_series(experiment, benchmark):
    output = {}
    for variant, masks in experiment.results[benchmark].items():
        units = max(mask.bit_count() for mask in experiment.plan[benchmark][variant])
        levels = defaultdict(list)
        for mask, samples in masks.items():
            if samples:
                typed = 100 * (1 - int(mask).bit_count() / units) if units else 100
                levels[typed].append(mean(samples))
        output[variant] = sorted((typed, mean(values)) for typed, values in levels.items())
    return output


def render_benchmark(experiment, benchmark, destination, variants=None):
    series = benchmark_series(experiment, benchmark)
    if variants is not None:
        series = {name: points for name, points in series.items() if name in variants}
    values = [runtime for points in series.values() for _, runtime in points]
    if not values:
        return False
    low, high = min(values), max(values)
    padding = max((high - low) * 0.08, high * 0.01)
    low, high = max(0, low - padding), high + padding
    plot_w, plot_h = WIDTH - LEFT - RIGHT, HEIGHT - TOP - BOTTOM
    sx = lambda value: LEFT + value / 100 * plot_w
    sy = lambda value: TOP + (high - value) / (high - low) * plot_h
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{WIDTH/2}" y="28" text-anchor="middle" font-family="sans-serif" font-size="20">{escape(benchmark)}{(" — " + escape(next(iter(variants)))) if variants and len(variants) == 1 else ""} preview</text>',
    ]
    for tick in range(0, 101, 20):
        x = sx(tick)
        parts.append(f'<line x1="{x}" y1="{TOP}" x2="{x}" y2="{TOP+plot_h}" stroke="#e5e7eb"/>')
        parts.append(f'<text x="{x}" y="{HEIGHT-42}" text-anchor="middle" font-family="sans-serif" font-size="12">{tick}%</text>')
    for index in range(6):
        value = low + (high - low) * index / 5
        y = sy(value)
        parts.append(f'<line x1="{LEFT}" y1="{y}" x2="{LEFT+plot_w}" y2="{y}" stroke="#e5e7eb"/>')
        parts.append(f'<text x="{LEFT-10}" y="{y+4}" text-anchor="end" font-family="sans-serif" font-size="12">{value:.3f}</text>')
    for index, (variant, points) in enumerate(series.items()):
        color = COLORS.get(variant, "#555")
        screen = [(sx(typed), sy(runtime)) for typed, runtime in points]
        parts.append(polyline(screen, color))
        parts.extend(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}"/>' for x, y in screen)
        parts.append(f'<text x="{LEFT+index*150}" y="{HEIGHT-12}" font-family="sans-serif" font-size="13" fill="{color}">● {escape(variant)}</text>')
    parts.extend([
        f'<text x="{LEFT+plot_w/2}" y="{HEIGHT-24}" text-anchor="middle" font-family="sans-serif" font-size="14">Typedness</text>',
        f'<text x="18" y="{TOP+plot_h/2}" transform="rotate(-90 18 {TOP+plot_h/2})" text-anchor="middle" font-family="sans-serif" font-size="14">Mean runtime (seconds)</text>',
        '</svg>',
    ])
    destination.write_text("\n".join(parts) + "\n")
    return True


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("experiment")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    experiment = load_experiment(experiment_for(args.experiment), include_typechecks=False)
    output = args.output or experiment.path / "previews"
    output.mkdir(parents=True, exist_ok=True)
    created = []
    for benchmark in experiment.plan:
        destination = output / f"{benchmark}.svg"
        if render_benchmark(experiment, benchmark, destination):
            created.append(destination)
            for variant in experiment.plan[benchmark]:
                variant_destination = output / f"{benchmark}_{variant}.svg"
                if render_benchmark(
                    experiment, benchmark, variant_destination, {variant}
                ):
                    created.append(variant_destination)
    print("\n".join(map(str, created)))


if __name__ == "__main__":
    main()

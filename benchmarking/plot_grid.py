"""Combine an experiment's per-benchmark SVG previews into one grid figure.

Usage:
    python benchmarking/plot_grid.py [TIMESTAMP] [--exclude NAME ...] [--columns N]

Reads `previews/<benchmark>.svg` written by `plot_exp.py`, drops each preview's
own legend, and writes `previews/grid.svg` with one shared key in the first
empty cell. When ImageMagick is installed, `previews/grid.png` is rendered too.
"""

from argparse import ArgumentParser
from pathlib import Path
from re import sub
from shutil import which
from subprocess import run
from sys import path as import_path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in import_path:
    import_path.insert(0, str(ROOT))

from benchmarking.plot_exp import COLORS, HEIGHT, WIDTH
from utilities.experiments import experiment_for, read_json


FONT = "/System/Library/Fonts/Helvetica.ttc"


def preview_body(path, benchmark):
    """Return a preview's drawing without its background, legend, or suffix."""
    lines = path.read_text().splitlines()[1:-1]
    body = []
    for line in lines:
        if "■" in line or 'height="100%"' in line:
            continue
        line = line.replace(f">{benchmark} preview<", f">{benchmark}<")
        # ImageMagick misplaces rotate(angle cx cy) inside translated groups.
        line = sub(
            r'x="([\d.]+)" y="([\d.]+)" transform="rotate\((-?\d+) [\d.]+ [\d.]+\)"',
            r'x="0" y="0" transform="translate(\1,\2) rotate(\3)"',
            line,
        )
        body.append(line)
    return body


def key(x, y):
    parts = [
        f'<text x="{x}" y="{y}" font-family="sans-serif" font-size="28" '
        'font-weight="bold">Key</text>'
    ]
    for index, (variant, color) in enumerate(COLORS.items()):
        row = y + 60 + index * 70
        parts += [
            f'<rect x="{x}" y="{row-22}" width="90" height="44" fill="{color}" fill-opacity="0.18"/>',
            f'<rect x="{x}" y="{row-2}" width="90" height="4" fill="{color}"/>',
            f'<text x="{x+110}" y="{row+9}" font-family="sans-serif" font-size="26" '
            f'fill="{color}">{variant} mean + min/max</text>',
        ]
    return parts


def render_grid(previews, benchmarks, columns):
    rows = len(benchmarks) // columns + 1
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH*columns}" '
        f'height="{HEIGHT*rows}" viewBox="0 0 {WIDTH*columns} {HEIGHT*rows}">',
        '<rect width="100%" height="100%" fill="white"/>',
    ]
    for index, benchmark in enumerate(benchmarks):
        x, y = index % columns * WIDTH, index // columns * HEIGHT
        parts.append(f'<g transform="translate({x},{y})">')
        parts += preview_body(previews / f"{benchmark}.svg", benchmark)
        parts.append("</g>")
    cell = len(benchmarks)
    parts += key(cell % columns * WIDTH + 260, cell // columns * HEIGHT + 170)
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("timestamp", nargs="?")
    parser.add_argument("--exclude", nargs="*", default=[])
    parser.add_argument("--columns", type=int, default=3)
    args = parser.parse_args()
    experiment = experiment_for(args.timestamp)
    previews = experiment / "previews"
    benchmarks = [
        benchmark for benchmark in read_json(experiment / "sample_plan.json")
        if benchmark not in args.exclude and (previews / f"{benchmark}.svg").is_file()
    ]
    destination = previews / "grid.svg"
    destination.write_text(render_grid(previews, benchmarks, args.columns))
    print(destination)
    if which("magick"):
        png = destination.with_suffix(".png")
        font = ["-font", FONT] if Path(FONT).is_file() else []
        run(["magick", "-density", "144", *font, str(destination), str(png)], check=True)
        print(png)


if __name__ == "__main__":
    main()

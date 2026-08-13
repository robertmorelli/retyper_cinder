"""Plot typedness_results.json: benchmark time against proportion of typedness.

Twelve benchmarks is too many lines for one axis, so each gets its own panel on
its own scale -- the absolute mean seconds it printed at each proportion, with
the spread across the masks sampled there behind it. A thirteenth panel indexes
every benchmark to its own fully typed time, which is the only way to compare
them to each other.

Run with the system python; the .venv has cinderx and nothing else.

  python3 plot_typedness.py [results.json] [-o typedness.png]
"""
from argparse import ArgumentParser
from json import load
from math import ceil, exp, log

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SERIES = "#2a78d6"
BAND = "#9ec5f4"
INK = "#0b0b0b"
SECONDARY = "#52514e"
MUTED = "#898781"
SURFACE = "#fcfcfb"


def curve(points, bench):
    rows = sorted((p for p in points if p["benchmark"] == bench),
                  key=lambda p: p["typedness"])
    ok = [r for r in rows if r["mean_seconds"] is not None]
    return rows, ok


def style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
        ax.spines[side].set_linewidth(.8)
    ax.tick_params(colors=MUTED, labelsize=8, length=3)
    ax.grid(True, color=MUTED, alpha=.18, linewidth=.7)
    ax.set_axisbelow(True)


def main():
    parser = ArgumentParser()
    parser.add_argument("results", nargs="?", default="typedness_results.json")
    parser.add_argument("-o", "--out", default="typedness.png")
    args = parser.parse_args()

    with open(args.results) as f:
        data = load(f)
    points = data["points"]
    benches = sorted({p["benchmark"] for p in points})

    cols = 4
    rows_n = ceil((len(benches) + 1) / cols)
    fig, axes = plt.subplots(rows_n, cols, figsize=(4 * cols, 2.9 * rows_n),
                             facecolor=SURFACE)
    flat = axes.ravel()

    for ax, bench in zip(flat, benches):
        rows, ok = curve(points, bench)
        style(ax)
        n = rows[0]["units"] if rows else 0
        ax.set_title(f"{bench}  ({n} units)", fontsize=10, color=INK,
                     loc="left", pad=6)
        if not ok:
            reason = next((r["failures"][0] for r in rows if r["failures"]),
                          "no data")
            ax.text(.5, .5, reason.replace("_", " "), ha="center", va="center",
                    color=SECONDARY, fontsize=9, transform=ax.transAxes)
            ax.set_xticks([]), ax.set_yticks([])
            continue
        x = [r["typedness"] for r in ok]
        ax.fill_between(x, [r["min_seconds"] for r in ok],
                        [r["max_seconds"] for r in ok], color=BAND, alpha=.45,
                        linewidth=0)
        ax.plot(x, [r["mean_seconds"] for r in ok], color=SERIES, linewidth=2,
                marker="o", markersize=4, markeredgecolor=SURFACE,
                markeredgewidth=1.2)
        ax.set_ylim(bottom=0)
        ax.set_xlim(-.03, 1.03)
        ax.set_ylabel("seconds", fontsize=8, color=SECONDARY)
        partial = [r for r in ok if r["ok"] < r["masks"]]
        if partial:
            ax.text(.02, .04, f"{len(partial)} proportions partly failed",
                    fontsize=7, color=SECONDARY, transform=ax.transAxes)

    # Indexed panel: every benchmark against its own fully typed time.
    ax = flat[len(benches)]
    style(ax)
    ax.set_title("all benchmarks, indexed to fully typed", fontsize=10,
                 color=INK, loc="left", pad=6)
    # A benchmark's levels land on multiples of 1/n, so 11 units and 6 units
    # never share an x. Points go into the nominal proportions the sweep asked
    # for, otherwise each x would average a different subset of benchmarks and
    # the curve would sawtooth on that alone.
    steps = max(data["proportions"] - 1, 1)
    by_typedness, indexed = {}, []
    for bench in benches:
        _, ok = curve(points, bench)
        base = next((r["mean_seconds"] for r in ok if r["typedness"] == 1), None)
        if not base:
            continue
        indexed.append(bench)
        for r in ok:
            bin_x = round(r["typedness"] * steps) / steps
            by_typedness.setdefault(bin_x, []).append(r["mean_seconds"] / base)
    if by_typedness:
        xs = sorted(by_typedness)
        geo = [exp(sum(map(log, by_typedness[t])) / len(by_typedness[t]))
               for t in xs]
        ax.text(.02, .1, f"geometric mean over the {len(indexed)} benchmarks "
                         f"with a fully typed baseline",
                fontsize=7, color=SECONDARY, transform=ax.transAxes)
        ax.plot(xs, geo, color=SERIES, linewidth=2, marker="o", markersize=4,
                markeredgecolor=SURFACE, markeredgewidth=1.2)
        ax.axhline(1, color=MUTED, linewidth=.8, linestyle=(0, (4, 3)))
        ax.set_ylabel("x fully typed time", fontsize=8, color=SECONDARY)
        ax.set_xlim(-.03, 1.03)
    for ax in flat[len(benches) + 1:]:
        ax.axis("off")
    for ax in flat[:len(benches) + 1]:
        ax.set_xlabel("proportion typed (1.0 = untouched source)", fontsize=8,
                      color=SECONDARY)

    label = ("JIT" if data.get("jit") else "no JIT")
    fig.suptitle(f"Static Python benchmark time vs proportion of typedness "
                 f"({data['samples']} masks per proportion, "
                 f"{data['granularity']} granularity, {label})",
                 fontsize=12, color=INK, x=.01, ha="left", y=.995)
    fig.tight_layout(rect=(0, 0, 1, .97))
    fig.savefig(args.out, dpi=160, facecolor=SURFACE)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    raise SystemExit(main())

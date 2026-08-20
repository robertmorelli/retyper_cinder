"""Plot compiled runtime, coercions, and L1D cache misses by typedness."""
from argparse import ArgumentParser
from json import load
from math import ceil

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PERF = "#eb6834"
PERF_BAND = "#f6c3ae"
GREEN = "#1baf7a"
PURPLE = "#8b5cf6"
INK = "#0b0b0b"
MUTED = "#777570"
SURFACE = "#fcfcfb"


def pad_limits(values, fraction=.12):
    low, high = min(values), max(values)
    pad = (high - low) * fraction or max(abs(high) * .1, .01)
    return max(0, low - pad), high + pad


def main():
    parser = ArgumentParser()
    parser.add_argument("results", nargs="?", default="typedness4_results.json")
    parser.add_argument("-o", "--out", default="typedness4.png")
    args = parser.parse_args()
    with open(args.results) as stream:
        data = load(stream)

    benches = sorted({p["benchmark"] for p in data["points"]})
    cols = min(4, len(benches))
    rows_n = ceil(len(benches) / cols)
    fig, axes = plt.subplots(rows_n, cols, figsize=(4.8 * cols, 3.35 * rows_n),
                             facecolor=SURFACE)
    flat = [axes] if len(benches) == 1 else axes.ravel()

    for ax, bench in zip(flat, benches):
        rows = sorted((p for p in data["points"] if p["benchmark"] == bench),
                      key=lambda p: p["typedness"])
        x = [p["typedness"] for p in rows]
        means = [p["compiled_mean"] for p in rows]
        ax.fill_between(x, [p["compiled_min"] for p in rows],
                        [p["compiled_max"] for p in rows], color=PERF_BAND,
                        alpha=.4, linewidth=0)
        ax.plot(x, means, color=PERF, marker="o", markersize=4, linewidth=2,
                label="compiled runtime", zorder=4)
        ax.set_ylim(*pad_limits([v for p in rows for v in
                                (p["compiled_min"], p["compiled_max"])]))

        coercions = ax.twinx()
        totals = [p["coercions_total_mean"] for p in rows]
        coercions.plot(x, totals, color=GREEN, marker="o", markersize=3,
                       linewidth=1.5, label="coercions")
        coercions.set_ylim(*pad_limits(totals + [rows[0]["coercions_original"]]))
        coercions.tick_params(axis="y", colors=GREEN, labelsize=7)
        coercions.spines["right"].set_color(GREEN)
        coercions.set_ylabel("coercions", color=GREEN, fontsize=7)

        cache = ax.twinx()
        cache.spines["right"].set_position(("outward", 45))
        cache_rows = [p for p in rows if p.get("l1d_cache_misses_per_run") is not None]
        if cache_rows:
            cache_values = [p["l1d_cache_misses_per_run"] for p in cache_rows]
            cache.plot([p["typedness"] for p in cache_rows], cache_values,
                       color=PURPLE, marker="D", markersize=3.2, linewidth=1.6,
                       label="L1D misses")
            cache.set_ylim(*pad_limits(cache_values))
            cache.ticklabel_format(axis="y", style="sci", scilimits=(0, 0))
        else:
            ax.text(.03, .05, "L1D counters crash this benchmark", color=PURPLE,
                    fontsize=7, transform=ax.transAxes)
        cache.tick_params(axis="y", colors=PURPLE, labelsize=7)
        cache.spines["right"].set_color(PURPLE)
        cache.set_ylabel("speculative L1D misses / run", color=PURPLE, fontsize=7,
                         labelpad=8)

        ax.set_facecolor(SURFACE)
        ax.grid(True, color=MUTED, alpha=.17, linewidth=.7)
        ax.set_axisbelow(True)
        ax.set_xlim(-.04, 1.04)
        ax.tick_params(colors=MUTED, labelsize=8)
        ax.spines["top"].set_visible(False)
        ax.set_title(f"{bench}  ({rows[0]['units']} units)", loc="left",
                     fontsize=10, color=INK)
        ax.set_xlabel("proportion typed", fontsize=8, color=MUTED)
        ax.set_ylabel("compiled seconds", fontsize=8, color=PERF)
        handles, labels = ax.get_legend_handles_labels()
        for twin in (coercions, cache):
            h, lab = twin.get_legend_handles_labels()
            handles += h; labels += lab
        ax.legend(handles, labels, fontsize=6.8, frameon=False, loc="best")

    for ax in flat[len(benches):]:
        ax.axis("off")
    fig.suptitle("Compiled runtime vs typedness, coercions, and speculative L1D cache misses",
                 x=.01, y=.997, ha="left", fontsize=12, color=INK)
    fig.tight_layout(rect=(0, 0, .985, .98), w_pad=2.5)
    fig.savefig(args.out, dpi=160, facecolor=SURFACE)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

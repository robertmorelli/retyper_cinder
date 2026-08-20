"""Plainly-run timings only, per benchmark (compiled series dropped).

One panel a benchmark from typedness2_results.json: two timing series on the
left axis, and the coercions the detyper injected as a line on
a right axis, so a benchmark's counts sit on the same picture as its times
instead of in a strip you have to match up by position. The right axis is a
second scale on one chart, which is normally worth avoiding -- it is here
because the question is "where in this benchmark did the coercions land", not
"are these two quantities proportional".

The y-limits come from both series and both spreads together, so nothing sits
off the viewport.

  python3 plot_typedness2.py [results.json] [-o typedness2.png]
"""
from argparse import ArgumentParser
from json import load
from math import ceil

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

COMPILED = "#2a78d6"
COMPILED_BAND = "#9ec5f4"
PLAIN = "#eb6834"
PLAIN_BAND = "#f6c3ae"
COERCION = "#1baf7a"
INK = "#0b0b0b"
SECONDARY = "#52514e"
MUTED = "#898781"
SURFACE = "#fcfcfb"


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
    ax.set_xlim(-.04, 1.04)


def series(rows, mode):
    pts = [(r["typedness"], r[f"{mode}_mean"], r[f"{mode}_min"], r[f"{mode}_max"])
           for r in rows if r.get(f"{mode}_mean") is not None]
    return sorted(pts)


def draw(ax, rows, mode, color, band, label):
    pts = series(rows, mode)
    if not pts:
        return []
    x = [p[0] for p in pts]
    ax.fill_between(x, [p[2] for p in pts], [p[3] for p in pts], color=band,
                    alpha=.4, linewidth=0)
    ax.plot(x, [p[1] for p in pts], color=color, linewidth=2, marker="o",
            markersize=4, markeredgecolor=SURFACE, markeredgewidth=1.1,
            label=label, zorder=3)
    return [v for p in pts for v in (p[2], p[3])]


def main():
    parser = ArgumentParser()
    parser.add_argument("results", nargs="?", default="typedness2_results.json")
    parser.add_argument("-o", "--out", default="typedness3.png")
    args = parser.parse_args()

    with open(args.results) as f:
        data = load(f)
    points = data["points"]
    benches = sorted({p["benchmark"] for p in points})
    holes = {}
    for f in data.get("failures", []):
        holes.setdefault(f["benchmark"], []).append(f)

    cols = min(4, len(benches))
    rows_n = ceil(len(benches) / cols)
    width = 7.5 if len(benches) == 1 else 4.3 * cols
    fig, axes = plt.subplots(rows_n, cols, figsize=(width, 3.2 * rows_n),
                             facecolor=SURFACE)
    flat = [axes] if len(benches) == 1 else axes.ravel()

    for ax, bench in zip(flat, benches):
        rows = [p for p in points if p["benchmark"] == bench]
        n = rows[0]["units"]
        # Absolute wrapper count in the emitted program, not a signed delta:
        # erasing can delete the author's own box()/cbool() calls, so a delta
        # goes negative and gets clipped off the bottom of the axis.
        written = rows[0]["coercions_original"]
        totals = [r["coercions_total_mean"] for r in rows]

        # coercions first, on their own axis and behind everything
        counts = ax.twinx()
        counts.set_facecolor(SURFACE)
        counts.plot([r["typedness"] for r in rows], totals, color=COERCION,
                    linewidth=1.6, zorder=1, marker="o", markersize=3,
                    markeredgecolor=SURFACE, markeredgewidth=.9,
                    label="coercions in program")
        if written:
            counts.axhline(written, color=COERCION, linewidth=.9,
                           linestyle=(0, (4, 3)), alpha=.7, zorder=1,
                           label=f"as written ({written:.0f})")
        span = [*totals, written]
        low, high = min(span), max(span)
        pad = (high - low) * .18 or max(high * .2, .5)
        counts.set_ylim(max(0, low - pad), high + pad)
        counts.tick_params(colors=COERCION, labelsize=7, length=3)
        counts.spines["top"].set_visible(False)
        counts.spines["left"].set_visible(False)
        counts.spines["right"].set_color(COERCION)
        counts.spines["right"].set_linewidth(.8)
        counts.set_ylabel(f"coercion calls in program (as written {written:.0f})", fontsize=7,
                          color=COERCION)

        style(ax)
        ax.patch.set_visible(False)          # let the green area show through
        ax.set_zorder(counts.get_zorder() + 1)
        ax.set_title(f"{bench}  ({n} units)", fontsize=10, color=INK,
                     loc="left", pad=6)
        values = draw(ax, rows, "plain", PLAIN, PLAIN_BAND, "run directly")
        if values:
            low, high = min(values), max(values)
            pad = (high - low) * .08 or high * .1 or .01
            ax.set_ylim(max(0, low - pad), high + pad)
        ax.set_ylabel("seconds", fontsize=8, color=SECONDARY)
        ax.set_xlabel("proportion typed", fontsize=8, color=SECONDARY)
        handles, labels = ax.get_legend_handles_labels()
        h2, l2 = counts.get_legend_handles_labels()
        ax.legend(handles + h2, labels + l2, fontsize=7, frameon=False,
                  labelcolor=SECONDARY, loc="best")
        missing = [f for f in holes.get(bench, []) if f["status"] == "slot_exhausted"]
        if missing:
            ax.text(.02, .03, f"{len(missing)} proportions incomplete",
                    fontsize=7, color=SECONDARY, transform=ax.transAxes)

    for ax in flat[len(benches):]:
        ax.axis("off")

    if len(benches) == 1:
        title = ("Runtime vs proportion typed, run directly; injected coercions "
                 f"({data['samples']} masks per proportion, --no-inliner)")
    else:
        title = ("Benchmark time vs proportion of typedness, run directly "
                 "(no static compilation), with injected coercions "
                 f"({data['samples']} masks per proportion, --no-inliner)")
    fig.suptitle(title,
                 fontsize=10 if len(benches) == 1 else 12,
                 color=INK, x=.01, ha="left", y=.997)
    fig.tight_layout(rect=(0, 0, 1, .92 if len(benches) == 1 else .98))
    fig.savefig(args.out, dpi=150, facecolor=SURFACE)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    raise SystemExit(main())

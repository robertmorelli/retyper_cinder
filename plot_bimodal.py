"""The two findings the factorial makes visible, in one figure.

Left: all 128 compiled cell means as a strip. They land in two clumps, not on a
gradient, and which clump a mask lands in is decided by one unit.

Right: the C/D/E cube. The penalty is not "less typing is slower" -- it is a
boundary between an int64 signature and a dynamic caller, so erasing one side
costs 26 ms and erasing both costs nothing.

  python3 plot_bimodal.py [factorial_call_method_slots.json] [-o bimodal.png]
"""
from argparse import ArgumentParser
from json import load

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

FAST = "#2a78d6"
SLOW = "#eb6834"
INK = "#0b0b0b"
SECONDARY = "#52514e"
MUTED = "#898781"
SURFACE = "#fcfcfb"
C, D, E = 2, 3, 4


def style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
        ax.spines[side].set_linewidth(.8)
    ax.tick_params(colors=MUTED, labelsize=9, length=3)
    ax.grid(True, color=MUTED, alpha=.18, linewidth=.7)
    ax.set_axisbelow(True)


def main():
    parser = ArgumentParser()
    parser.add_argument("results", nargs="?",
                        default="factorial_call_method_slots.json")
    parser.add_argument("-o", "--out", default="bimodal.png")
    args = parser.parse_args()
    with open(args.results) as f:
        data = load(f)
    cells = {c["mask"]: c for c in data["cells"] if c["mode"] == "compiled"}
    mean = lambda m: sum(cells[m]["samples"]) / len(cells[m]["samples"]) * 1000

    fig, (left, right) = plt.subplots(1, 2, figsize=(13.5, 4.6),
                                      facecolor=SURFACE,
                                      gridspec_kw={"width_ratios": [1.35, 1]})

    style(left)
    for group, color, label in (
            (lambda m: not m >> C & 1, FAST, "C kept (a,b,c: int64 annotated)"),
            (lambda m: m >> C & 1, SLOW, "C erased")):
        masks = [m for m in range(128) if group(m)]
        left.hist([mean(m) for m in masks], bins=24, range=(45, 85),
                  color=color, alpha=.75, label=label)
    left.set_xlabel("benchmark time (ms), one entry per mask", fontsize=9,
                    color=SECONDARY)
    left.set_ylabel("masks", fontsize=9, color=SECONDARY)
    left.set_title("All 128 masks land in two clumps, not on a gradient",
                   fontsize=11, color=INK, loc="left", pad=8)
    left.legend(fontsize=8, frameon=False, labelcolor=SECONDARY)

    style(right)
    combos = [(c, d, e) for c in (0, 1) for d in (0, 1) for e in (0, 1)]
    values = [mean((c << C) | (d << D) | (e << E)) for c, d, e in combos]
    names = ["".join(letter for letter, bit in zip("CDE", combo) if bit) or "none"
             for combo in combos]
    colors = [SLOW if v > 65 else FAST for v in values]
    right.barh(range(len(combos)), values, color=colors, height=.66)
    right.set_yticks(range(len(combos)))
    right.set_yticklabels([f"erased: {n}" for n in names], fontsize=9)
    right.invert_yaxis()
    for index, value in enumerate(values):
        right.text(value + .8, index, f"{value:.1f}", va="center", fontsize=8,
                   color=SECONDARY)
    right.set_xlim(0, 92)
    right.set_xlabel("benchmark time (ms)", fontsize=9, color=SECONDARY)
    right.set_title("Mixed typing costs; uniform typing does not",
                    fontsize=11, color=INK, loc="left", pad=8)

    fig.suptitle(f"{data['benchmark']}: full 2^{data['n']} factorial, "
                 f"{data['reps']} reps per cell, statically compiled, "
                 "--no-inliner", fontsize=12, color=INK, x=.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, .94))
    fig.savefig(args.out, dpi=160, facecolor=SURFACE)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    raise SystemExit(main())

"""Analyse the 2^7 factorial: main effects, interactions, and what is real.

The design is complete and orthogonal, so effects need no fitting -- each is a
Hadamard contrast over the 128 cell means, computed by a fast Walsh-Hadamard
transform. Replicates give a pooled within-cell variance, so every one of the
127 effects gets an F-test; Benjamini-Hochberg controls the false discovery rate
across them, because at alpha=.05 on 127 tests you would expect six false
positives by chance. Lenth's PSE is reported alongside as a check that does not
lean on the replicate variance at all.

Convention: a factor is +1 when its unit is ERASED. So a positive main effect
means erasing that unit makes the benchmark slower.

  python3 analyze_factorial.py [factorial_call_method_slots.json]
  python3 analyze_factorial.py --top 25 -o factorial.png
"""
from argparse import ArgumentParser

from json import load
from re import findall
from math import sqrt

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import stats

MAIN = "#2a78d6"
TWO_WAY = "#eb6834"
HIGHER = "#898781"
INK = "#0b0b0b"
SECONDARY = "#52514e"
MUTED = "#898781"
SURFACE = "#fcfcfb"


def hadamard(values):
    """In-place Walsh-Hadamard transform; index i is the contrast for subset i."""
    out = list(values)
    n = len(out)
    step = 1
    while step < n:
        for start in range(0, n, step * 2):
            for i in range(start, start + step):
                a, b = out[i], out[i + step]
                out[i], out[i + step] = a + b, a - b
        step *= 2
    return out


def cells_for(data, mode):
    return {c["mask"]: c for c in data["cells"]
            if c["mode"] == mode and c["samples"]}


def effects(data, mode):
    """Every subset's effect, with an F-test against pooled replicate error."""
    n = data["n"]
    size = 1 << n
    cells = cells_for(data, mode)
    if len(cells) < size:
        raise SystemExit(f"{mode}: {len(cells)}/{size} cells filled; "
                         "the design has holes and is no longer orthogonal")
    means = [sum(cells[m]["samples"]) / len(cells[m]["samples"]) for m in range(size)]

    # pooled within-cell variance -> the error term the F-tests use
    ss_error, df_error = 0.0, 0
    for mask in range(size):
        xs = cells[mask]["samples"]
        if len(xs) < 2:
            continue
        m = sum(xs) / len(xs)
        ss_error += sum((x - m) ** 2 for x in xs)
        df_error += len(xs) - 1
    ms_error = ss_error / df_error if df_error else float("nan")
    reps = sum(len(cells[m]["samples"]) for m in range(size)) / size

    # Hadamard contrasts. The sign of index i on cell mask is (-1)^popcount(i&mask),
    # so the transform of the cell means gives every contrast at once.
    raw = hadamard(means)
    rows = []
    for index in range(1, size):
        # effect = mean(erased half) - mean(kept half). The Hadamard sign at
        # cell m is (-1)^popcount(index & m), which is -1 exactly where the
        # factor is erased, so the transform gives kept - erased and the sign
        # has to be flipped to match the convention in the docstring.
        effect = -2 * raw[index] / size
        ss = reps * size * (effect / 2) ** 2
        f = ss / ms_error if ms_error and ms_error == ms_error else float("nan")
        p = float(stats.f.sf(f, 1, df_error)) if df_error else float("nan")
        order = bin(index).count("1")
        rows.append({"index": index, "order": order, "effect": effect,
                     "ss": ss, "f": f, "p": p,
                     "factors": [i for i in range(n) if index >> i & 1]})
    # Benjamini-Hochberg
    for rank, row in enumerate(sorted(rows, key=lambda r: r["p"]), 1):
        row["q"] = min(1.0, row["p"] * len(rows) / rank)
    running = 1.0
    for row in sorted(rows, key=lambda r: r["p"], reverse=True):
        running = min(running, row["q"])
        row["q"] = running

    # Lenth's pseudo standard error: robust, ignores the replicate variance
    absolute = sorted(abs(r["effect"]) for r in rows)
    s0 = 1.5 * absolute[len(absolute) // 2]
    kept = [a for a in absolute if a < 2.5 * s0]
    pse = 1.5 * (sorted(kept)[len(kept) // 2] if kept else s0)
    for row in rows:
        row["lenth_t"] = row["effect"] / pse if pse else float("nan")

    grand = sum(means) / size
    return {"rows": rows, "means": means, "grand": grand, "ms_error": ms_error,
            "df_error": df_error, "reps": reps, "pse": pse,
            "sd_within": sqrt(ms_error) if ms_error == ms_error else None,
            "typed": means[0], "erased": means[size - 1]}


def pretty(label):
    """A unit is a frozenset of AST nodes; say what it covers, briefly."""
    funcs = findall(r"FunctionDef\(name='(\w+)'", label)
    args = findall(r"arg\(arg='(\w+)', annotation=Name\(id='(\w+)'", label)
    targets = findall(r"AnnAssign\(target=Name\(id='(\w+)'[^)]*\), "
                      r"annotation=Name\(id='(\w+)'", label)
    stores = findall(r"Name\(id='(\w+)', ctx=Store\(\)\)", label)
    parts = []
    if funcs:
        parts.append("def " + ",".join(sorted(set(funcs))))
    if args:
        parts.append("args " + ",".join(f"{a}:{t}" for a, t in
                                       sorted(set(args))))
    if targets:
        parts.append("var " + ",".join(f"{a}:{t}" for a, t in sorted(set(targets))))
    bare = sorted(set(stores) - {a for a, _ in targets})
    if bare:
        parts.append("names " + ",".join(bare))
    return "; ".join(parts) or label[:60]


def name(row, labels):
    return " x ".join(pretty(labels[i]) for i in row["factors"])


def short(row):
    return "".join("ABCDEFGH"[i] for i in row["factors"])


def report(data, mode, fit, labels, top):
    print(f"\n{'=' * 78}\n{mode.upper()}   grand mean {fit['grand'] * 1000:.2f} ms   "
          f"fully typed {fit['typed'] * 1000:.2f} ms   "
          f"fully erased {fit['erased'] * 1000:.2f} ms")
    sd = f"{fit['sd_within'] * 1000:.3f} ms" if fit["sd_within"] else "n/a (1 rep)"
    print(f"within-cell sd {sd} on {fit['df_error']} df "
          f"({fit['reps']:.1f} reps/cell), Lenth PSE {fit['pse'] * 1000:.3f} ms")
    total_ss = sum(r["ss"] for r in fit["rows"])
    print(f"\n{'term':<10s}{'order':>6s}{'effect (ms)':>13s}{'% of typed':>12s}"
          f"{'% var':>8s}{'F':>10s}{'q':>10s}")
    for row in sorted(fit["rows"], key=lambda r: -abs(r["effect"]))[:top]:
        star = "*" if row["q"] < .05 else " "
        print(f"{short(row):<10s}{row['order']:>6d}{row['effect'] * 1000:>13.3f}"
              f"{row['effect'] / fit['typed'] * 100:>11.1f}%"
              f"{row['ss'] / total_ss * 100:>7.1f}%{row['f']:>10.1f}"
              f"{row['q']:>9.2g}{star}")
    sig = [r for r in fit["rows"] if r["q"] < .05]
    print(f"\n{len(sig)}/127 terms significant at q<.05: "
          f"{sum(1 for r in sig if r['order'] == 1)} main, "
          f"{sum(1 for r in sig if r['order'] == 2)} two-way, "
          f"{sum(1 for r in sig if r['order'] > 2)} higher")
    by_order = {}
    for row in fit["rows"]:
        by_order.setdefault(row["order"], []).append(row["ss"])
    print("variance by order: " + "  ".join(
        f"{order}-way {sum(ss) / total_ss * 100:.1f}%"
        for order, ss in sorted(by_order.items())))
    print("\nmain effects, in unit order (+ = erasing this unit is slower):")
    for row in sorted((r for r in fit["rows"] if r["order"] == 1),
                      key=lambda r: r["index"]):
        print(f"  {short(row)}  {row['effect'] * 1000:+8.3f} ms  q={row['q']:<9.2g} "
              f"{name(row, labels)[:70]}")


def plot(data, fits, labels, out):
    modes = list(fits)
    fig, axes = plt.subplots(2, len(modes), figsize=(7.2 * len(modes), 8.4),
                             facecolor=SURFACE)
    axes = axes.reshape(2, len(modes))
    for col, mode in enumerate(modes):
        fit = fits[mode]
        rows = sorted(fit["rows"], key=lambda r: -abs(r["effect"]))[:20][::-1]
        ax = axes[0][col]
        ax.set_facecolor(SURFACE)
        colors = [MAIN if r["order"] == 1 else TWO_WAY if r["order"] == 2 else HIGHER
                  for r in rows]
        ax.barh([short(r) for r in rows], [r["effect"] * 1000 for r in rows],
                color=colors, height=.72)
        ax.axvline(0, color=MUTED, linewidth=.8)
        ax.set_xlabel("effect on runtime (ms), + = erasing is slower", fontsize=9,
                      color=SECONDARY)
        ax.set_title(f"{mode}: 20 largest effects", fontsize=11, color=INK,
                     loc="left")
        ax.tick_params(colors=MUTED, labelsize=8)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.grid(True, axis="x", color=MUTED, alpha=.18, linewidth=.7)
        ax.set_axisbelow(True)

        # half-normal plot: real effects leave the line
        ax = axes[1][col]
        ax.set_facecolor(SURFACE)
        ordered = sorted(fit["rows"], key=lambda r: abs(r["effect"]))
        m = len(ordered)
        quantiles = [stats.norm.ppf(.5 + .5 * (i - .5) / m) for i in range(1, m + 1)]
        ax.scatter(quantiles, [abs(r["effect"]) * 1000 for r in ordered], s=14,
                   color=[MAIN if r["order"] == 1 else TWO_WAY if r["order"] == 2
                          else HIGHER for r in ordered], zorder=3)
        for q, row in zip(quantiles, ordered):
            if abs(row["effect"]) > 2.5 * fit["pse"]:
                ax.annotate(short(row), (q, abs(row["effect"]) * 1000),
                            fontsize=7, color=SECONDARY,
                            xytext=(4, -2), textcoords="offset points")
        ax.set_xlabel("half-normal quantile", fontsize=9, color=SECONDARY)
        ax.set_ylabel("|effect| (ms)", fontsize=9, color=SECONDARY)
        ax.set_title(f"{mode}: half-normal plot (blue main, orange 2-way, "
                     f"grey higher)", fontsize=11, color=INK, loc="left")
        ax.tick_params(colors=MUTED, labelsize=8)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.grid(True, color=MUTED, alpha=.18, linewidth=.7)
        ax.set_axisbelow(True)
    fig.suptitle(f"{data['benchmark']}: full 2^{data['n']} factorial over "
                 f"annotation units ({data['reps']} reps, --no-inliner)",
                 fontsize=12, color=INK, x=.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, .97))
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    print(f"\nwrote {out}")


def main():
    parser = ArgumentParser()
    parser.add_argument("results", nargs="?",
                        default="factorial_call_method_slots.json")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("-o", "--out", default="factorial.png")
    args = parser.parse_args()
    with open(args.results) as f:
        data = load(f)
    labels = data["units"]
    print(f"{data['benchmark']}  {data['n']} units, {1 << data['n']} masks, "
          f"{data['reps']} reps, {len(data['failures'])} failed runs, "
          f"{data['elapsed_s'] / 60:.1f} min")
    print("units (factor letters):")
    for i, label in enumerate(labels):
        print(f"  {'ABCDEFGH'[i]} = {pretty(label)}")
    fits = {}
    for mode in data["modes"]:
        fits[mode] = effects(data, mode)
        report(data, mode, fits[mode], labels, args.top)
    plot(data, fits, labels, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""The paper's figures, written to results/figures/ as PDF and PNG.

Every result is read from results/ (analysis/delta.py,
analysis/extra_numbers.py, analysis/figure_numbers.py) and
data/ad-vendors.json. Crawl dates and protocol constants in the pipeline
diagram are written in. The file names match the paper's source.

  fig8-pipeline-overview   Fig. 1: the harness and study, five stages
  fig15-sample-flow        Fig. 2: sample to the sites behind each RQ
  fig14-rq1-whether-what   Fig. 3, RQ1: fails at load vs. what changes after use
  fig16-rq1-new-rules      Fig. 4, RQ1: which rules newly fail, top document vs.
                           frames only, recurrence on both visits
  fig17-rq2-delta-size     Fig. 5, RQ2: size of ads - blocked vs. identical visits
  fig11-vendor-dose        Fig. 6, RQ2: Δ>0 / Δ<0 by listed vendors requested
                           (0 = placebo)
  fig12-structure-split    Fig. 7, RQ2: structure-rule shares, top 5% of sites
                           vs. the rest
  fig7-oopif-vs-mainframe  Fig. 8, RQ3: violating nodes per rule inside visible
                           cross-origin frames, by who serves the frame
                           (top-document-only axe rules excluded)
  fig18-rq3-frame-orgs     Fig. 9, RQ3: hosts serving visible frames, reach and
                           failure rate

Style follows figures4papers (C. Liu, github.com/ChenLiu-1996/figures4papers):
a sans-serif face (Liberation Sans, metric-compatible with Arial), no top or
right spines, black-edged bars, hatching so series survive grey-scale print,
values printed on the marks, and one palette: navy/blue for the measured
quantity, pink/red for ads, greys for baselines.

Usage (repo root, after the three analysis scripts):
    python3 scripts/make_figures.py
"""
import csv, json, math, os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Patch

ROOT = Path(__file__).resolve().parent.parent
SE = Path(os.environ.get("RESULTS_DIR") or ROOT / "results")
OUT = SE / "figures"

NAVY, BLUE, LIGHT = "#0F4D92", "#3775BA", "#A9C8EA"
RED, PINK = "#B64342", "#E9A6A1"
LIGHTGRAY, INK, INK2, ARROW = "#E3E3E3", "#272727", "#4D4D4D", "#6B7380"
PANEL, CARD_EDGE = "#F2F4F7", "#CDD3DC"
COL_W, PAGE_W = 3.5, 7.16          # IEEE column and text width, inches

plt.rcParams.update({
    "font.family": ["Liberation Sans", "DejaVu Sans"],
    "font.size": 8, "axes.labelsize": 8, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
    "legend.fontsize": 7.5, "axes.spines.top": False, "axes.spines.right": False,
    "axes.linewidth": 0.8, "axes.edgecolor": INK, "axes.labelcolor": INK, "text.color": INK,
    "xtick.color": INK, "ytick.color": INK, "xtick.major.width": 0.8, "ytick.major.width": 0.8,
    "legend.frameon": False, "hatch.linewidth": 0.5, "hatch.color": INK,
    "pdf.fonttype": 42, "savefig.bbox": "tight", "savefig.pad_inches": 0.02,
})
EDGE = dict(edgecolor=INK, linewidth=0.6)
ERR = dict(ecolor=INK, elinewidth=0.6, capsize=2, capthick=0.6)

delta = json.loads((SE / "delta.json").read_text())["main"]
extra = json.loads((SE / "extra-numbers.json").read_text())


def wilson(k, n, z=1.96):
    p = k / n; d = 1 + z * z / n; c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100 * p, 100 * (c - h), 100 * (c + h)


def save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        fig.savefig(OUT / f"{name}.{ext}", dpi=300)
    plt.close(fig)
    print(f"  wrote {name}.pdf/.png")


# ---------------------------------------------------------------- fig11: RQ2 placebo and dose
def fig11():
    """Share of sites made worse (Δ>0, up) and better (Δ<0, down) by ads, by
    how many listed vendors a site requests; 0 vendors is the placebo."""
    pd = delta["placebo_dose"]
    bins = [("0", "0"), ("1-2", "1–2"), ("3-5", "3–5"), ("6-10", "6–10"), (">10", ">10")]
    fig, ax = plt.subplots(figsize=(COL_W, 1.6))
    xs = range(len(bins))
    for x, (key, lab) in zip(xs, bins):
        b = pd[key]; n = b["n"]
        for share, sign, color, hatch in [(b["pos"], 1, RED, None), (b["neg"], -1, LIGHT, "////")]:
            p, lo, hi = wilson(round(share * n / 100), n)
            ax.bar(x, sign * p, width=0.56, color=color, hatch=hatch, **EDGE)
            ax.errorbar(x, sign * p, yerr=[[p - lo], [hi - p]], fmt="none", **ERR)
        ax.text(x, b["pos"] + 9, f"net {b['net']:+.1f}".replace("-", "−"), ha="center", va="bottom", fontsize=7,
                fontweight="bold", color=RED if b["net"] > 0 else INK2)
    ax.axhline(0, color=INK, linewidth=0.8)
    ax.set_xticks(list(xs), [f"{lab}\n(N = {pd[k]['n']:,})" for k, lab in bins], fontsize=7)
    ax.tick_params(axis="x", length=0)
    ax.set_ylim(-25, 100)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{abs(v):.0f}"))
    ax.set_ylabel("% of sites")
    ax.set_xlabel("Listed vendors the site requests")
    ax.legend(handles=[Patch(facecolor=RED, **EDGE, label="More new violations with ads"),
                       Patch(facecolor=LIGHT, hatch="////", **EDGE, label="Fewer")],
              loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, handlelength=1.4, borderaxespad=0.1)
    save(fig, "fig11-vendor-dose")


# ---------------------------------------------------------------- fig12: RQ2 structure split
def fig12():
    """Structure-rule shares attributed to ads, on the 5% of sites with the
    largest excess and on the rest: counterfactual in both visit orders vs.
    selector matching."""
    ss = delta["structure_split"]
    groups = [("top5", f"{ss['top5']['sites']:,} sites with the\nlargest excess (5%)"),
              ("rest", f"Other {ss['rest']['sites']:,} sites\n(95%)")]
    series = [("cf_normal_first", "Blocking, normal visit first", NAVY, None),
              ("cf_blocked_first", "Blocking, blocked visit first", LIGHT, "////"),
              ("selector_ad", "Selector, ad tokens", PINK, None),
              ("selector_ad_or_sponsored", "Selector, + sponsored", RED, "\\\\\\\\")]
    fig, ax = plt.subplots(figsize=(COL_W, 1.65))
    w = 0.19
    for i, (key, lab, color, hatch) in enumerate(series):
        xs = [g + (i - 1.5) * w for g in range(len(groups))]
        vals = [ss[g][key] for g, _ in groups]
        ax.bar(xs, vals, width=w * 0.92, color=color, hatch=hatch, label=lab, **EDGE)
        for x, v in zip(xs, vals):
            ax.text(x, v + 1.5, f"{v:.0f}", ha="center", va="bottom", fontsize=6.5)
    ax.set_xticks(range(len(groups)), [lab for _, lab in groups], fontsize=7)
    ax.tick_params(axis="x", length=0)
    ax.set_ylim(0, 82)
    ax.set_ylabel("% of new structure-rule\nviolations credited to ads")
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, handlelength=1.4,
              borderaxespad=0.1, columnspacing=1.0)
    save(fig, "fig12-structure-split")


# ---------------------------------------------------------------- fig7: RQ3 rules inside frames
def fig7():
    per = extra["inframe"]["frame_rules"]["per_rule"][:8]
    fig, ax = plt.subplots(figsize=(COL_W, 1.45))
    ys = range(len(per))
    ven = [r["vendor_nodes"] for r in per]
    oth = [r["other_nodes"] for r in per]
    ax.barh(list(ys), ven, height=0.62, color=NAVY, label="Frame from a listed vendor", **EDGE)
    ax.barh(list(ys), oth, left=ven, height=0.62, color=LIGHT, hatch="////", label="Other frame", **EDGE)
    for y, r in zip(ys, per):
        ax.text(r["nodes"] + 25, y, f"{r['nodes']:,}", va="center", fontsize=7)
    ax.set_yticks(list(ys), [r["rule"] for r in per], fontsize=7)
    ax.tick_params(axis="y", length=0)
    ax.invert_yaxis()
    ax.set_xlim(0, max(r["nodes"] for r in per) * 1.15)
    ax.set_xlabel("Violating nodes inside visible cross-origin frames")
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, handlelength=1.4, borderaxespad=0.1)
    save(fig, "fig7-oopif-vs-mainframe")


# ---------------------------------------------------------------- fig8: pipeline
def _card(ax, x, y, w, h, fc="white", ec=CARD_EDGE, r=0.05, lw=0.6):
    ax.add_patch(FancyBboxPatch((x + r, y + r), w - 2 * r, h - 2 * r, boxstyle=f"round,pad={r}",
                                fc=fc, ec=ec, lw=lw))


def _arrow(ax, a, b, head=True):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>" if head else "-", mutation_scale=7,
                                 lw=0.8, color=ARROW, shrinkA=0, shrinkB=0))


def _lines(ax, x, y, items, ha="left", step=0.118):
    """items: (text, size, weight, color); y is the first baseline, going down."""
    for text, size, weight, color in items:
        ax.text(x, y, text, ha=ha, va="baseline", fontsize=size, fontweight=weight, color=color)
        y -= step if size < 7 else step + 0.02


def fig8():
    vendors = json.loads((ROOT / "data/ad-vendors.json").read_text())["vendors"]
    n_vend, n_pat = len(vendors), sum(len(v["patterns"]) for v in vendors)
    pop = extra["facts"]["stratum_popularity"]
    records = sum(delta["records"].values())
    sites = delta["rq1_any_visit"][3]
    inframe_records = extra["inframe"]["records"]

    W, H = PAGE_W, 2.4
    fig = plt.figure(figsize=(W, H))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
    pw, gap = 1.30, (W - 5 * 1.30) / 4
    xs = [i * (pw + gap) for i in range(5)]
    cx = lambda i: xs[i] + 0.08                      # card left
    cw = pw - 0.16                                  # card width
    top, bot = 2.08, 0.10                           # card area
    T, B, S = 7, 6.3, 7.5                           # title, body, section sizes
    for i, name in enumerate(["SITES", "CRAWLS", "AUDIT", "RECORDS", "ANALYSES"]):
        ax.add_patch(FancyBboxPatch((xs[i] + 0.06, 0.08), pw - 0.12, H - 0.16, boxstyle="round,pad=0.06",
                                    fc=PANEL, ec="none"))
        ax.text(xs[i] + 0.09, 2.22, str(i + 1), fontsize=7.5, fontweight="bold", color=BLUE, va="baseline")
        ax.text(xs[i] + 0.21, 2.22, name, fontsize=7.5, fontweight="bold", color=INK, va="baseline")

    # 1 Sites
    _card(ax, cx(0), bot, cw, top - bot)
    m = cx(0) + cw / 2
    _lines(ax, m, 1.88, [("Site sample", S, "bold", INK)], ha="center")
    _lines(ax, m, 1.62, [("CrUX top-1M", T, "bold", INK), ("popularity ranks", B, "normal", INK2)], ha="center")
    _lines(ax, m, 1.28, [("∩ news domains", T, "bold", INK), ("GDELT 7-day window", B, "normal", INK2),
                         ("and Wikidata", B, "normal", INK2)], ha="center")
    _lines(ax, m, 0.83, [(f"{sum(p['sites'] for p in pop.values()):,} sites", T, "bold", INK),
                         (f"{pop['us-en']['sites']:,} US/generic", B, "normal", INK2),
                         (f"{pop['eu']['sites']:,} Europe", B, "normal", INK2),
                         (f"{pop['row']['sites']:,} rest of world", B, "normal", INK2)], ha="center")

    # 2 Crawls
    h2, g2 = 0.58, (top - bot - 3 * 0.58) / 2
    ys2 = [top - h2, top - 2 * h2 - g2, bot]
    crawl = [("Normal arm", ["ads load as usual", "2 rounds, 8–11 Oct"]),
             ("Blocked arm", [f"requests to {n_vend} vendors", f"({n_pat} patterns) dropped", "2 rounds, 8–11 Oct"]),
             ("In-frame crawl", ["ads load as usual", "1 visit per site, 12–20 Oct"])]
    for y, (title, body) in zip(ys2, crawl):
        _card(ax, cx(1), y, cw, h2)
        _lines(ax, cx(1) + 0.09, y + h2 - 0.16, [(title, T, "bold", INK)] + [(b, B, "normal", INK2) for b in body])

    # 3 Audit
    _card(ax, cx(2), bot, cw, top - bot)
    _lines(ax, cx(2) + 0.09, 1.90, [("Every visit", S, "bold", INK)])
    steps = [("1", "Baseline audit", ["axe-core 4.13 after", "load + 7 s"], 1.65),
             ("2", "Exercise", ["scroll, 25-s wait,", "40 Tabs, hover"], 1.27),
             ("3", "Post audit", ["new if (rule, selector)", "absent at baseline"], 0.89),
             ("+", "In-frame crawl", ["axe inside each", "cross-origin frame"], 0.51)]
    for num, title, body, y in steps:
        ax.add_patch(Circle((cx(2) + 0.15, y + 0.03), 0.062, fc=NAVY, ec="none"))
        ax.text(cx(2) + 0.15, y + 0.03, num, ha="center", va="center", fontsize=6, fontweight="bold", color="white")
        _lines(ax, cx(2) + 0.27, y, [(title, T, "bold", INK)] + [(b, B, "normal", INK2) for b in body])

    # 4 Records
    mid = (top + bot) / 2
    rh = 0.92
    _card(ax, cx(3), mid - rh / 2, cw, rh, fc=NAVY, ec=NAVY)
    m = cx(3) + cw / 2
    _lines(ax, m, mid + 0.22, [("Crawl records", S, "bold", "white"),
                              (f"{records:,} sessions", B + 0.4, "normal", "#E6EEFB"),
                              (f"{sites:,} sites included", B + 0.4, "normal", "#E6EEFB"),
                              (f"{inframe_records:,} in-frame visits", B + 0.4, "normal", "#E6EEFB")], ha="center")

    # 5 Analyses
    h5, g5 = 0.50, (top - bot - 3 * 0.50) / 2
    ys5 = [top - h5 - k * (h5 + g5) for k in range(3)]
    sh, rg = delta["shares"], delta["by_region"]
    fr = extra["inframe"]["frame_rules"]["any_visible"]
    rqs = [("RQ1", "Missed failures", [f"{delta['rq1_new_rule_any'][0]:.1f}% of sites: a rule that", "did not fail at load fails"]),
           ("RQ2", "Ads' share", [f"net excess {delta['noise']['excess_minus_aa']:.0f}–{delta['order_check']['reverse']['net']:.0f} points", "after order correction"]),
           ("RQ3", "Inside frames", [f"{fr['pct']:.1f}% of sites: a visible", "frame fails WCAG"])]
    for y, (rq, title, body) in zip(ys5, rqs):
        _card(ax, cx(4), y, cw, h5)
        ax.text(cx(4) + 0.09, y + h5 - 0.14, rq, fontsize=T, fontweight="bold", color=BLUE, va="baseline")
        ax.text(cx(4) + 0.37, y + h5 - 0.14, title, fontsize=T, fontweight="bold", color=INK, va="baseline")
        ax.text(cx(4) + 0.09, y + 0.165, body[0], fontsize=B, color=NAVY, va="baseline")
        ax.text(cx(4) + 0.09, y + 0.055, body[1], fontsize=B, color=NAVY, va="baseline")

    # arrows
    right = lambda i: cx(i) + cw
    for y in ys2:
        yc = y + h2 / 2
        _arrow(ax, (right(0), yc), (cx(1), yc))
        _arrow(ax, (right(1), yc), (cx(2), yc))
    _arrow(ax, (right(2), mid), (cx(3), mid))
    xm = (right(3) + cx(4)) / 2
    centers = [y + h5 / 2 for y in ys5]
    _arrow(ax, (right(3), mid), (xm, mid), head=False)
    _arrow(ax, (xm, centers[0]), (xm, centers[-1]), head=False)
    for yc in centers:
        _arrow(ax, (xm, yc), (cx(4), yc))
    save(fig, "fig8-pipeline-overview")


# ---------------------------------------------------------------- fig14: RQ1 whether vs. what
def fig14():
    """RQ1 as a narrowing: nearly every site fails at load; what changes after
    use is which rules fail. Any included visit (navy) and a majority of
    visits (light, hatched) where the outcome is defined per visit."""
    rows = list(csv.DictReader(open(SE / "rq1-per-site.csv")))
    n = len(rows)
    count = lambda col: sum(int(r[col]) for r in rows)
    bf, nf, pf = delta["baseline_facts"], delta["new_rule_frames"], delta["pass_then_fail"]["wcag"]
    k_load = round(bf["sites_failing_at_load_every_visit_pct"] * n / 100)
    items = [  # label, [(value-triple, color, hatch)]
        ("Fails at load\n(every visit)", [(wilson(k_load, n), LIGHTGRAY, None)]),
        ("≥1 new violation", [(wilson(count("new_any"), n), NAVY, None),
                              (wilson(count("new_majority"), n), LIGHT, "////")]),
        ("≥1 rule that did\nnot fail at load", [(wilson(count("newrule_any"), n), NAVY, None),
                                          (wilson(count("newrule_majority"), n), LIGHT, "////")]),
        ("… only inside\nframes", [(wilson(nf["sites_frames_only"], n), NAVY, None)]),
        ("No WCAG failure at\nload, fails later", [(wilson(pf["sites_fail_later"], n), NAVY, None)]),
    ]
    fig, ax = plt.subplots(figsize=(COL_W, 1.7))
    for y, (_, bars) in enumerate(items):
        offs = [0.0] if len(bars) == 1 else [-0.19, 0.19]
        for off, (v, color, hatch) in zip(offs, bars):
            h = 0.42 if len(bars) == 1 else 0.34
            ax.barh(y + off, v[0], height=h, color=color, hatch=hatch,
                    xerr=[[v[0] - v[1]], [v[2] - v[0]]], error_kw=ERR, **EDGE)
            ax.text(v[2] + 1.2, y + off, f"{v[0]:.1f}%", va="center", fontsize=7.5)
    ax.axhline(0.5, color=INK2, linewidth=0.6, linestyle=(0, (3, 2)))
    ax.set_yticks(range(len(items)), [lab for lab, _ in items], fontsize=7)
    ax.tick_params(axis="y", length=0)
    ax.invert_yaxis()
    ax.set_xlim(0, 112)
    ax.set_xticks(range(0, 101, 20))
    ax.set_xlabel(f"% of sites (N = {n:,}), Wilson 95% CI")
    ax.legend(handles=[Patch(facecolor=LIGHTGRAY, **EDGE, label="Load-time audit"),
                       Patch(facecolor=NAVY, **EDGE, label="Any included visit"),
                       Patch(facecolor=LIGHT, hatch="////", **EDGE, label="Majority of visits")],
              loc="lower left", bbox_to_anchor=(-0.02, 1.0), ncol=3, handlelength=1.4,
              columnspacing=1.0, borderaxespad=0.1)
    save(fig, "fig14-rq1-whether-what")


# ---------------------------------------------------------------- fig15: sample flow, every denominator
def fig15():
    """From the sample to the sites behind each research question."""
    recs, inc = delta["records"], delta["included_visits"]
    pop = extra["facts"]["stratum_popularity"]
    sampled = sum(p["sites"] for p in pop.values())
    n_drop = delta["sample"]["platform_domains"]
    analysed = sampled - n_drop
    sites, paired = delta["rq1_any_visit"][3], delta["sites"]
    nz, oc = delta["noise"], delta["order_check"]
    fr = extra["inframe"]
    W, H = COL_W, 1.84
    fig = plt.figure(figsize=(W, H))
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
    T, B = 7, 6.3
    # tier 1: sample
    _card(ax, 0.55, 1.49, 2.40, 0.35)
    _lines(ax, W / 2, 1.715, [(f"{analysed:,} sites", T, "bold", INK),
                              (f"{sampled:,} sampled; {n_drop} platform and government domains dropped", B - 0.5, "normal", INK2)],
           ha="center", step=0.115)
    # tier 2: crawls
    _card(ax, 0.02, 0.79, 2.20, 0.58, fc=PANEL)
    _lines(ax, 0.12, 1.24, [("Main crawl", T, "bold", INK)])
    ax.text(0.86, 1.24, "2 rounds per arm", fontsize=B, color=INK2, va="baseline")
    for x, arm in ((0.12, "normal"), (1.17, "blocked")):
        _lines(ax, x, 1.09, [(f"{arm.capitalize()} arm", B, "bold", NAVY),
                             (f"{inc[arm]:,} of {recs[arm]:,}", B, "normal", INK),
                             ("visits included", B, "normal", INK2)], step=0.115)
    _card(ax, 2.32, 0.79, 1.16, 0.58, fc=PANEL)
    _lines(ax, 2.42, 1.24, [("In-frame crawl", T, "bold", INK)])
    _lines(ax, 2.42, 1.09, [("1 visit per site", B, "normal", INK2), (f"{fr['records']:,} visits", B, "normal", INK)],
           step=0.125)
    # tier 3: research questions
    y3, h3 = 0.02, 0.62
    cards = [(0.02, 0.90, "RQ1", [(f"{sites:,} sites", T, "bold", INK), ("included", B, "normal", INK2)]),
             (0.98, 1.24, "RQ2", [(f"{paired:,} paired sites", T, "bold", INK),
                                   (f"{nz['sites']:,}: identical visits", B, "normal", INK2),
                                   (f"{oc['reverse_pairs']:,}: reversed pairs", B, "normal", INK2)]),
             (2.32, 1.16, "RQ3", [(f"{fr['sites']:,} sites", T, "bold", INK), ("included", B, "normal", INK2)])]
    for x, w, rq, lines in cards:
        _card(ax, x, y3, w, h3)
        ax.text(x + 0.10, y3 + h3 - 0.16, rq, fontsize=T, fontweight="bold", color=BLUE, va="baseline")
        _lines(ax, x + 0.10, y3 + h3 - 0.31, lines, step=0.115)
    # arrows
    _arrow(ax, (1.12, 1.49), (1.12, 1.37))
    _arrow(ax, (2.90, 1.49), (2.90, 1.37))
    _arrow(ax, (0.47, 0.79), (0.47, y3 + h3))
    _arrow(ax, (1.60, 0.79), (1.60, y3 + h3))
    _arrow(ax, (2.90, 0.79), (2.90, y3 + h3))
    save(fig, "fig15-sample-flow")

# ---------------------------------------------------------------- figure-numbers.json (analysis/figure_numbers.py)
def _fign():
    return json.loads((SE / "figure-numbers.json").read_text())


# ---------------------------------------------------------------- fig16: RQ1 which rules newly fail
def fig16():
    """Top rules that newly fail (failed nowhere at load), by site, split into
    sites where the rule fails in the top-level document and sites where it
    fails only inside frames; how often it recurs on both visits."""
    r1 = _fign()["rq1_rules"]
    rows, n = r1["rules"][:8], r1["sites"]
    fig, ax = plt.subplots(figsize=(COL_W, 1.72))
    ys = range(len(rows))
    top = [100 * r["top_doc_sites"] / n for r in rows]
    fro = [100 * r["frames_only_sites"] / n for r in rows]
    ax.barh(list(ys), top, height=0.62, color=NAVY, label="Fails in the top-level document", **EDGE)
    ax.barh(list(ys), fro, left=top, height=0.62, color=LIGHT, hatch="////", label="Only inside frames", **EDGE)
    for y, r in zip(ys, rows):
        ax.text(r["pct"] + 0.3, y, f"{r['pct']:.1f}%", va="center", fontsize=7)
        ax.text(16.6, y, f"{r['recur_pct']:.0f}%", va="center", ha="right", fontsize=7, color=INK2)
    ax.text(16.6, -0.95, "recurs", ha="right", va="center", fontsize=6.5, color=INK2, fontweight="bold")
    ax.set_yticks(list(ys), [r["rule"] + ("" if r["wcag"] else "*") for r in rows], fontsize=7)
    ax.tick_params(axis="y", length=0)
    ax.set_ylim(len(rows) - 0.5, -1.45)
    ax.set_xlim(0, 17)
    ax.spines["bottom"].set_bounds(0, 14)
    ax.set_xticks(range(0, 15, 2))
    ax.set_xlabel(f"% of sites (N = {n:,}) where the rule newly fails")
    ax.legend(loc="lower left", bbox_to_anchor=(-0.02, 1.0), ncol=2, handlelength=1.4, borderaxespad=0.1)
    save(fig, "fig16-rq1-new-rules")


# ---------------------------------------------------------------- fig17: RQ2 how large the difference is
def fig17():
    """Share of noise-floor sites whose difference in new violating nodes is at
    least +t (up) or at most -t (down): ads minus blocked in the first paired
    round, and the earlier minus the later of two identical normal visits."""
    dt = _fign()["delta_tails"]
    labels = [">0" if t == 1 else f"≥{t}" for t in dt["thresholds"]]
    fig, ax = plt.subplots(figsize=(COL_W, 1.58))
    w = 0.36
    series = [("ads_minus_blocked", -w / 2, RED, None, "Ads − blocked"),
              ("identical_visits", w / 2, LIGHTGRAY, "////", "Identical visits (earlier − later)")]
    for key, off, color, hatch, lab in series:
        for x, row in enumerate(dt[key]):
            for share, sign in ((row["up"], 1), (row["down"], -1)):
                p = share["pct"]
                ax.bar(x + off, sign * p, width=w, color=color, hatch=hatch, label=lab if (x, sign) == (0, 1) else None,
                       **EDGE)
                ax.errorbar(x + off, sign * p, yerr=[[p - share["lo"]], [share["hi"] - p]] if sign > 0
                            else [[share["hi"] - p], [p - share["lo"]]], fmt="none", **ERR)
                if sign > 0 or x == 0:
                    ax.text(x + off, sign * (share["hi"] + 1.5), f"{p:.0f}" if p >= 1 else f"{p:.1f}",
                            ha="center", va="bottom" if sign > 0 else "top", fontsize=6.3)
    ax.axhline(0, color=INK, linewidth=0.8)
    ax.set_xticks(range(len(labels)), labels)
    ax.tick_params(axis="x", length=0)
    ax.set_ylim(-34, 55)
    ax.set_yticks([-20, 0, 20, 40])
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{abs(v):.0f}"))
    ax.set_ylabel(f"% of sites (N = {dt['sites']:,})")
    ax.set_xlabel("Difference in new violating nodes, in either direction")
    ax.text(5.45, 50, "more with ads /\nearlier visit higher", ha="right", va="top", fontsize=6.3, color=INK2)
    ax.text(5.45, -31, "fewer", ha="right", va="bottom", fontsize=6.3, color=INK2)
    ax.legend(loc="lower left", bbox_to_anchor=(-0.02, 1.0), ncol=2, handlelength=1.4, borderaxespad=0.1)
    save(fig, "fig17-rq2-delta-size")


# ---------------------------------------------------------------- fig18: RQ3 who serves failing frames
ORG_LABEL = {"Google GPT/DoubleClick": "Google ads (GPT)", "Google IMA SDK (ads in video)": "Google IMA (video ads)"}


def fig18():
    """Organisations serving visible cross-origin frames on >= 30 sites: sites
    reached (grey) and sites where that frame fails a rule (listed vendor:
    red; other host: navy), with the failure rate."""
    fo = _fign()["frame_orgs"]
    rows = fo["orgs"]
    fig, ax = plt.subplots(figsize=(COL_W, 1.6))
    ys = range(len(rows))
    ax.barh(list(ys), [r["reach"] for r in rows], height=0.66, color=LIGHTGRAY, **EDGE)
    for y, r in zip(ys, rows):
        ax.barh(y, r["failing"]["k"], height=0.66, color=RED if r["listed"] else NAVY, **EDGE)
        ax.text(r["reach"] + 6, y, f"{r['failing']['k']}/{r['reach']} ({r['failing']['pct']:.0f}%)",
                va="center", fontsize=6.8)
    ax.set_yticks(list(ys), [ORG_LABEL.get(r["org"], r["org"]) for r in rows], fontsize=7)
    ax.tick_params(axis="y", length=0)
    ax.invert_yaxis()
    ax.set_xlim(0, max(r["reach"] for r in rows) * 1.32)
    ax.spines["bottom"].set_bounds(0, 500)
    ax.set_xticks(range(0, 501, 100))
    ax.set_xlabel(f"Sites (of {fo['sites']:,}) with a visible frame from the host")
    ax.legend(handles=[Patch(facecolor=LIGHTGRAY, **EDGE, label="Has a frame"),
                       Patch(facecolor=RED, **EDGE, label="It fails: listed vendor"),
                       Patch(facecolor=NAVY, **EDGE, label="It fails: other host")],
              loc="lower left", bbox_to_anchor=(-0.02, 1.0), ncol=3, handlelength=1.2, columnspacing=0.8,
              borderaxespad=0.1)
    save(fig, "fig18-rq3-frame-orgs")


if __name__ == "__main__":
    for f in (fig8, fig15, fig14, fig16, fig17, fig11, fig12, fig7, fig18):
        f()

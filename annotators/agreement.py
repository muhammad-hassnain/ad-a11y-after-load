#!/usr/bin/env python3
"""Agreement between the three automatic news-publisher annotators.

  rater A  domain rules (annotators/rater_a.py; data/labels/rater-a.json)
  rater B  an LLM, on a 400-site sample stratified by region
           (data/labels/rater-b.json; 395 sites labelled)
  rater C  crawl-feature rules (annotators/rater_c.py;
           results/annotators/rater-c-labels.csv)

Computes pairwise Cohen's κ and Fleiss' κ on the sites all three labelled
(three categories, and binary news vs. other), and κ(A, C) on all sites
both labelled. Writes results/annotators/agreement.{txt,json}.
Usage (repo root): python3 annotators/agreement.py
"""
import csv, json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
A_FILE = ROOT / "data/labels/rater-a.json"
B_FILE = ROOT / "data/labels/rater-b.json"
C_FILE = ROOT / "results/annotators/rater-c-labels.csv"
OUT_TXT = ROOT / "results/annotators/agreement.txt"
OUT_JSON = ROOT / "results/annotators/agreement.json"


def load_a(): return {k: v["label"] for k, v in json.loads(A_FILE.read_text()).items()}
def load_b(): return {k: v["label"] for k, v in json.loads(B_FILE.read_text()).items()}


def load_c():
    with C_FILE.open() as f:
        return {r["host"]: r["rater_c"] for r in csv.DictReader(f)}


LABELS = ["news_publisher", "not_news_publisher", "ambiguous"]


def to_binary(lbl):
    return "news_publisher" if lbl == "news_publisher" else "other"


def kappa_pair(labels_x, labels_y, hosts, binary=False):
    """Cohen's κ on hosts common to both."""
    paired = [(labels_x[h], labels_y[h]) for h in hosts if h in labels_x and h in labels_y]
    if binary:
        paired = [(to_binary(a), to_binary(b)) for a, b in paired]
        cats = ["news_publisher", "other"]
    else:
        cats = LABELS
    n = len(paired)
    if n == 0: return 0.0, 0.0, 0
    agree = sum(1 for a, b in paired if a == b)
    po = agree / n
    cx = Counter(a for a, _ in paired)
    cy = Counter(b for _, b in paired)
    pe = sum((cx[c] / n) * (cy[c] / n) for c in cats)
    kappa = (po - pe) / (1 - pe) if (1 - pe) > 1e-9 else 0.0
    return kappa, po, n


def fleiss_kappa(labels_a, labels_b, labels_c, hosts, binary=False):
    """Fleiss' κ for three raters."""
    if binary:
        f = to_binary; cats = ["news_publisher", "other"]
    else:
        f = lambda x: x; cats = LABELS
    rows = []
    for h in hosts:
        if h in labels_a and h in labels_b and h in labels_c:
            rows.append([f(labels_a[h]), f(labels_b[h]), f(labels_c[h])])
    n = len(rows)
    if n == 0: return 0.0, 0
    N = 3  # raters
    P_items = []
    for row in rows:
        cnt = Counter(row)
        P_items.append((sum(cnt[c] ** 2 for c in cats) - N) / (N * (N - 1)))
    Pbar = sum(P_items) / n
    total = n * N
    pj = {c: sum(row.count(c) for row in rows) / total for c in cats}
    Pe = sum(pj[c] ** 2 for c in cats)
    kappa = (Pbar - Pe) / (1 - Pe) if (1 - Pe) > 1e-9 else 0.0
    return kappa, n


def main():
    A = load_a(); B = load_b(); C = load_c()
    hosts_abc = sorted(set(A) & set(B) & set(C))
    hosts_ac = sorted(set(A) & set(C))
    res = {"labelled": {"A": len(A), "B": len(B), "C": len(C)}}

    lines = ["News-publisher labels: agreement between the three automatic annotators",
             "=" * 72, "",
             f"Rater A: domain rules (domain name, platform blocklist, GDELT and Wikidata"
             f" corpora, lexical patterns).  N = {len(A)}",
             f"Rater B: an LLM.  N = {len(B)}",
             f"Rater C: crawl-feature rules (publisher trackers, consent banner and article"
             f" shape, vendor count, visible text).  N = {len(C)}",
             ""]
    for key, binary, title in (("three_category", False,
                                "3-category κ (news_publisher / not_news_publisher / ambiguous)"),
                               ("binary", True, "2-category κ (news_publisher vs other)")):
        lines.append(f"--- {title}, sites all three labelled ---")
        res[key] = {}
        for (x, y, nx, ny) in [("A", "B", A, B), ("A", "C", A, C), ("B", "C", B, C)]:
            kap, po, n = kappa_pair(nx, ny, hosts_abc, binary=binary)
            res[key][f"{x}{y}"] = {"kappa": round(kap, 3), "observed_agreement": round(po, 3), "n": n}
            lines.append(f"  κ({x}, {y}) = {kap:+.3f}   observed agreement = {po:.3f}   N = {n}")
        kf, nf = fleiss_kappa(A, B, C, hosts_abc, binary=binary)
        res[key]["fleiss"] = {"kappa": round(kf, 3), "n": nf}
        lines.append(f"  Fleiss κ(A, B, C) = {kf:+.3f}   N = {nf}")
        lines.append("")
    lines.append("--- κ(A, C) on all sites both labelled ---")
    kap, po, n = kappa_pair(A, C, hosts_ac)
    kap_b, po_b, n_b = kappa_pair(A, C, hosts_ac, binary=True)
    res["full_AC"] = {"kappa": round(kap, 3), "observed_agreement": round(po, 3), "n": n,
                      "binary_kappa": round(kap_b, 3), "binary_observed_agreement": round(po_b, 3)}
    lines.append(f"  κ(A, C) = {kap:+.3f}   observed agreement = {po:.3f}   N = {n}")
    lines.append(f"  κ(A, C) binary = {kap_b:+.3f}   observed agreement = {po_b:.3f}   N = {n_b}")

    OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    OUT_TXT.write_text("\n".join(lines) + "\n")
    OUT_JSON.write_text(json.dumps(res, indent=2) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

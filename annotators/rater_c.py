#!/usr/bin/env python3
"""Rater C: news-publisher labels from features of the crawled page.

Uses only what the main crawl observed on the site's first normal-arm
visit (the vendors it requested and the gating probe's page features),
nothing from the domain name or the news corpora, so its features are
disjoint from rater A's.

Rules (first match wins):

1. Publisher-tracker signal: >= 2 of {Chartbeat, Parse.ly, Taboola,
   Outbrain, Comscore, Nielsen, Adobe Audience Manager, MOAT} requested
   -> news_publisher (content-recommendation and audience-analytics stacks
   that publishers use).
2. Consent banner and article shape: `gating.cmpHits` non-empty AND
   `visibleTextLen` >= 2,000 AND `contentContainers` >= 3 -> news_publisher.
4. Little text: `visibleTextLen` < 500 OR `shortPage` -> not_news_publisher.
5. Vendor floor: <= 1 listed vendor and no publisher tracker
   -> not_news_publisher (ad-funded publishers load many vendors).
3. Long static text: no publisher tracker AND <= 3 vendors AND
   `visibleTextLen` >= 10,000 -> ambiguous.
6. Default -> ambiguous.

(Rules keep their original numbers; they are tested in the order listed.)

Usage (repo root): python3 annotators/rater_c.py
Writes results/annotators/rater-c-labels.csv.
"""
import argparse, csv, json, os
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRAWL = Path(os.environ.get("CRAWL_DIR") or ROOT / "crawl")

PUBLISHER_TRACKERS = {
    "Chartbeat",
    "Parse.ly",
    "Taboola",
    "Outbrain",
    "Comscore",
    "Nielsen",
    "Adobe Audience Manager",
    "MOAT",
}


def classify_one(j: dict) -> str:
    vendors = set((j.get("vendors") or {}).keys())
    pub_hit = vendors & PUBLISHER_TRACKERS

    gating = j.get("gating") or {}
    cmp_hits = gating.get("cmpHits") or []
    txt = gating.get("visibleTextLen", 0) or 0
    cc = gating.get("contentContainers", 0) or 0
    short = gating.get("shortPage", False)

    # Rule 1
    if len(pub_hit) >= 2:
        return "news_publisher"
    # Rule 2
    if cmp_hits and txt >= 2000 and cc >= 3:
        return "news_publisher"
    # Rule 4
    if txt < 500 or short:
        return "not_news_publisher"
    # Rule 5
    if len(vendors) <= 1 and not pub_hit:
        return "not_news_publisher"
    # Rule 3
    if not pub_hit and len(vendors) <= 3 and txt >= 10000:
        return "ambiguous"
    # Default
    return "ambiguous"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persite-dir", default=str(CRAWL / "main/arm-normal/per-site"))
    ap.add_argument("--out", default=str(ROOT / "results/annotators/rater-c-labels.csv"))
    args = ap.parse_args()

    files = sorted(Path(args.persite_dir).glob("*__v*.json"))
    if not files:
        raise SystemExit(f"no crawl records in {args.persite_dir} (see README: Crawl data)")
    # One label per host, from its first recorded visit (v1, else v2, v3).
    rows = []
    seen = set()
    for p in files:
        host, _, rest = p.name.rpartition("__v")
        if host in seen:
            continue
        seen.add(host)
        try:
            j = json.loads(p.read_text())
            label = classify_one(j)
            rows.append({"host": host, "rater_c": label})
        except Exception:
            rows.append({"host": host, "rater_c": "error"})

    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    with outp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["host", "rater_c"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"Rater C labelled {len(rows)} hosts -> {outp}")
    cnt = Counter(r["rater_c"] for r in rows)
    for k, v in cnt.most_common():
        print(f"  {k:<22} {v:>5}  ({100*v/len(rows):.1f}%)")


if __name__ == "__main__":
    main()

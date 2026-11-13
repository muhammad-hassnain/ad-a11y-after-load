#!/usr/bin/env python3
"""filter_platforms.py — drop platform and government domains from the sample.

A site is dropped if its registrable domain (eTLD+1) is on the platform
blocklist of sample/fetch_wikidata_v2.py (apple.com, google.com, github.com,
archive.org, wikipedia.org, amazon.*, linkedin.com, x.com, t.me, medium.com,
wordpress.com, government sites such as cdc.gov and gov.uk, sports leagues,
shopping and booking platforms, ...). Of the 2,500 sampled sites, 35 are
dropped; the other 2,465 are the sites the paper analyses.

Usage (repo root):
    python3 sample/filter_platforms.py      # data/sites-crux-news.txt -> data/sites-crux-news-filtered.txt
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_wikidata_v2 import is_blocked  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default="data/sites-crux-news.txt")
    ap.add_argument("--out", default="data/sites-crux-news-filtered.txt")
    args = ap.parse_args()
    sites = [l.strip() for l in open(args.sites) if l.strip() and not l.startswith("#")]
    keep = [s for s in sites if not is_blocked(s)]
    dropped = sorted(set(sites) - set(keep))
    with open(args.out, "w") as f:
        f.write(f"# news-only subset: original {len(sites)} with {len(dropped)} platform-blocklist domains removed\n")
        f.write(f"# dropped by blocklist: {dropped}\n")
        for s in keep:
            f.write(s + "\n")
    print(f"{len(sites)} sites, {len(dropped)} dropped, {len(keep)} kept -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()

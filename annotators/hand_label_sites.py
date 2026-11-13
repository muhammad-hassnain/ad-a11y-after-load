#!/usr/bin/env python3
"""The 50 sites the author labelled by hand.

They are a random sample (seed 20261011) of the 395 sites rater B labelled,
which in turn come from a 400-site sample stratified by region
(data/labels/rater-b-sites.txt: 200 US or generic, 100 Europe, 100 rest of
the world). This script redraws the 50 sites and checks them against the
hand labels in data/labels/hand-labels-50.json.

Usage (repo root): python3 annotators/hand_label_sites.py
"""
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    b = json.loads((ROOT / "data/labels/rater-b.json").read_text())
    random.seed(2026_10_11)
    sample = random.sample(sorted(b), 50)
    hand = json.loads((ROOT / "data/labels/hand-labels-50.json").read_text())
    for h in sample:
        print(h)
    if set(sample) != set(hand):
        sys.exit(f"the redrawn sample differs from the hand-labelled sites: {sorted(set(sample) ^ set(hand))}")
    print(f"the redrawn {len(sample)} sites match data/labels/hand-labels-50.json", file=sys.stderr)


if __name__ == "__main__":
    main()

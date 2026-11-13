#!/usr/bin/env bash
# Reproduce every number and figure in the paper from the crawl data.
#
#   1. annotators: rater A (checked against data/labels/rater-a.json),
#      rater C (from the main crawl) and their agreement with rater B;
#      the draw of the 50 hand-labelled sites
#   2. analysis/delta.py          -> results/delta.{json,txt}, delta-per-site.csv, rq1-per-site.csv
#      analysis/extra_numbers.py  -> results/extra-numbers.{json,txt}
#      analysis/figure_numbers.py -> results/figure-numbers.{json,txt}
#   3. scripts/check_numbers.py checks every number the paper reports
#   4. scripts/make_figures.py    -> results/figures/
#
# Needs the crawl data under crawl/ (see README: Crawl data), Python 3 with
# numpy and matplotlib (requirements.txt), and node_modules from `npm ci`
# (the in-frame numbers read axe-core's rule metadata; set NODE_MODULES to
# use another install). Set PY to choose the Python interpreter.
# Takes about four minutes.
set -euo pipefail
cd "$(dirname "$0")/.."
START=$(date +%s)

PY="${PY:-python3}"
CRAWL="${CRAWL_DIR:-crawl}"
NM="${NODE_MODULES:-node_modules}"

missing=0
for d in main/arm-normal/per-site main/arm-blocked/per-site inframe/arm-normal/per-site; do
  if [ -z "$(ls -A "$CRAWL/$d" 2>/dev/null)" ]; then
    echo "missing crawl data: $CRAWL/$d" >&2; missing=1
  fi
done
if [ "$missing" = 1 ]; then
  cat >&2 <<'EOF'
Download the release assets crawl-main-normal.tar.zst, crawl-main-blocked.tar.zst,
crawl-inframe-normal.tar.zst and SHA256SUMS, check them with `sha256sum -c SHA256SUMS`,
and extract each at the repository root (`tar --zstd -xf <file>`); see README.md.
EOF
  exit 2
fi
for f in axe-core/axe.js playwright-core/browsers.json; do
  [ -f "$NM/$f" ] || { echo "missing $NM/$f: run 'npm ci' (or set NODE_MODULES)" >&2; exit 2; }
done
"$PY" -c "import numpy, matplotlib" 2>/dev/null \
  || { echo "$PY lacks numpy or matplotlib: pip install -r requirements.txt" >&2; exit 2; }

echo "== crawlers and versions"
echo "  main crawl (both arms, rounds 1-3, 8-11 Oct 2026): harness/crawl-main.js"
echo "  in-frame crawl (normal arm, 12-20 Oct 2026):       harness/crawl-inframe.js"
"$PY" - "$NM" <<'PY'
import json, sys
lock = json.load(open("package-lock.json"))["packages"]
br = json.load(open(f"{sys.argv[1]}/playwright-core/browsers.json"))["browsers"]
print("  playwright", lock["node_modules/playwright"]["version"],
      "| axe-core", lock["node_modules/axe-core"]["version"],
      "| @axe-core/playwright", lock["node_modules/@axe-core/playwright"]["version"],
      "| chromium", next(b["browserVersion"] for b in br if b["name"] == "chromium"))
PY

echo
echo "== annotators"
"$PY" annotators/rater_a.py > /dev/null
cmp -s results/annotators/rater-a.json data/labels/rater-a.json \
  || { echo "rater A labels differ from data/labels/rater-a.json" >&2; exit 1; }
echo "  rater A reproduces data/labels/rater-a.json"
"$PY" annotators/rater_c.py > /dev/null
echo "  wrote results/annotators/rater-c-labels.csv"
"$PY" annotators/agreement.py > /dev/null
echo "  wrote results/annotators/agreement.{json,txt}"
out=$("$PY" annotators/hand_label_sites.py 2>&1 > /dev/null) || { echo "$out" >&2; exit 1; }
echo "  hand labels: $out"

echo
echo "== analysis/delta.py"
"$PY" analysis/delta.py > /dev/null
echo "  wrote results/delta.{json,txt}, delta-per-site.csv, rq1-per-site.csv"

echo
echo "== analysis/extra_numbers.py"
"$PY" analysis/extra_numbers.py > /dev/null
echo "  wrote results/extra-numbers.{json,txt}"

echo
echo "== analysis/figure_numbers.py"
"$PY" analysis/figure_numbers.py > /dev/null
echo "  wrote results/figure-numbers.{json,txt}"

echo
echo "== scripts/check_numbers.py"
"$PY" scripts/check_numbers.py

echo
echo "== scripts/make_figures.py"
"$PY" scripts/make_figures.py

echo
echo "done in $(( $(date +%s) - START )) s"

#!/usr/bin/env bash
# The main crawl: every site of the sample in the normal and the blocked arm,
# three rounds, alternating arms:
#   normal 1 -> blocked 1 -> normal 2 -> blocked 2 -> normal 3 -> blocked 3
# so that the two visits of a pair (same round) are hours apart and the
# normal visit comes first. The original crawl ran 8-11 October 2026 on one
# machine (about a day per arm and round at concurrency 6).
#
# For each arm and round:
#   1. a pass over all sites (harness/crawl-main.js, 150 s per site,
#      concurrency $CONC, browser recycled every $RECYCLE contexts); the
#      pass is restarted up to $RETRY times if Node exits with an error and
#      skips sites that already have a record;
#   2. a second pass over the sites that still have no record (they timed
#      out), with 300 s per site and concurrency 2. Failed navigations leave
#      a record and are not retried.
# In the original crawl, the second pass of a cell ran while the next cell's
# first pass started; here they run one after the other.
#
# Writes crawl/main/arm-{normal,blocked}/per-site/<host>__v<round>.json
# (plus crawl.jsonl and one log per round). Resumable: re-run after an
# interruption; finished cells are skipped (visit-<round>.done).
#
# Usage (repo root, after `npm ci` and `npx playwright install chromium`):
#   bash scripts/crawl_main.sh
# Options (environment): SITES (default data/sites-crux-news.txt), OUT
# (default crawl/main), CONC (6), RECYCLE (200), RETRY (5), SWEEP_CONC (2),
# SWEEP_TIMEOUT (300000 ms), ROUNDS (default "1 2 3").
set -euo pipefail
cd "$(dirname "$0")/.."

: "${SITES:=data/sites-crux-news.txt}"
: "${OUT:=crawl/main}"
: "${CONC:=6}"
: "${RECYCLE:=200}"
: "${RETRY:=5}"
: "${SWEEP_CONC:=2}"
: "${SWEEP_TIMEOUT:=300000}"
: "${ROUNDS:=1 2 3}"

command -v node >/dev/null || { echo "node not found (Node 20 is needed)" >&2; exit 1; }
[ -d node_modules/playwright ] || { echo "run 'npm ci' and 'npx playwright install chromium' first" >&2; exit 1; }
[ -f "$SITES" ] || { echo "sample file not found: $SITES" >&2; exit 1; }

echo "=== main crawl START $(date -u +%FT%TZ)  sites=$SITES concurrency=$CONC"
for v in $ROUNDS; do
  for arm in normal blocked; do
    out="$OUT/arm-$arm"
    log="$out/visit-$v.log"
    mkdir -p "$out"
    if [[ -f "$out/visit-$v.done" ]]; then
      echo "[$(date -u +%FT%TZ)] skip arm=$arm round=$v (done)"; continue
    fi

    echo "[$(date -u +%FT%TZ)] start arm=$arm round=$v -> $out"
    attempt=0; prev_n=-1
    while (( attempt < RETRY )); do
      attempt=$((attempt + 1))
      echo "[$(date -u +%FT%TZ)] attempt $attempt/$RETRY" >> "$log"
      set +e
      node harness/crawl-main.js --sites "$SITES" --out "$out" --arm "$arm" --visit "$v" \
        --concurrency "$CONC" --recycle-every "$RECYCLE" --resume true >> "$log" 2>&1
      rc=$?
      set -e
      echo "[$(date -u +%FT%TZ)] node rc=$rc" >> "$log"
      (( rc == 0 )) && break
      # stop retrying if an attempt made no progress
      cur_n=$(ls "$out/per-site/" 2>/dev/null | grep -c "__v${v}\.json$" || true)
      (( cur_n == prev_n )) && { echo "no progress; giving up on this cell" >> "$log"; break; }
      prev_n=$cur_n
      sleep 10
    done

    # second pass: sites without a record for this round
    missing="$out/visit-$v.missing.txt"
    python3 - "$SITES" "$out/per-site" "$v" > "$missing" <<'PY'
import os, re, sys
sites, d, v = sys.argv[1], sys.argv[2], sys.argv[3]
done = {f[:-len(f"__v{v}.json")] for f in os.listdir(d) if f.endswith(f"__v{v}.json")}
for ln in open(sites):
    s = ln.strip()
    if s and not s.startswith("#") and re.sub(r"[^a-z0-9.-]", "_", s, flags=re.I) not in done:
        print(s)
PY
    if [[ -s "$missing" ]]; then
      echo "[$(date -u +%FT%TZ)] second pass arm=$arm round=$v: $(wc -l < "$missing") sites"
      node harness/crawl-main.js --sites "$missing" --out "$out" --arm "$arm" --visit "$v" \
        --concurrency "$SWEEP_CONC" --timeout "$SWEEP_TIMEOUT" --recycle-every 50 --resume true \
        >> "$log" 2>&1 || echo "[$(date -u +%FT%TZ)] second pass: node exited $? (continuing)"
    fi
    touch "$out/visit-$v.done"
    echo "[$(date -u +%FT%TZ)] done  arm=$arm round=$v ($(ls "$out/per-site/" | grep -c "__v${v}\.json$") records)"
  done
done
echo "=== main crawl COMPLETE $(date -u +%FT%TZ)"

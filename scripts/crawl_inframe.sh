#!/usr/bin/env bash
# The in-frame crawl: one normal-arm visit per site with
# harness/crawl-inframe.js, which also audits inside each cross-origin frame.
# The original crawl ran 12-20 October 2026 on one machine (concurrency 3,
# 180 s per site).
#
#   1. a pass over all sites, skipping sites that already have a record;
#   2. a retry pass over sites without a record or whose navigation failed
#      (no HTTP status, e.g. a DNS error); a retried visit replaces the
#      failed record.
#
# Writes crawl/inframe/arm-normal/per-site/<host>__v1.json (plus
# crawl.jsonl and a log). Resumable: re-run after an interruption.
#
# Usage (repo root, after `npm ci` and `npx playwright install chromium`):
#   bash scripts/crawl_inframe.sh
# Options (environment): SITES (default data/sites-crux-news.txt), OUT
# (default crawl/inframe), CONC (3), TIMEOUT (180000 ms).
set -euo pipefail
cd "$(dirname "$0")/.."

: "${SITES:=data/sites-crux-news.txt}"
: "${OUT:=crawl/inframe}"
: "${CONC:=3}"
: "${TIMEOUT:=180000}"

command -v node >/dev/null || { echo "node not found (Node 20 is needed)" >&2; exit 1; }
[ -f node_modules/axe-core/axe.min.js ] || { echo "run 'npm ci' and 'npx playwright install chromium' first" >&2; exit 1; }
[ -f "$SITES" ] || { echo "sample file not found: $SITES" >&2; exit 1; }

out="$OUT/arm-normal"
log="$out/visit-1.log"
mkdir -p "$out"

echo "=== in-frame crawl START $(date -u +%FT%TZ)  sites=$SITES concurrency=$CONC"
node harness/crawl-inframe.js --sites "$SITES" --out "$out" --arm normal --visit 1 \
  --concurrency "$CONC" --timeout "$TIMEOUT" --resume true >> "$log" 2>&1 \
  || echo "[$(date -u +%FT%TZ)] node exited $? (continuing)"

retry="$out/visit-1.retry.txt"
python3 - "$SITES" "$out/per-site" > "$retry" <<'PY'
import json, os, re, sys
sites, d = sys.argv[1], sys.argv[2]
for ln in open(sites):
    s = ln.strip()
    if not s or s.startswith("#"):
        continue
    p = os.path.join(d, re.sub(r"[^a-z0-9.-]", "_", s, flags=re.I) + "__v1.json")
    try:
        if json.load(open(p)).get("status") is not None:
            continue
    except (OSError, ValueError):
        pass
    print(s)
PY
if [[ -s "$retry" ]]; then
  echo "[$(date -u +%FT%TZ)] retry pass: $(wc -l < "$retry") sites"
  # no --resume: a retried visit overwrites the failed record
  node harness/crawl-inframe.js --sites "$retry" --out "$out" --arm normal --visit 1 \
    --concurrency "$CONC" --timeout "$TIMEOUT" >> "$log" 2>&1 \
    || echo "[$(date -u +%FT%TZ)] node exited $? (continuing)"
fi
echo "=== in-frame crawl COMPLETE $(date -u +%FT%TZ) ($(ls "$out/per-site/" | wc -l) records)"

#!/usr/bin/env python3
"""sample.py — the 2,500-site sample.

Reads the Chrome UX Report (CrUX) origins up to a popularity rank bucket
from BigQuery, keeps the domains in a news-domain list, and fills three
regional quotas (by top-level domain) in CrUX rank order. As used for the
paper (CrUX August 2026, top 1M, the GDELT 7-day and Wikidata union):

    python3 sample/sample.py --crux-month 202608 --crux-top 1000000 \\
        --news-list data/news-domains-combined.txt \\
        --strata us-en:1250,eu:625,row:625 --n 2500 \\
        --out data/sites-crux-news.txt

Then sample/filter_platforms.py removes the platform and government
domains (data/sites-crux-news-filtered.txt).

Needs the BigQuery client (`pip install google-cloud-bigquery db-dtypes`)
and Google Cloud credentials (`gcloud auth application-default login`);
the query is billed to your project (--gcp-project or the default one).
The frozen output and its provenance (data/sites-crux-news.txt,
data/sites-crux-news.provenance.json, with the rank of every site) are in
the repository, so nothing downstream needs BigQuery.

Reproducibility: the BigQuery table and month are pinned; the news list's
SHA-256 is recorded in the provenance JSON. CrUX ranks are coarse buckets
(1k, 10k, 100k, 1M); within a bucket, origins are taken in the order of
their origin strings.
"""
import argparse, hashlib, json, os, sys, time


def _norm(d):
    d = d.strip().lower().rstrip(".")
    if d.startswith("www."):
        d = d[4:]
    return d


def load_news_list(path):
    sha = hashlib.sha256()
    doms = set()
    with open(path, "rb") as f:
        data = f.read()
    sha.update(data)
    for line in data.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # tolerate CSV: first column
        if "," in line:
            line = line.split(",", 1)[0].strip()
        doms.add(_norm(line))
    return sha.hexdigest(), doms


def fetch_crux(month, top_n=10000, gcp_project=None):
    """Read top-N origins from CrUX monthly table via BigQuery.
    Returns [(rank, domain), ...] in ascending rank order.

    CrUX schema (chrome-ux-report.all.YYYYMM):
        origin STRING                         e.g. "https://www.nytimes.com"
        experimental.popularity.rank INT64    bucket: 1000, 10000, 100000, 1M

    We take `rank <= top_n`, then normalise the origin to a bare domain.
    `gcp_project` is the billing project for the query; falls back to
    GOOGLE_CLOUD_PROJECT env or ADC's attached project.
    """
    try:
        from google.cloud import bigquery
    except ImportError:
        raise SystemExit(
            "The CrUX query needs the BigQuery client:\n"
            "    pip install google-cloud-bigquery db-dtypes\n"
            "and GCP auth:\n"
            "    gcloud auth application-default login"
        )
    client = bigquery.Client(project=gcp_project) if gcp_project else bigquery.Client()
    table = f"chrome-ux-report.all.{month}"
    query = f"""
        SELECT origin, experimental.popularity.rank AS rank
        FROM `{table}`
        WHERE experimental.popularity.rank <= {int(top_n)}
        ORDER BY rank, origin
    """
    print(f"[crux] querying {table} for rank<={top_n} …", file=sys.stderr)
    rows = []
    for row in client.query(query).result():
        origin = row.origin or ""
        # strip protocol, strip :port, strip trailing /
        host = origin.split("://", 1)[-1].rstrip("/").split(":", 1)[0]
        host = _norm(host)
        if host:
            rows.append((row.rank, host))
    print(f"[crux] got {len(rows)} rows", file=sys.stderr)
    return rows


# Regions, by top-level domain (as analysis/common.py):
#   us-en: .com .net .org .us .gov .edu .io .tv .co
#   eu:    .de .fr .it .es .nl .se .no .dk .fi .pl .pt .be .at .ch
#          .ie .gr .cz .hu .ro .bg + .eu + .uk
#   row:   everything else
EU_TLDS = {'de','fr','it','es','nl','se','no','dk','fi','pl','pt','be','at','ch','ie','gr','cz','hu','ro','bg','eu','uk'}
US_LIKE_TLDS = {'com','net','org','us','gov','edu','io','tv','co'}


def region_of(domain):
    parts = domain.split('.')
    tld = parts[-1].lower()
    sld = parts[-2].lower() if len(parts) >= 2 else ''
    # cover co.uk, com.au style
    if len(parts) >= 3 and sld in {'co','com'}:
        tld = parts[-1].lower()
        if tld in EU_TLDS: return 'eu'
        if tld in {'us'}: return 'us-en'
        return 'row'
    if tld in EU_TLDS: return 'eu'
    if tld in US_LIKE_TLDS: return 'us-en'
    return 'row'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--crux-month", required=True, help="CrUX monthly table, YYYYMM (the paper: 202608).")
    ap.add_argument("--crux-top", type=int, default=1000000, help="CrUX rank-bucket cap (the paper: 1000000).")
    ap.add_argument("--gcp-project", help="GCP billing project for the BigQuery query. Falls back to "
                                          "GOOGLE_CLOUD_PROJECT or ADC's attached project.")
    ap.add_argument("--news-list", required=True, help="News domains, one per line (the paper: "
                                                       "data/news-domains-combined.txt).")
    ap.add_argument("--n", type=int, default=2500, help="Max number of sites to emit.")
    ap.add_argument("--out", default="data/sites-crux-news.txt")
    ap.add_argument("--strata", default="us-en:1250,eu:625,row:625",
                    help="Comma-separated 'region:count' quotas (regions: us-en, eu, row).")
    args = ap.parse_args()

    prov = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "frame": "crux",
        "nRequested": args.n,
        "cruxMonth": args.crux_month,
        "cruxTopUsed": args.crux_top,
    }

    # 1. Ranked frame: CrUX
    ranked_rows = fetch_crux(args.crux_month, top_n=args.crux_top, gcp_project=args.gcp_project)

    # 2. News classification
    sha, news_set = load_news_list(args.news_list)
    classification_src = {"kind": "local-file", "path": args.news_list,
                          "sha256": sha, "domainCount": len(news_set)}
    print(f"[news-list] {args.news_list}: {len(news_set)} domains, sha256={sha[:16]}…", file=sys.stderr)
    prov["classification"] = classification_src

    # 3. Deterministic stratified intersection by TLD-based region.
    quotas = {}
    for spec in args.strata.split(','):
        if ':' not in spec: continue
        k, v = spec.split(':', 1)
        quotas[k.strip()] = int(v)
    if not quotas:
        quotas = {'us-en': args.n}
    # Fill each stratum in rank order
    out_by_stratum = {k: [] for k in quotas}
    seen = set()
    for rank, dom in ranked_rows:
        if dom in seen: continue
        if dom not in news_set: continue
        r = region_of(dom)
        if r in out_by_stratum and len(out_by_stratum[r]) < quotas[r]:
            out_by_stratum[r].append((rank, dom))
            seen.add(dom)
        # also stop early if ALL quotas filled
        if all(len(out_by_stratum[k]) >= quotas[k] for k in quotas):
            break
    out = []
    for k in quotas:
        out.extend(out_by_stratum[k])
    out.sort(key=lambda rd: rd[0])  # keep rank order in the final file
    prov['strata_filled'] = {k: len(v) for k, v in out_by_stratum.items()}
    prov['strata_quotas'] = quotas

    if len(out) < max(10, args.n // 10):
        print(f"[warn] only {len(out)} sites after intersection "
              f"(news_set={len(news_set)}, rank_top={args.crux_top}). "
              f"Check the news list or widen --crux-top.", file=sys.stderr)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        f.write("# CrUX-ranked news/media sample\n")
        f.write(f"# generated: {prov['generatedAt']}\n")
        f.write(f"# crux: table=chrome-ux-report.all.{prov['cruxMonth']} top_used={prov['cruxTopUsed']}\n")
        f.write(f"# classification: local file {classification_src['path']} "
                f"sha256={classification_src['sha256']} news_domains={classification_src['domainCount']}\n")
        f.write(f"# n_requested={args.n} n_emitted={len(out)} strata_quotas={prov.get('strata_quotas')} strata_filled={prov.get('strata_filled')}\n")
        f.write("# format: one domain per line, ordered by crux rank (ascending)\n")
        for _, dom in out:
            f.write(dom + "\n")

    pj = args.out.rsplit(".", 1)[0] + ".provenance.json"
    with open(pj, "w") as f:
        prov["emittedCount"] = len(out)
        prov["sites"] = [{"rank": r, "domain": d} for r, d in out]
        json.dump(prov, f, indent=2)

    print(f"[done] wrote {args.out} ({len(out)} sites) and {pj}", file=sys.stderr)


if __name__ == "__main__":
    main()

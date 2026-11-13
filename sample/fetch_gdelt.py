#!/usr/bin/env python3
"""fetch_gdelt.py — build a reproducible news-domain list from the GDELT
Project's Global Knowledge Graph (GKG) 2.0 feed.

GDELT is free, needs no API key, and tags each record with the source
article URL, so a day of GKG files yields the domains that published news
that day.

Usage (the sample used seven full days, 30 Sep to 6 Oct 2026):
    python3 sample/fetch_gdelt.py --date 20261005 --n-files 96 \\
        --out data/news-domains-gdelt-20261005.txt

    # Default: the 4 most recent GKG files (about one hour of news)
    python3 sample/fetch_gdelt.py --out data/news-domains-gdelt.txt

Produces:
    <out>                   one domain per line, sorted
    <out>.provenance.json   list of GDELT file URLs fetched, SHA256 of
                            each, number of unique domains, generation ts
"""
import argparse, hashlib, io, json, os, re, sys, time, urllib.request, zipfile
from urllib.parse import urlparse

GDELT_MASTER_URL = "http://data.gdeltproject.org/gdeltv2/masterfilelist.txt"
GDELT_LAST_URL = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"
# GKG CSV column 5 (0-indexed) is DocumentIdentifier — the source URL.
GKG_URL_COL = 4  # safe for the "V2 GKG" format
UA = "news-domain-fetch/1.0 (research; mhassnain@ucdavis.edu)"


def fetch(url, timeout=120, retries=4):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"failed {url}: {last}")


def host_of(url):
    try:
        h = urlparse(url).hostname or ""
        h = h.lower().strip().rstrip(".")
        if h.startswith("www."):
            h = h[4:]
        # strip :port if any
        h = h.split(":", 1)[0]
        return h
    except Exception:
        return ""


def pick_files(date_str, n_files):
    """Return list of GKG file URLs for the given day. If date_str is None,
    returns the N MOST RECENT files (default behaviour)."""
    print("[gdelt] fetching master file list …", file=sys.stderr)
    txt = fetch(GDELT_MASTER_URL).decode("utf-8", errors="replace")
    all_gkg = [ln.split()[-1] for ln in txt.splitlines()
               if ln.strip().endswith(".gkg.csv.zip")]
    if date_str is None:
        # Master list is OLDEST-first; recent files are at the end.
        return all_gkg[-n_files:] if n_files else all_gkg
    needle = f"/{date_str}"
    gkg = [u for u in all_gkg if needle in u]
    gkg.sort()
    return gkg[:n_files] if n_files else gkg


def extract_domains_from_gkg(blob):
    """Parse a .gkg.csv.zip blob and yield unique DocumentIdentifier hostnames."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for name in z.namelist():
            with z.open(name) as fp:
                for raw in io.TextIOWrapper(fp, encoding="utf-8", errors="replace"):
                    cols = raw.split("\t")
                    if len(cols) <= GKG_URL_COL:
                        continue
                    url = cols[GKG_URL_COL].strip()
                    if url and (url.startswith("http://") or url.startswith("https://")):
                        h = host_of(url)
                        if h and "." in h:
                            yield h


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="YYYYMMDD; default = recent files from last-update feed.")
    ap.add_argument("--n-files", type=int, default=4,
                    help="Number of GDELT GKG files to download. Each ≈ 20–40 MB. "
                         "4 files ≈ 1 hour of news; 24 ≈ 6 hours; 96 ≈ 1 day.")
    ap.add_argument("--out", default="data/news-domains-gdelt.txt")
    ap.add_argument("--min-mentions", type=int, default=1,
                    help="Only emit domains seen in >=M distinct URLs across the fetched files. "
                         "Default 1 (all). Raise to filter one-off references.")
    args = ap.parse_args()

    files = pick_files(args.date, args.n_files)
    if not files:
        raise SystemExit("no GDELT files found for the given criteria")
    print(f"[gdelt] fetching {len(files)} files …", file=sys.stderr)

    domain_counts = {}
    file_shas = []
    total_bytes = 0
    for i, url in enumerate(files, 1):
        try:
            blob = fetch(url)
        except Exception as e:
            print(f"  [{i}/{len(files)}] FAIL {url}: {e}", file=sys.stderr)
            continue
        sha = hashlib.sha256(blob).hexdigest()
        file_shas.append({"url": url, "sha256": sha, "bytes": len(blob)})
        total_bytes += len(blob)
        n_new = 0
        for d in extract_domains_from_gkg(blob):
            if d in domain_counts:
                domain_counts[d] += 1
            else:
                domain_counts[d] = 1
                n_new += 1
        print(f"  [{i}/{len(files)}] {url.rsplit('/', 1)[-1]:40s} "
              f"+{n_new} new, total {len(domain_counts)}", file=sys.stderr)

    # Filter + sort
    domains = sorted(d for d, c in domain_counts.items() if c >= args.min_mentions)
    print(f"[gdelt] {len(domain_counts)} unique domains before filter, "
          f"{len(domains)} after min-mentions={args.min_mentions}", file=sys.stderr)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        f.write(f"# GDELT GKG 2.0 news domains\n")
        f.write(f"# generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")
        f.write(f"# source files: {len(file_shas)} GKG files from GDELT ({total_bytes/1024/1024:.1f} MiB)\n")
        f.write(f"# date filter: {args.date or 'recent via lastupdate.txt'}\n")
        f.write(f"# min-mentions filter: {args.min_mentions}\n")
        f.write(f"# unique domains emitted: {len(domains)}\n")
        f.write(f"# format: one domain per line, sorted alphabetically\n")
        for d in domains:
            f.write(d + "\n")

    pj = args.out + ".provenance.json"
    output_sha = hashlib.sha256(("\n".join(domains)).encode()).hexdigest()
    with open(pj, "w") as f:
        json.dump({
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": "GDELT Project GKG v2.0",
            "dateFilter": args.date,
            "nFilesRequested": args.n_files,
            "nFilesFetched": len(file_shas),
            "minMentionsFilter": args.min_mentions,
            "nUniqueDomains": len(domains),
            "outputSha256": output_sha,
            "sourceFiles": file_shas,
        }, f, indent=2)
    print(f"[done] wrote {args.out} ({len(domains)} domains, sha256={output_sha[:16]}…)", file=sys.stderr)
    print(f"[done] wrote {pj}", file=sys.stderr)


if __name__ == "__main__":
    main()

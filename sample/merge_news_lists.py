#!/usr/bin/env python3
"""merge_news_lists.py — union of several news-domain lists.

Produces one `<out>` of unique lower-cased domains (one per line, sorted),
plus `<out>.provenance.json` recording each source file's SHA and the
resulting SHA.

Usage (repo root), as used for the sample:
    python3 sample/merge_news_lists.py --out data/news-domains-gdelt-7day.txt \\
        data/news-domains-gdelt-2026093*.txt data/news-domains-gdelt-202610*.txt
    python3 sample/merge_news_lists.py --out data/news-domains-combined.txt \\
        data/news-domains-gdelt-7day.txt data/news-domains-wikidata.txt
"""
import argparse, hashlib, json, os, sys, time


def _norm(d):
    d = d.strip().lower().rstrip(".")
    if d.startswith("www."):
        d = d[4:]
    return d


def read_list(path):
    with open(path, "rb") as f:
        data = f.read()
    sha = hashlib.sha256(data).hexdigest()
    doms = set()
    for ln in data.decode("utf-8", errors="replace").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if "," in s:
            s = s.split(",", 1)[0]
        d = _norm(s)
        if d and "." in d:
            doms.add(d)
    return sha, doms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("inputs", nargs="+", help="paths to news-domain txt files")
    args = ap.parse_args()

    union = set()
    sources = []
    for p in args.inputs:
        sha, doms = read_list(p)
        pj = p + ".provenance.json"
        prov = json.load(open(pj)) if os.path.exists(pj) else {}
        sources.append({"path": p, "sha256": sha, "domains": len(doms),
                        "upstream": prov.get("source"), "date": prov.get("date"),
                        "gdeltFiles": prov.get("files", [])})
        union |= doms
    out_list = sorted(union)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        f.write(f"# merged news-domain list — {len(sources)} sources, {len(out_list)} unique\n")
        for s in sources:
            f.write(f"# src: {s['path']} sha256={s['sha256']} domains={s['domains']}\n")
        for d in out_list:
            f.write(d + "\n")

    out_sha = hashlib.sha256(("\n".join(out_list)).encode()).hexdigest()
    with open(args.out + ".provenance.json", "w") as f:
        json.dump({
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "mergeOf": sources,
            "mergedDomainCount": len(out_list),
            "outputSha256": out_sha,
        }, f, indent=2)
    print(f"[merged] {len(sources)} inputs → {len(out_list)} unique domains "
          f"→ {args.out} (sha256={out_sha[:16]}…)", file=sys.stderr)


if __name__ == "__main__":
    main()

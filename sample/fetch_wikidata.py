#!/usr/bin/env python3
"""fetch_wikidata.py — reproducible news-organization domain list from
Wikidata via SPARQL (the list the sample draws on; data/news-domains-wikidata.txt).

Why Wikidata: free, public, CC0, machine-readable; the only global
cross-lingual news-source classifier with strong EU and RoW coverage.
Our GDELT 1-week union captures ~8 K US-EN news domains but only ~630 EU
and ~890 RoW; Wikidata adds ~2 400 EU and ~2 800 RoW news organisations
that have an official website recorded.

We query entities of type (or subclass of):
  Q11032   newspaper
  Q1110794 daily newspaper
  Q1153191 online newspaper
  Q1156234 online magazine
  Q1954461 news website
  Q1931658 news agency
  Q871232  electronic magazine
each with property P856 (official website).

Reproducibility:
  * The exact SPARQL text is pinned in this file and echoed into the
    provenance JSON.
  * The Wikidata query service does NOT publish version IDs for its
    live index; we record the HTTP response date header, the raw-result
    SHA256, and the result count.
"""
import argparse, hashlib, json, os, sys, time, urllib.parse, urllib.request
from urllib.parse import urlparse

ENDPOINT = "https://query.wikidata.org/sparql"
UA = "news-domain-fetch/1.0 (research; mhassnain@ucdavis.edu)"

SPARQL = """
SELECT DISTINCT ?outlet ?website ?country WHERE {
  { ?outlet wdt:P31 wd:Q11032 } UNION
  { ?outlet wdt:P31 wd:Q1110794 } UNION
  { ?outlet wdt:P31 wd:Q1153191 } UNION
  { ?outlet wdt:P31 wd:Q1156234 } UNION
  { ?outlet wdt:P31 wd:Q1954461 } UNION
  { ?outlet wdt:P31 wd:Q1931658 } UNION
  { ?outlet wdt:P31 wd:Q871232 }
  ?outlet wdt:P856 ?website .
  OPTIONAL { ?outlet wdt:P17 ?country . }
}
""".strip()


def host_of(url):
    try:
        h = urlparse(url).hostname or ""
        h = h.lower().strip().rstrip(".")
        if h.startswith("www."):
            h = h[4:]
        h = h.split(":", 1)[0]
        return h
    except Exception:
        return ""


def run_sparql(query, timeout=120):
    data = urllib.parse.urlencode({"query": query}).encode()
    req = urllib.request.Request(ENDPOINT + "?" + data.decode(),
                                 headers={"Accept": "application/sparql-results+json",
                                          "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), dict(r.headers)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/news-domains-wikidata.txt")
    args = ap.parse_args()

    print(f"[wd] querying Wikidata SPARQL …", file=sys.stderr)
    t0 = time.time()
    raw, hdrs = run_sparql(SPARQL)
    print(f"[wd] got {len(raw)} bytes in {time.time()-t0:.1f}s "
          f"(response-date: {hdrs.get('Date')})", file=sys.stderr)

    sha = hashlib.sha256(raw).hexdigest()
    parsed = json.loads(raw)
    rows = parsed["results"]["bindings"]
    hosts = set()
    for r in rows:
        h = host_of(r.get("website", {}).get("value", ""))
        if h and "." in h:
            hosts.add(h)
    out = sorted(hosts)
    print(f"[wd] {len(rows)} bindings, {len(hosts)} unique hostnames", file=sys.stderr)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        f.write("# Wikidata news-organization domains (newspaper, news agency, ..., with P856 website)\n")
        f.write(f"# generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")
        f.write(f"# query-sha256: {hashlib.sha256(SPARQL.encode()).hexdigest()}\n")
        f.write(f"# response-sha256: {sha}\n")
        f.write(f"# response-date-header: {hdrs.get('Date')}\n")
        f.write(f"# bindings={len(rows)} unique_hostnames={len(hosts)}\n")
        for h in out:
            f.write(h + "\n")

    with open(args.out + ".provenance.json", "w") as f:
        json.dump({
            "source": "Wikidata SPARQL",
            "endpoint": ENDPOINT,
            "sparqlText": SPARQL,
            "sparqlSha256": hashlib.sha256(SPARQL.encode()).hexdigest(),
            "responseSha256": sha,
            "responseDateHeader": hdrs.get("Date"),
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "bindings": len(rows),
            "uniqueHostnames": len(hosts),
            "outputSha256": hashlib.sha256(("\n".join(out)).encode()).hexdigest(),
        }, f, indent=2)
    print(f"[done] wrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()

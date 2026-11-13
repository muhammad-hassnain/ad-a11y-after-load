#!/usr/bin/env python3
"""fetch_wikidata_v2.py — a stricter Wikidata news-publisher list, used by
rater A (annotators/rater_a.py) and for the platform blocklist
(sample/filter_platforms.py). It does not feed the sample itself.

fetch_wikidata.py unions seven news-adjacent entity classes including the
ambiguous "online magazine" (Q1156234), "news website" (Q1954461), and
"electronic magazine" (Q871232). These classes catch presence of an
"official website" (P856) on parent brand entities, which pulls in
platform domains (apps.apple.com, github.com, etc.) that happen to have a
news-adjacent Wikidata instance_of chain.

This list is stricter in three ways:

  1. KEEP only the three classes with unambiguous editorial-outlet
     semantics:
       Q11032    newspaper
       Q1110794  daily newspaper
       Q1931658  news agency
     (Weekly and online-only magazines are left out.)

  2. REQUIRE at least one positive editorial signal:
       P1113     publication frequency (daily/weekly/monthly/…)
       P2561     native name in a language
       P495      country of origin
       P127      owner, when owner itself is a media company
     An entity passing (1) still needs (2) to be emitted.

  3. HARD-BLOCKLIST platform parent domains that are not news publishers
     even if Wikidata classifies them as such (apps.apple.com,
     docs.google.com, github.com, archive.org, en.wikipedia.org,
     amazon.*, linkedin.com, x.com, t.me, tv.apple.com,
     developer.apple.com, medium.com, substack.com, …).

The result is a news-publisher list rather than a has-a-Wikidata-article
list.

Usage:
    python3 sample/fetch_wikidata_v2.py --out data/news-domains-wikidata-v2.txt
"""
import argparse, hashlib, json, os, sys, time, urllib.parse, urllib.request
from urllib.parse import urlparse

ENDPOINT = "https://query.wikidata.org/sparql"
UA = "news-domain-fetch/2.0 (research; mhassnain@ucdavis.edu)"

SPARQL = """
SELECT DISTINCT ?outlet ?website ?country ?freq WHERE {
  { ?outlet wdt:P31 wd:Q11032 }        # newspaper
  UNION
  { ?outlet wdt:P31 wd:Q1110794 }      # daily newspaper
  UNION
  { ?outlet wdt:P31 wd:Q1931658 }      # news agency
  ?outlet wdt:P856 ?website .
  OPTIONAL { ?outlet wdt:P17 ?country . }
  OPTIONAL { ?outlet wdt:P1113 ?freq . }
  # Must have at least one editorial-signal predicate.
  FILTER EXISTS {
    { ?outlet wdt:P1113 ?_f }    # publication frequency
    UNION
    { ?outlet wdt:P495  ?_c }    # country of origin
    UNION
    { ?outlet wdt:P127  ?_o }    # owner
    UNION
    { ?outlet wdt:P2561 ?_n }    # native name
    UNION
    { ?outlet wdt:P2769 ?_b }    # budget (financially sized = real org)
  }
}
""".strip()

# Hard blocklist — platform / parent-brand / sports-league / vendor-list
# domains that Wikidata's news-adjacent classes sometimes pull in.
# Anchored to registrable domain; subdomains covered by eTLD+1 fold.
PLATFORM_BLOCKLIST = {
    "apple.com", "google.com", "github.com", "archive.org",
    "wikipedia.org", "wikidata.org", "mediawiki.org",
    "spotify.com", "amazon.com", "amazon.co.uk", "amazon.de", "amazon.fr",
    "amazon.it", "amazon.es", "amazon.jp", "amazon.in", "amazon.ca",
    "linkedin.com", "x.com", "twitter.com", "t.me", "telegram.org",
    "facebook.com", "instagram.com", "youtube.com", "tiktok.com",
    "reddit.com", "pinterest.com", "medium.com", "substack.com",
    "wordpress.com", "blogspot.com", "tumblr.com", "livejournal.com",
    "springer.com", "nature.com", "sciencedirect.com", "jstor.org",
    "mlb.com", "nfl.com", "nba.com", "fifa.com", "uefa.com",
    "gov.uk", "cdc.gov", "fda.gov", "nih.gov", "loc.gov", "nasa.gov",
    "whitehouse.gov", "state.gov", "irs.gov", "usps.com",
    "ebay.com", "etsy.com", "shopify.com", "alibaba.com", "aliexpress.com",
    "booking.com", "airbnb.com", "tripadvisor.com", "yelp.com",
    "zillow.com", "indeed.com", "glassdoor.com", "stackoverflow.com",
    "netflix.com", "hulu.com", "disneyplus.com", "primevideo.com",
    "twitch.tv", "roblox.com", "discord.com",
    # Vendor / enterprise-SaaS parents that occasionally slip through
    "salesforce.com", "oracle.com", "microsoft.com", "ibm.com",
    "cloudflare.com", "akamai.com", "fastly.com",
}


def host_of(url):
    try:
        h = urlparse(url).hostname or ""
        h = h.lower().strip().rstrip(".")
        if h.startswith("www."):
            h = h[4:]
        return h.split(":", 1)[0]
    except Exception:
        return ""


def etld_plus_1(host):
    """Rough eTLD+1 fold. Keeps co.uk, com.au style TLDs intact."""
    parts = host.split(".")
    if len(parts) < 2:
        return host
    multi = {"co.uk", "co.jp", "com.au", "com.br", "com.mx", "co.in",
             "co.za", "co.nz", "ne.jp", "or.jp"}
    tail2 = ".".join(parts[-2:])
    if tail2 in multi and len(parts) >= 3:
        return ".".join(parts[-3:])
    return tail2


def is_blocked(host):
    e1 = etld_plus_1(host)
    return e1 in PLATFORM_BLOCKLIST


def run_sparql(query, timeout=120):
    data = urllib.parse.urlencode({"query": query}).encode()
    req = urllib.request.Request(ENDPOINT + "?" + data.decode(),
                                 headers={"Accept": "application/sparql-results+json",
                                          "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), dict(r.headers)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/news-domains-wikidata-v2.txt")
    args = ap.parse_args()

    print("[wd-v2] querying Wikidata SPARQL …", file=sys.stderr)
    t0 = time.time()
    raw, hdrs = run_sparql(SPARQL)
    print(f"[wd-v2] {len(raw)} bytes in {time.time()-t0:.1f}s", file=sys.stderr)

    sha_q = hashlib.sha256(SPARQL.encode()).hexdigest()
    sha_r = hashlib.sha256(raw).hexdigest()
    parsed = json.loads(raw)
    rows = parsed["results"]["bindings"]

    # Collect hosts, drop platform-blocklist eTLD+1s
    hosts = {}
    blocked_count = 0
    for r in rows:
        h = host_of(r.get("website", {}).get("value", ""))
        if not h or "." not in h:
            continue
        if is_blocked(h):
            blocked_count += 1
            continue
        hosts.setdefault(h, []).append(r.get("outlet", {}).get("value", ""))

    out = sorted(hosts)
    print(f"[wd-v2] {len(rows)} bindings → {len(hosts) + blocked_count} unique hostnames → "
          f"{len(hosts)} after blocklist (dropped {blocked_count})", file=sys.stderr)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        f.write("# Wikidata news-publisher domains (v2 — tightened)\n")
        f.write(f"# classes: Q11032 newspaper, Q1110794 daily newspaper, Q1931658 news agency\n")
        f.write(f"# positive filter: at least one of P1113 / P495 / P127 / P2561 / P2769\n")
        f.write(f"# platform blocklist: {len(PLATFORM_BLOCKLIST)} entries\n")
        f.write(f"# generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")
        f.write(f"# query-sha256: {sha_q}\n")
        f.write(f"# response-sha256: {sha_r}\n")
        f.write(f"# bindings={len(rows)} unique_hostnames={len(hosts)} blocklist_drops={blocked_count}\n")
        for h in out:
            f.write(h + "\n")

    with open(args.out + ".provenance.json", "w") as f:
        json.dump({
            "source": "Wikidata SPARQL (v2 tightened)",
            "sparqlText": SPARQL,
            "sparqlSha256": sha_q,
            "responseSha256": sha_r,
            "platformBlocklist": sorted(PLATFORM_BLOCKLIST),
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "bindings": len(rows),
            "uniqueHostnamesBeforeBlocklist": len(hosts) + blocked_count,
            "uniqueHostnamesAfterBlocklist": len(hosts),
            "blocklistDrops": blocked_count,
            "outputSha256": hashlib.sha256(("\n".join(out)).encode()).hexdigest(),
        }, f, indent=2)
    print(f"[done] wrote {args.out} ({len(hosts)} domains)", file=sys.stderr)


if __name__ == "__main__":
    main()

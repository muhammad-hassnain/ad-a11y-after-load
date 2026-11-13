#!/usr/bin/env python3
"""Rater A: news-publisher labels from domain rules.

Labels each analysed site (data/sites-crux-news-filtered.txt) from its
domain name and the two news corpora the sample was drawn from, without
looking at the site itself.

Classes:
  news_publisher     primary business is editorial news
  not_news_publisher platform, government, e-commerce, aggregator, SaaS, ...
  ambiguous          lifestyle magazine, tech review, mixed use, blog, or
                     too little evidence

Rules (applied in order; first match wins):

1. eTLD+1 in the platform blocklist -> not_news_publisher
2. TLD .gov / .edu, or ".gov" in the host -> not_news_publisher
3. Host starts with a platform subdomain (apps., docs., tv., developer.,
   support., sites., books., ...) -> not_news_publisher
4. Host in the GDELT 7-day list AND in Wikidata (newspaper / daily /
   news agency with an editorial-signal property, sample/fetch_wikidata_v2.py)
   -> news_publisher
5. Host in that Wikidata list only -> news_publisher
6. Host matches a strong news-publisher lexical pattern (...times, ...post,
   ...tribune, ...herald, ...daily, ...gazette, ...journal, ...news, ...)
   -> news_publisher
8. Host matches a lifestyle / sports / tech / entertainment pattern
   (cosmopolitan, elle, vogue, cnet, techcrunch, variety, ...) -> ambiguous
7. Host in the GDELT 7-day list only -> ambiguous (GDELT records any URL
   seen in a news article, not only publishers)
9. Default -> ambiguous

(Rules 7 and 8 are numbered as in the labels file; rule 8 is tested first.)

Usage (repo root):
    python3 annotators/rater_a.py            # writes results/annotators/rater-a.json
The frozen labels are data/labels/rater-a.json; scripts/replicate.sh checks
that this script reproduces them.
"""
import argparse, json, os, re

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
    "salesforce.com", "oracle.com", "microsoft.com", "ibm.com",
    "cloudflare.com", "akamai.com", "fastly.com",
}

NEWS_LEXICAL_RX = re.compile(
    r'(^|[-.])(news|times|post|tribune|herald|daily|gazette|journal|chronicle|'
    r'observer|messenger|sentinel|express|press|guardian|mirror|telegraph|'
    r'echo|standard|star|sun|examiner|courier|bulletin|dispatch|register|'
    r'inquirer|digest|advocate|record|world|monitor|wire|report|'
    r'nachrichten|zeitung|zeit|courrier|tribune|mercury|clarin|folha|'
    r'informe|noticia|kronik|avisen|aftenposten|dagbladet)([-.]|$)',
    re.IGNORECASE
)

AMBIG_LEXICAL_RX = re.compile(
    r'(cosmopolitan|elle|vogue|gq|womansworld|boldsky|lifestyle|'
    r'cnet|arstechnica|techcrunch|theverge|wired|ars|engadget|slashdot|'
    r'pitchfork|variety|hollywoodreporter|rolling|wrestlinginc|billboard|'
    r'baltimoreravens|baltimoreorioles|nhl|nascar|f1|formula1|'
    r'realestate|bankrate|zillow|redfin|realtor)',
    re.IGNORECASE
)

NOT_PATTERN_RX = re.compile(
    r'^(apps|docs|tv|developer|support|sites|books|podcasts|science|space|'
    r'developers|cloud|console|admin|portal|help)\.',
    re.IGNORECASE
)


def etld_plus_1(host):
    parts = host.split(".")
    if len(parts) < 2: return host
    multi = {"co.uk","co.jp","com.au","com.br","com.mx","co.in","co.za","co.nz","ne.jp","or.jp"}
    tail2 = ".".join(parts[-2:])
    if tail2 in multi and len(parts) >= 3:
        return ".".join(parts[-3:])
    return tail2


def load_set(p):
    out = set()
    if not os.path.exists(p): return out
    for ln in open(p):
        s = ln.strip()
        if not s or s.startswith("#"): continue
        if "," in s: s = s.split(",", 1)[0].strip()
        if s and "." in s: out.add(s.lower())
    return out


def classify(host, gdelt, wiki_v2):
    e1 = etld_plus_1(host)
    if e1 in PLATFORM_BLOCKLIST:
        return "not_news_publisher", "rule 1: eTLD+1 in platform blocklist"
    tld = host.rsplit(".", 1)[-1].lower()
    if tld in ("gov", "edu") or ".gov" in host:
        return "not_news_publisher", "rule 2: gov/edu TLD"
    if NOT_PATTERN_RX.match(host):
        return "not_news_publisher", "rule 3: known platform subdomain pattern"
    in_g = host in gdelt
    in_w = host in wiki_v2
    if in_g and in_w:
        return "news_publisher", "rule 4: GDELT ∩ Wikidata-v2 (both agree, highest confidence)"
    if in_w:
        return "news_publisher", "rule 5: Wikidata-v2 (newspaper/daily/agency with editorial signal)"
    if NEWS_LEXICAL_RX.search(host):
        return "news_publisher", "rule 6: strong news-publisher lexical pattern"
    if AMBIG_LEXICAL_RX.search(host):
        return "ambiguous", "rule 8: known lifestyle/sports/tech/entertainment pattern"
    if in_g:
        return "ambiguous", "rule 7: GDELT only (uncertain — GDELT catalogs any URL seen in a news article)"
    return "ambiguous", "rule 9: default (uncertain)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default="data/sites-crux-news-filtered.txt")
    ap.add_argument("--gdelt", default="data/news-domains-gdelt-7day.txt")
    ap.add_argument("--wiki-v2", default="data/news-domains-wikidata-v2.txt")
    ap.add_argument("--out", default="results/annotators/rater-a.json")
    args = ap.parse_args()

    gdelt = load_set(args.gdelt)
    wiki_v2 = load_set(args.wiki_v2)
    sites = [l.strip() for l in open(args.sites)
             if l.strip() and not l.startswith("#")]

    labels = {}
    by_class = {"news_publisher": 0, "not_news_publisher": 0, "ambiguous": 0}
    for s in sites:
        label, rule = classify(s, gdelt, wiki_v2)
        labels[s] = {"label": label, "rule": rule}
        by_class[label] += 1

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(labels, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")

    print(f"Rater A labelled {len(sites)} sites:")
    for k, v in by_class.items():
        print(f"  {k:22s}  {v:5d}  ({100*v/len(sites):.1f}%)")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

# What Load-Time Accessibility Checks Miss on Ad-Supported News Sites — artifact

This repository is the artifact of the paper *What Load-Time Accessibility
Checks Miss on Ad-Supported News Sites*. It contains

- the **harness**: a Playwright + axe-core crawler that audits each page
  before and after a scripted exercise (scrolling, waiting, tabbing,
  hovering), with ad vendors loaded (normal arm) or blocked (blocked arm),
  and a second crawler that also audits inside cross-origin frames;
- the **crawl drivers** of both crawls, with the settings of the original
  runs, and the **block list** of 59 vendors;
- the **sample construction**: the news-domain lists (GDELT, Wikidata), the
  CrUX-based sample of 2,500 sites and the 2,465 sites analysed, with
  provenance;
- the **news-publisher labels** of the three automatic annotators and the
  author's 50 hand labels, with the agreement script;
- the **analysis, figure and check scripts**, the frozen result summaries
  and the paper's figures.

The crawl records themselves (about 7 GB of JSON, 155 MB compressed) are
release assets, not part of the repository (see [Crawl data](#crawl-data)).

Licensed under the MIT License (code) and CC BY 4.0 (data); see [License](#license).

## Layout

```
harness/      the crawlers
  crawl-main.js       main crawl (normal and blocked arm, 3 rounds)
  crawl-inframe.js    in-frame crawl (crawl-main.js + an audit inside each cross-origin frame)
  instrument.js       page instrumentation injected before site scripts (overlays, mutations, ...)
  attribution.js      maps request hosts to block-list vendors
scripts/
  crawl_main.sh       driver of the main crawl
  crawl_inframe.sh    driver of the in-frame crawl
  replicate.sh        regenerates every number and figure from the crawl data
  check_numbers.py    checks every number the paper reports
  make_figures.py     the paper's figures
analysis/
  common.py           paths, selector-matching patterns, rule families, regions
  delta.py            RQ1, RQ2 (Δ, chance and order checks, placebo, Table I), crawl facts
  extra_numbers.py    RQ3 (in-frame crawl), frames in the main audit, hand labels, versions
  figure_numbers.py   numbers behind three figures (rules, size of Δ, frame hosts)
annotators/
  rater_a.py          rater A: domain rules
  rater_c.py          rater C: crawl-feature rules
  agreement.py        Cohen's and Fleiss' κ between raters A, B and C
  hand_label_sites.py redraws the 50 hand-labelled sites
sample/
  fetch_gdelt.py      news domains from a day of GDELT GKG files
  fetch_wikidata.py   news-organisation domains from Wikidata (used for the sample)
  fetch_wikidata_v2.py stricter Wikidata list and platform blocklist (rater A, filter)
  merge_news_lists.py union of news-domain lists
  sample.py           CrUX sample with regional quotas (BigQuery)
  filter_platforms.py drops the 35 platform and government domains
data/
  ad-vendors.json     the block list: 59 vendors, 121 host or path patterns
  sites-crux-news.txt (+ .provenance.json)  the 2,500 sampled sites, with CrUX ranks
  sites-crux-news-filtered.txt              the 2,465 analysed sites
  news-domains-*.txt (+ .provenance.json)   GDELT and Wikidata news-domain lists
  labels/             rater-a.json, rater-b.json, rater-b-sites.txt, hand-labels-50.json
results/              frozen outputs of scripts/replicate.sh (see the table below)
  figures/            the paper's figures, PDF and PNG
  annotators/         rater A and C labels, agreement.{json,txt}
crawl/                crawl data, extracted from the release assets (not in git)
```

## Requirements

- **Node.js 20** and the pinned npm packages (Playwright 1.63.0, axe-core
  4.13.0, @axe-core/playwright 4.13.0; `package-lock.json`):

  ```bash
  npm ci
  npx playwright install chromium     # only to re-run the crawls (Chromium 153)
  ```

  Reproducing the numbers needs only `npm ci`: the in-frame analysis reads
  axe-core's rule metadata and Playwright's Chromium version from
  `node_modules`.
- **Python 3.10+** with numpy and matplotlib:

  ```bash
  python3 -m venv .venv && . .venv/bin/activate
  pip install -r requirements.txt
  ```

  The BigQuery client in `requirements.txt` is needed only by
  `sample/sample.py`. All other scripts use the standard library plus numpy
  (analysis) and matplotlib (figures).
- About 7.5 GB of disk for the extracted crawl data. The figures use the
  Liberation Sans font if installed (DejaVu Sans otherwise).

The paper's numbers were produced on Ubuntu with Node 20.20.2 and
Python 3.14.

## Crawl data

The crawl records are three release assets that extract into `crawl/`:

| Asset | Contents | Records | Size |
|---|---|---|---|
| `crawl-main-normal.tar.zst` | `crawl/main/arm-normal/per-site/`: main crawl, normal arm, rounds 1–3 | 7,497 | 82 MB (3.8 GB extracted) |
| `crawl-main-blocked.tar.zst` | `crawl/main/arm-blocked/per-site/`: main crawl, blocked arm, rounds 1–3 | 7,484 | 31 MB (1.7 GB) |
| `crawl-inframe-normal.tar.zst` | `crawl/inframe/arm-normal/per-site/`: in-frame crawl | 2,495 | 42 MB (1.6 GB) |
| `SHA256SUMS` | SHA-256 of the three archives | | |

```bash
sha256sum -c SHA256SUMS
for f in crawl-main-normal crawl-main-blocked crawl-inframe-normal; do
  tar --zstd -xf $f.tar.zst          # at the repository root
done
```

(Without GNU tar's `--zstd`: `zstd -dc $f.tar.zst | tar -xf -`.)

Each record is one visit: `<host>__v<round>.json`. The main crawl includes
all three rounds of both arms, including round 2, which the paper does not
analyse (a DNS outage on the crawl machine disrupted the normal-arm round 2;
`ANALYSIS_ROUNDS=1,2,3` includes it). The fields the analysis uses:
`status`, `finalUrl`, `startedAt`, `gating` (bot and consent-wall probe),
`baseline.axe` and `post.axe` (axe-core violations, nodes with `target`
and `html`), `post.inventory` (ad-like elements), `interactions`
(scrolls, Tab presses, hovers), `vendors` (requests per block-list
vendor), `trackerRequestsFromInteraction`, `requestsTruncated`, and, in the
in-frame crawl, `post.oopifAxe` (one entry per audited cross-origin frame:
URL, origin, viewport and body size, `visible`, violations).

## Reproducing the numbers and figures

```bash
bash scripts/replicate.sh
```

It takes about four minutes (one CPU core, a fast SSD) and

1. reruns rater A (and checks it against `data/labels/rater-a.json`) and
   rater C, computes their agreement with rater B, and redraws the 50
   hand-labelled sites;
2. runs `analysis/delta.py`, `analysis/extra_numbers.py` and
   `analysis/figure_numbers.py`, which write `results/*.json`, `*.txt` and
   `*.csv`;
3. runs `scripts/check_numbers.py`, which compares every number the paper
   reports (text, figures, Table I) with the regenerated results and fails
   on any mismatch; the run ends with `all numbers in the paper reproduce`;
4. runs `scripts/make_figures.py`, which writes the nine figures to
   `results/figures/`.

It also prints the crawler file and the Playwright, axe-core and Chromium
versions behind each crawl. Options (environment variables): `PY` (Python
interpreter), `CRAWL_DIR` (crawl data root, default `crawl/`),
`RESULTS_DIR` (output directory, default `results/`), `NODE_MODULES`
(default `node_modules/`), `ANALYSIS_ROUNDS` (default `1,3`),
`KEEP_PLATFORM_DOMAINS=1` (keep the 35 platform and government domains).

### Where each result comes from

| Paper | Output | Keys |
|---|---|---|
| Fig. 1 (study overview) | `results/figures/fig8-pipeline-overview.pdf` | from `delta.json`, `extra-numbers.json`, `data/ad-vendors.json` |
| Study Design: sample, quotas, platform domains | `results/delta.json`, `results/extra-numbers.json` | `main.sample`, `facts.stratum_popularity` |
| Study Design: annotator agreement (κ ≤ 0.27) | `results/annotators/agreement.json` | `three_category`, `full_AC` |
| Study Design: 50 hand labels (41 news, 82.0%) | `results/extra-numbers.json` | `news_precision` |
| Study Design: versions, pairs, timing, frame cap, tracker requests | `results/delta.json`, `results/extra-numbers.json` | `main.pair_timing`, `main.tracker_cost`, `inframe.frame_cap_reached`, `facts.chromium` |
| Results intro, Fig. 2 (sample flow) | `results/figures/fig15-sample-flow.pdf` | `main.records`, `main.included_visits`, `main.exclusions_normal` |
| RQ1 text, Fig. 3 | `results/figures/fig14-rq1-whether-what.pdf`, `results/rq1-per-site.csv` | `main.rq1_*`, `main.baseline_facts`, `main.pass_then_fail`, `main.new_rule_frames`, `sensitivity.churn_filtered` |
| RQ1, Fig. 4 (rules that newly fail) | `results/figures/fig16-rq1-new-rules.pdf` | `figure-numbers.json`: `rq1_rules` |
| RQ2: Δ, variants | `results/delta.json`, `results/delta-per-site.csv` | `main.shares`, `main.pos_ci`, `main.hl`, `main.wcag_only`, `main.net_count`, `main.scroll_check`, `sensitivity.*` |
| RQ2: chance and visit order | `results/delta.json` | `main.noise` (`aa` = two identical normal visits, `ab` = an ads/blocked pair), `main.order_check` |
| RQ2, Fig. 5 (size of Δ) | `results/figures/fig17-rq2-delta-size.pdf` | `figure-numbers.json`: `delta_tails` |
| RQ2, Fig. 6 (placebo and vendor dose) | `results/figures/fig11-vendor-dose.pdf` | `main.placebo_dose`, `sensitivity.without_non_ad_ui_vendors` |
| RQ2, Table I | `results/delta.txt` | `main.selector_table_paired` (Ad, +Sp.), `main.table1_counterfactual.forward` / `.reverse` (N first, B first) |
| RQ2, Fig. 7 (structure split) | `results/figures/fig12-structure-split.pdf` | `main.table1_concentration`, `main.structure_split` |
| RQ3 text | `results/extra-numbers.json` | `inframe.frame_rules`, `inframe.any_visible_by_viewport`, `main_crawl` |
| RQ3, Fig. 8 (rules inside frames) | `results/figures/fig7-oopif-vs-mainframe.pdf` | `inframe.frame_rules.per_rule` |
| RQ3, Fig. 9 (frame hosts) | `results/figures/fig18-rq3-frame-orgs.pdf` | `figure-numbers.json`: `frame_orgs` |
| Discussion | `results/extra-numbers.json` | `inframe.frame_rules.ad_vendor` |
| Threats to Validity | `results/delta.json`, `results/extra-numbers.json` | `main.google_ad_frame_in_inventory`, `main.hovers_per_visit`, `main.time_of_day`, `main.aa_timing`, `main.structure_split`, `main.table1_counterfactual.identical_visits`, `main.by_region`, `news_precision` |

In the JSON, `layout` is the paper's *structure* rule family, `ad` is the
ad-token pattern and `ad_or_sponsored` adds the sponsored-content pattern,
and `oopif` (in the records and the figure name) refers to the in-frame
audit. `scripts/check_numbers.py` names, for every number, the paper
section it comes from.

## Re-running the crawls

The crawls load live pages and ads, so a new crawl gives different
records; the frozen records above are the ones the paper analyses.

```bash
bash scripts/crawl_main.sh       # 3 rounds x 2 arms, about a day per arm and round
bash scripts/crawl_inframe.sh    # 1 normal-arm visit per site, several days
```

`crawl_main.sh` alternates the arms (normal 1, blocked 1, normal 2, ...),
with concurrency 6 and 150 s per site, and retries sites that timed out
with 300 s and concurrency 2. Options: `SITES`, `OUT`, `CONC`, `RECYCLE`,
`RETRY`, `SWEEP_CONC`, `SWEEP_TIMEOUT`, `ROUNDS`. `crawl_inframe.sh` uses
concurrency 3 and 180 s per site and retries sites without a record or
whose navigation failed. Options: `SITES`, `OUT`, `CONC`, `TIMEOUT`. The
crawlers themselves take `--sites --out --arm normal|blocked --visit N
--concurrency --timeout --resume true` (see the top of each file).

Which code ran: the main crawl (8–11 October 2026) ran
`harness/crawl-main.js`; from 9 October it also saved a viewport
screenshot after the post audit (not released; the records are
unaffected). The in-frame crawl (12–20 October 2026) ran
`harness/crawl-inframe.js`. Both used the `instrument.js`,
`attribution.js`, `data/ad-vendors.json` and npm packages in this
repository, from a US vantage point. In the blocked arm, the crawler drops
every subresource request that matches the block list
(`Network.setBlockedURLs`); there is no cosmetic filtering.

## Rebuilding the sample

The news-domain lists and the sample are frozen in `data/` with their
provenance (source files, SHA-256 of inputs and outputs). The chain that
built them:

```bash
for d in 20260930 20261001 20261002 20261003 20261004 20261005 20261006; do
  python3 sample/fetch_gdelt.py --date $d --n-files 96 --out data/news-domains-gdelt-$d.txt
done
python3 sample/merge_news_lists.py --out data/news-domains-gdelt-7day.txt \
    data/news-domains-gdelt-2026093*.txt data/news-domains-gdelt-202610*.txt
python3 sample/fetch_wikidata.py --out data/news-domains-wikidata.txt
python3 sample/merge_news_lists.py --out data/news-domains-combined.txt \
    data/news-domains-gdelt-7day.txt data/news-domains-wikidata.txt
python3 sample/sample.py --crux-month 202608 --crux-top 1000000 \
    --news-list data/news-domains-combined.txt \
    --strata us-en:1250,eu:625,row:625 --n 2500 --out data/sites-crux-news.txt
python3 sample/filter_platforms.py      # -> data/sites-crux-news-filtered.txt (2,465 sites)
python3 sample/fetch_wikidata_v2.py --out data/news-domains-wikidata-v2.txt   # used by rater A
```

`sample.py` queries the public CrUX dataset on BigQuery (needs Google
Cloud credentials; the query is billed to your project). Wikidata and,
over time, GDELT change, so re-fetching gives different lists; the merge
and filter steps reproduce the frozen files exactly from the frozen inputs.

## Annotators and hand labels

Not every site in a news corpus is a news publisher. Three automatic
annotators labelled sites as `news_publisher`, `not_news_publisher` or
`ambiguous`:

- **Rater A**, domain rules (`annotators/rater_a.py`;
  `data/labels/rater-a.json`, all 2,465 sites): the domain name, a platform
  blocklist, membership in the GDELT and Wikidata lists, and lexical
  patterns. Each label names the rule that fired.
- **Rater B**, an LLM (`data/labels/rater-b.json`): 395 of a 400-site
  sample stratified by region (`data/labels/rater-b-sites.txt`). Only its
  labels are included, not the prompt.
- **Rater C**, crawl-feature rules (`annotators/rater_c.py`;
  `results/annotators/rater-c-labels.csv`): publisher trackers, consent
  banner and article shape, vendor count and visible text, from the site's
  first main-crawl visit.

`annotators/agreement.py` computes their agreement
(`results/annotators/agreement.{json,txt}`): pairwise Cohen's κ between
0.019 and 0.265 on the 395 sites all three labelled, and 0.008 between
raters A and C on all 2,465 sites. Because they disagree, the author
labelled 50 sites by hand (`data/labels/hand-labels-50.json`): a random
sample (seed 20261011) of rater B's 395 sites, which
`annotators/hand_label_sites.py` redraws. Of these, 41 (82.0%, Wilson 95%
CI 69.2–90.2) are news publishers.

## License

- **Code** (`harness/`, `scripts/`, `analysis/`, `annotators/`, `sample/`):
  MIT License, see [LICENSE](LICENSE).
- **Data** (`data/`, `results/` and the crawl records in the release
  assets): Creative Commons Attribution 4.0 International (CC BY 4.0), see
  [LICENSE-DATA](LICENSE-DATA). Page content captured in the crawl records
  (HTML snippets, request URLs) belongs to the respective site owners and is
  not covered by this license.

If you use this artifact, please cite the paper *What Load-Time
Accessibility Checks Miss on Ad-Supported News Sites*.

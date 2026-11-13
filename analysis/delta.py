#!/usr/bin/env python3
"""RQ1 and RQ2 numbers, and the facts about the main crawl, from the main
crawl's per-visit records. Every comparison in the paper that involves Δ
uses the same per-visit quantity:

  new(visit)  = post-exercise violating nodes whose (rule, canonical
                selector) is absent from that visit's baseline audit.
                Counts are NODES: two nodes that share a canonical selector
                both count.
  included    = the inclusion rule (paper, Measures): no bot wall or hard
                consent wall detected at load, HTTP status < 400, baseline
                and post audit present, >= 10 Tab presses; applied to BOTH
                arms. A robustness check also drops visits whose axe run
                returned an error (recorded by the crawler as 0 violations).
  Δ(site)     = median over rounds k with both visits included of
                new(normal, k) - new(blocked, k)

From the same per-visit counts it derives
  - RQ1: any-visit and majority-of-visits shares of sites with a new
    violation and with a newly failing rule (a rule that fails after the
    exercise and failed nowhere on the page at load), WCAG-only, frames
    only, what already fails at load, selector-matching tags
  - RQ2: Δ>0 / Δ<0 / Δ=0 shares, Hodges-Lehmann and mean with
    site-bootstrap CIs; the chance check (two identical normal visits vs.
    one ads/blocked pair on the same sites); the visit-order check (blocked
    visit first); the placebo and vendor-dose check; Table I (selector
    matching and the counterfactual share per rule and family, both visit
    orders, two identical visits), its concentration on few sites and the
    structure-rule split
  - variants: WCAG rules only, without nodes that look re-rendered, pairs
    that scrolled the same distance, the change in all violating nodes
    (net count), without the six non-ad services on the block list,
    without walls the gate missed, without failed audits
  - facts: record counts, exclusion reasons, DNS failures, inclusion and
    shares by region, time between paired visits, time of day, hovers per
    arm, Google ad frames that leak into the blocked arm, request-log
    truncation, tracker requests during the Tab and hover steps

Writes results/delta.json, delta.txt, delta-per-site.csv, rq1-per-site.csv.
Needs numpy. Usage (repo root): python3 analysis/delta.py
"""
import csv, json, math, re, statistics as st, sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (MAIN_ARMS as ARMS, OUT, ROUNDS, DROP_PLATFORM, NAMING_RULES,  # noqa: E402
                    LAYOUT_RULES, EU_TLDS, classify_match, platform_hosts, read_sites, region_of)

EXCLUDE = {"bot_challenge", "consent_wall_hard"}
TABLE_RULES = ["role-img-alt", "frame-title", "aria-command-name", "image-alt",
               "link-name", "button-name", "region", "color-contrast"]
B, SEED = 1000, 20261011
GADS = re.compile(r'google_ads_iframe|aswift|google_ads_frame', re.I)
WCAG_TAG = re.compile(r'^wcag\d')
RULE_TAGS = defaultdict(set)                    # rule id -> axe tags seen
# Block-list entries (data/ad-vendors.json) that are not ad delivery but
# render page UI of their own: paywall, video player, social SDK, site
# search, social embeds, app banners.
NON_AD_UI = {"Piano", "JW Player", "Meta Pixel / Audience Network",
             "Google Custom Search / GSA", "TikTok Pixel / Pangle", "Branch"}
# The Europe stratum (common.EU_TLDS) includes four TLDs that are not EU states.
NON_EU_IN_EUROPE = {"uk", "ch", "no", "eu"}
# Walls the gating probe missed: consent gates, bot checks and sign-in pages
# reached by redirect (final URL path) or named in the page title.
WALL_URL = re.compile(r'myprivacy\.|/consent(?:/|$)|accounts\.google\.|//signin\.|/signin|/sign-in'
                      r'|/login|captcha|/challenge', re.I)
WALL_TITLE = re.compile(r'privacy gate|verifying device|verification failed|captcha|just a moment'
                        r'|access denied|attention required|^sign in|sign in -|sign in \||log in', re.I)


# Same canonicalisation as the harness (diff() in harness/crawl-*.js): hash-like
# tokens (four or more hex digits) at the end of ids and class names are
# normalised, :nth-child / :nth-of-type indices dropped.
_ID_RX = re.compile(r'(#[A-Za-z_-][\w-]*)')
_CLS_RX = re.compile(r'(\.[A-Za-z_-][\w-]*)')
_HASH_SUFFIX = re.compile(r'[_-]?[0-9a-f]{4,}$', re.IGNORECASE)
_NTH_RX = re.compile(r':nth-(?:of-type|child)\(\d+\)')


def canon_sel(sel):
    if not sel: return ''
    s = str(sel)
    s = _ID_RX.sub(lambda m: _HASH_SUFFIX.sub('_HASH', m.group(1)), s)
    s = _CLS_RX.sub(lambda m: _HASH_SUFFIX.sub('_HASH', m.group(1)), s)
    return _NTH_RX.sub(':nth(N)', s)


# Re-render check: a new node "looks re-rendered" if the same rule already
# failed at baseline on a node with the same HTML, or on a selector that
# differs only in digits.
# Native-ad feed markup (Taboola, Outbrain, MGID, RevContent, Nativo, Zergnet).
NATIVE = re.compile(r'taboola|trc_|tbl-|outbrain|ob-widget|mgid|revcontent|nativo|zergnet', re.I)
_DIGITS = re.compile(r'[0-9a-f]{6,}|\d+', re.I)
_WS = re.compile(r'\s+')


def html_key(h):
    return _WS.sub(' ', _DIGITS.sub('#', (h or '').lower()))[:200]


def sel_key(sel):
    return re.sub(r'\d+', '#', canon_sel(sel))


def tgt0(node):
    # axe-core's target: one selector for a node in the top-level document,
    # a chain of selectors (frame element first) for a node inside a frame.
    t = node.get('target') or ['']
    s = t[0] if t else ''
    if isinstance(s, list): s = ' '.join(str(x) for x in s)
    return str(s)


def axe_ok(d):
    return not ('error' in (d['baseline'].get('axe') or {'error': 1})
                or 'error' in (d['post'].get('axe') or {'error': 1}))


def exclusion_reason(d):
    if (d.get('gating') or {}).get('classification') in EXCLUDE: return 'bot or hard consent wall'
    s = d.get('status')
    if s is None: return 'no HTTP status'
    if not isinstance(s, int) or s >= 400: return 'HTTP status >= 400'
    if 'baseline' not in d or 'post' not in d: return 'no baseline/post audit'
    if ((d.get('interactions') or {}).get('tabs') or 0) < 10: return 'fewer than 10 Tab presses'
    return None


def included(d, require_axe_ok=False):
    return exclusion_reason(d) is None and (not require_axe_ok or axe_ok(d))


def is_wall(d):
    url = (d.get('finalUrl') or '').split('?')[0]
    title = ((d.get('gating') or {}).get('title') or '').strip()
    return bool(WALL_URL.search(url) or WALL_TITLE.search(title))


def is_wcag(rule):
    return any(WCAG_TAG.match(t) for t in RULE_TAGS.get(rule, ()))


def n_nodes(axe):
    return sum(len(v.get('nodes', [])) for v in axe.get('violations', []))


def visit_record(d):
    """Everything the analyses need from one included visit."""
    b_axe, p_axe = d['baseline']['axe'], d['post']['axe']
    base, base_html, base_sel = set(), set(), set()
    for v in b_axe.get('violations', []):
        RULE_TAGS[v['id']].update(v.get('tags') or [])
        for n in v.get('nodes', []):
            base.add(v['id'] + '|' + canon_sel(tgt0(n)))
            base_html.add((v['id'], html_key(n.get('html'))))
            base_sel.add(v['id'] + '|' + sel_key(tgt0(n)))
    new, ad, sp, churn = Counter(), Counter(), Counter(), Counter()
    new_rule = Counter()
    base_rules = {v['id'] for v in b_axe.get('violations', []) if v.get('nodes')}
    is_w = lambda v: any(WCAG_TAG.match(t) for t in v.get('tags') or [])
    base_wcag = sum(len(v.get('nodes', [])) for v in b_axe.get('violations', []) if is_w(v))
    post_wcag = sum(len(v.get('nodes', [])) for v in p_axe.get('violations', []) if is_w(v))
    # rules that newly fail with at least one node in the top-level document
    # (an axe target with one entry); the rest newly fail only inside frames
    new_rule_top = {v['id'] for v in p_axe.get('violations', [])
                    if v.get('nodes') and v['id'] not in base_rules
                    and any(len(n.get('target') or []) <= 1 for n in v['nodes'])}
    sp_struct = sp_struct_native = 0
    native = False
    for v in p_axe.get('violations', []):
        RULE_TAGS[v['id']].update(v.get('tags') or [])
        if v.get('nodes') and v['id'] not in base_rules:
            new_rule[v['id']] = 1            # the rule did not fail at all at baseline
        for n in v.get('nodes', []):
            t, html = tgt0(n), n.get('html') or ''
            sig = v['id'] + '|' + canon_sel(t)
            if sig in base: continue
            new[v['id']] += 1
            cls = classify_match(t) or classify_match(html)
            ad[v['id']] += cls == 'ad'
            sp[v['id']] += cls == 'sponsored'
            native = native or bool(NATIVE.search(t + ' ' + html))
            if cls == 'sponsored' and v['id'] in LAYOUT_RULES:
                sp_struct += 1
                sp_struct_native += bool(NATIVE.search(t + ' ' + html))
            if ((v['id'], html_key(html)) not in base_html
                    and v['id'] + '|' + sel_key(t) not in base_sel):
                churn[v['id']] += 1
    items = (d['post'].get('inventory') or {}).get('items') or []
    started = d.get('startedAt')
    return {"new": new, "ad": ad, "sp": sp, "churn": churn,
            "new_rule": new_rule, "native": native,
            "base_nodes": n_nodes(b_axe), "post_nodes": n_nodes(p_axe),
            "base_wcag": base_wcag, "post_wcag": post_wcag, "new_rule_top": new_rule_top,
            "scrolls": (d.get('interactions') or {}).get('scrolls') or 0,
            "sp_struct": sp_struct, "sp_struct_native": sp_struct_native,
            "net": n_nodes(p_axe) - n_nodes(b_axe), "axe_ok": axe_ok(d),
            "gads": any(GADS.search(json.dumps(it)) for it in items),
            "start": datetime.fromisoformat(started.replace('Z', '+00:00')) if started else None,
            "hovers": (d.get('interactions') or {}).get('hovers') or 0,
            "tracker": int(d.get('trackerRequestsFromInteraction') or 0),
            "truncated": bool(d.get('requestsTruncated')),
            "vendors": set((d.get('vendors') or {}).keys()),
            "wall": is_wall(d),
            "final_host": (urlsplit(d.get('finalUrl') or '').hostname or '').lower()}


def load():
    """One pass over the main crawl. Returns {arm: {(host, k): record}}
    for included visits, plus record-level facts under '_'-prefixed keys."""
    for arm, d_ in ARMS.items():
        if not d_.is_dir() or not any(d_.glob("*.json")):
            sys.exit(f"no crawl records in {d_} (see README: Crawl data)")
    recs = {arm: {} for arm in ARMS}
    records, reasons, by_region, dns = Counter(), Counter(), Counter(), Counter()
    drop = platform_hosts() if DROP_PLATFORM else set()
    dropped, skipped_rounds = Counter(), Counter()
    for arm, d_ in ARMS.items():
        for p in sorted(d_.glob("*.json")):
            if p.name.split("__v")[0] in drop:
                dropped[arm] += 1; continue
            if int(p.stem.split("__v")[1]) not in ROUNDS:
                skipped_rounds[arm] += 1; continue
            try: d = json.loads(p.read_text())
            except Exception:
                reasons[(arm, 'unreadable')] += 1; continue
            records[arm] += 1
            why = exclusion_reason(d)
            reasons[(arm, why or 'included')] += 1
            s = d.get('status')
            if why == 'bot or hard consent wall' and isinstance(s, int) and s >= 400:
                reasons[(arm, 'wall flag with HTTP status >= 400')] += 1
            if arm == 'normal':
                reg = region_of(d.get('host', ''))
                by_region[(reg, 'records')] += 1
                by_region[(reg, 'included')] += why is None
                if d.get('status') is None and any('ERR_NAME_NOT_RESOLVED' in str(e.get('msg', ''))
                                                   for e in d.get('errors') or []):
                    dns[int(d.get('visit', 1))] += 1
            if why is None:
                recs[arm][(d['host'], int(d.get('visit', 1)))] = visit_record(d)
    recs["_records"] = dict(records)
    recs["_reasons"] = {a: {r: k for (a2, r), k in reasons.items() if a2 == a} for a in ARMS}
    recs["_inclusion_by_region"] = {g: round(100 * by_region[(g, "included")] / by_region[(g, "records")], 1)
                                    for g in sorted({g for g, _ in by_region})}
    recs["_dns_failures_by_visit"] = dict(sorted(dns.items()))
    recs["_platform_records_dropped"] = dict(dropped)
    recs["_round_records_skipped"] = dict(skipped_rounds)
    return recs


def view(recs, field="new", pred=None, combine=None):
    """{arm: {(host, k): Counter}} for analyse(): one per-visit Counter,
    optionally restricted to visits for which pred(arm, key, rec) holds."""
    out = {}
    for arm in ARMS:
        out[arm] = {}
        for key, r in recs[arm].items():
            if pred is not None and not pred(arm, key, r): continue
            out[arm][key] = r[field] + r[combine] if combine else r[field]
    return out


def wilson(k, n, z=1.96):
    p = k / n; dd = 1 + z * z / n; c = (p + z * z / (2 * n)) / dd
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / dd
    return [round(100 * p, 1), round(100 * (c - h), 1), round(100 * (c + h), 1)]


def hl(x):
    """Hodges-Lehmann estimator: the median of all pairwise (Walsh) averages."""
    x = np.asarray(x, float)
    i, j = np.triu_indices(len(x))
    return float(np.median((x[i] + x[j]) / 2))


def boot(x, f, b=B, seed=SEED):
    rng = np.random.default_rng(seed); x = np.asarray(x, float); n = len(x)
    vals = np.sort([f(x[rng.integers(0, n, n)]) for _ in range(b)])
    return [round(float(vals[int(0.025 * b)]), 2), round(float(vals[int(0.975 * b) - 1]), 2)]


def sign_shares(xs):
    n = len(xs)
    return {"n": n, "pos": round(100 * sum(v > 0 for v in xs) / n, 1),
            "neg": round(100 * sum(v < 0 for v in xs) / n, 1),
            "zero": round(100 * sum(v == 0 for v in xs) / n, 1)}


def analyse(visits, keep=None):
    tot = lambda c: sum(v for r, v in c.items() if keep is None or keep(r))
    nrm, blk = visits["normal"], visits["blocked"]
    paired = sorted(set(nrm) & set(blk))
    by_site = defaultdict(list)
    for h, k in paired:
        by_site[h].append((k, tot(nrm[(h, k)]) - tot(blk[(h, k)])))
    delta = {h: st.median(d for _, d in v) for h, v in by_site.items()}
    x = list(delta.values())
    k_pos = sum(v > 0 for v in x)
    res = {"pairs": len(paired), "sites": len(x), "shares": sign_shares(x),
           "pos_ci": wilson(k_pos, len(x)),
           "hl": round(hl(x), 2), "hl_ci": boot(x, hl),
           "mean": round(float(np.mean(x)), 2), "mean_ci": boot(x, np.mean),
           "median": float(np.median(x)),
           "cohen_d": round(float(np.mean(x) / np.std(x, ddof=1)), 2)}

    # Chance check ("noise"): the same `new` quantity on both sides. aa: the
    # earlier minus the later of a site's first two included normal visits;
    # ab: its first ads/blocked pair.
    normal_by_site = defaultdict(dict)
    for (h, k), c in nrm.items(): normal_by_site[h][k] = tot(c)
    rows = []                                   # (aa, ab) per site with both
    for h, v in sorted(normal_by_site.items()):
        if len(v) < 2 or h not in by_site: continue
        a, b = sorted(v)[:2]
        rows.append((v[a] - v[b], sorted(by_site[h])[0][1]))
    aa = [r[0] for r in rows]; ab = [r[1] for r in rows]
    net = lambda xs: 100 * (np.sum(np.asarray(xs) > 0) - np.sum(np.asarray(xs) < 0)) / len(xs)
    rng = np.random.default_rng(SEED); n = len(rows)
    arr = np.asarray(rows, float)
    bs_ab, bs_diff = [], []
    for _ in range(B):
        s = arr[rng.integers(0, n, n)]
        bs_ab.append(net(s[:, 1])); bs_diff.append(net(s[:, 1]) - net(s[:, 0]))
    ci = lambda v: [round(float(np.sort(v)[int(0.025 * B)]), 1), round(float(np.sort(v)[int(0.975 * B) - 1]), 1)]
    res["noise"] = {"sites": n, "aa": sign_shares(aa), "ab": sign_shares(ab),
                    "aa_net": round(net(aa), 1), "ab_net": round(net(ab), 1), "ab_net_ci": ci(bs_ab),
                    "excess_minus_aa": round(net(ab) - net(aa), 1), "excess_minus_aa_ci": ci(bs_diff),
                    "aa_disagree": round(100 * sum(v != 0 for v in aa) / n, 1)}

    # Table I counterfactual column: per-rule share over the same pairs.
    rn, rb = Counter(), Counter()
    for key in paired:
        rn.update(nrm[key]); rb.update(blk[key])
    share = lambda n_, b_: round(100 * max(n_ - b_, 0) / n_, 1) if n_ else 0.0
    fam = lambda rules: (sum(rn[r] for r in rules), sum(rb[r] for r in rules))
    res["per_rule"] = {r: {"normal": rn[r], "blocked": rb[r], "share": share(rn[r], rb[r])}
                       for r in TABLE_RULES}
    nn, nb = fam(NAMING_RULES); ln, lb = fam(LAYOUT_RULES)
    res["family"] = {"naming": share(nn, nb), "layout": share(ln, lb)}
    return res, delta


def selector_table(recs, keys, arm="normal"):
    """Table I selector columns over the given visits of one arm."""
    new, ad, sp = Counter(), Counter(), Counter()
    for key in keys:
        r = recs[arm][key]
        new.update(r["new"]); ad.update(r["ad"]); sp.update(r["sp"])
    pct = lambda a, n: round(100 * a / n, 1) if n else 0.0
    row = lambda rules: {"new": sum(new[r] for r in rules),
                         "ad": pct(sum(ad[r] for r in rules), sum(new[r] for r in rules)),
                         "ad_or_sponsored": pct(sum(ad[r] + sp[r] for r in rules),
                                                sum(new[r] for r in rules))}
    return {"visits": len(keys),
            "per_rule": {r: row([r]) for r in TABLE_RULES},
            "family": {"naming": row(NAMING_RULES), "layout": row(LAYOUT_RULES)}}


def rq1_any_visit(visits, keep=None):
    sites = defaultdict(int)
    for (h, _), c in visits["normal"].items():
        sites[h] = max(sites[h], sum(v for r, v in c.items() if keep is None or keep(r)))
    return wilson(sum(v > 0 for v in sites.values()), len(sites)) + [len(sites)]


def per_site_flags(visits, keep=None):
    """{host: [outcome on visit k > 0, ...]} over included normal visits."""
    out = defaultdict(list)
    for (h, _), c in sorted(visits["normal"].items()):
        out[h].append(sum(v for r, v in c.items() if keep is None or keep(r)) > 0)
    return out


def rq1_majority(visits, keep=None):
    # Outcome on more than half of a site's included visits (with two
    # visits: both).
    f = per_site_flags(visits, keep)
    return wilson(sum(sum(v) > len(v) / 2 for v in f.values()), len(f)) + [len(f)]


def tld(host):
    return host.rsplit('.', 1)[-1].lower()


def by_region(recs, delta):
    """Shares by region; Europe also split into EU states and others."""
    anyv, ad = per_site_flags(view(recs, "new")), per_site_flags(view(recs, "ad"))
    groups = defaultdict(set)
    for h in anyv:
        reg = region_of(h)
        groups[reg].add(h)
        if reg == "eu":
            groups["eu_member_states" if tld(h) not in NON_EU_IN_EUROPE else "europe_non_eu"].add(h)
    out = {}
    for g, hs in sorted(groups.items()):
        ds = [delta[h] for h in hs if h in delta]
        out[g] = {"sites": len(hs),
                  "any_new": wilson(sum(any(anyv[h]) for h in hs), len(hs)),
                  "selector_ad": wilson(sum(any(ad[h]) for h in hs), len(hs)),
                  "delta_sites": len(ds), "delta_pos": round(100 * sum(v > 0 for v in ds) / len(ds), 1)}
    out["europe_tlds"] = sorted(EU_TLDS)
    return out


def pair_timing(recs):
    gaps = []
    for key in sorted(set(recs["normal"]) & set(recs["blocked"])):
        a, b = recs["normal"][key]["start"], recs["blocked"][key]["start"]
        if a and b: gaps.append((b - a).total_seconds() / 3600)
    g = np.asarray(gaps)
    return {"pairs": len(gaps), "median_h": round(float(np.median(g)), 1),
            "p10_h": round(float(np.percentile(g, 10)), 1), "p90_h": round(float(np.percentile(g, 90)), 1),
            "over_12h_pct": round(100 * float(np.mean(g > 12)), 1), "blocked_first": int(np.sum(g < 0))}


def aa_timing(recs):
    """Hours between the two normal visits the chance check compares
    (the first two included normal visits of sites that also have a pair)."""
    paired_sites = {h for h, _ in set(recs["normal"]) & set(recs["blocked"])}
    by = defaultdict(dict)
    for (h, k), r in recs["normal"].items(): by[h][k] = r["start"]
    gaps = []
    for h, v in sorted(by.items()):
        if len(v) < 2 or h not in paired_sites: continue
        a, b = sorted(v)[:2]
        if v[a] and v[b]: gaps.append((v[b] - v[a]).total_seconds() / 3600)
    g = np.asarray(gaps)
    return {"sites": len(gaps), "median_h": round(float(np.median(g)), 1),
            "under_24h_pct": round(100 * float(np.mean(g < 24)), 1)}


def cf_table(triples):
    """Counterfactual shares over (host, Counter_with, Counter_without)
    triples: max(Σwith - Σwithout, 0) / Σwith per rule and family, with
    site-bootstrap 95% CIs for the two families. Used for the forward
    pairing (normal k, blocked k), the reverse pairing (normal k+1,
    blocked k) and two identical normal visits (earlier, later)."""
    rn, rb = Counter(), Counter()
    per_site = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    for h, a, b in triples:
        rn.update(a); rb.update(b)
        s = per_site[h]
        s[0] += sum(a[r] for r in NAMING_RULES); s[1] += sum(b[r] for r in NAMING_RULES)
        s[2] += sum(a[r] for r in LAYOUT_RULES); s[3] += sum(b[r] for r in LAYOUT_RULES)
    share = lambda n_, b_: round(100 * max(n_ - b_, 0) / n_, 1) if n_ else 0.0
    arr = np.asarray(list(per_site.values()), float)
    rng = np.random.default_rng(SEED); n = len(arr)
    bs_n, bs_l = [], []
    for _ in range(B):
        s = arr[rng.integers(0, n, n)].sum(axis=0)
        bs_n.append(100 * (s[0] - s[1]) / s[0]); bs_l.append(100 * (s[2] - s[3]) / s[2])
    ci = lambda v: [round(float(np.sort(v)[int(0.025 * B)]), 1), round(float(np.sort(v)[int(0.975 * B) - 1]), 1)]
    nn, nb = sum(rn[r] for r in NAMING_RULES), sum(rb[r] for r in NAMING_RULES)
    ln, lb = sum(rn[r] for r in LAYOUT_RULES), sum(rb[r] for r in LAYOUT_RULES)
    lw = [r for r in LAYOUT_RULES if is_wcag(r)]
    return {"pairs": len(triples), "sites": n,
            "per_rule": {r: share(rn[r], rb[r]) for r in TABLE_RULES},
            "family": {"naming": share(nn, nb), "layout": share(ln, lb)},
            "layout_wcag": share(sum(rn[r] for r in lw), sum(rb[r] for r in lw)),
            "layout_wcag_rules": len(lw),
            "family_ci": {"naming": ci(bs_n), "layout": ci(bs_l)},
            "layout_without_region": share(ln - rn["region"], lb - rb["region"]),
            "region_share_of_layout_new": round(100 * rn["region"] / ln, 1) if ln else 0.0}


def next_normal(N, h, k):
    """The site's next included normal visit after round k (or None)."""
    later = [k2 for k2 in sorted(ROUNDS) if k2 > k and (h, k2) in N]
    return (h, later[0]) if later else None


def pair_triples(recs):
    """Forward (normal k, blocked k), reverse (normal k+1, blocked k) and
    identical-visit (earlier, later included normal visit; chance-check
    sites) triples of per-visit `new` Counters."""
    N, Bl = recs["normal"], recs["blocked"]
    fwd = [(h, N[(h, k)]["new"], Bl[(h, k)]["new"]) for (h, k) in sorted(set(N) & set(Bl))]
    rev = [(h, N[next_normal(N, h, k)]["new"], Bl[(h, k)]["new"]) for (h, k) in sorted(Bl) if next_normal(N, h, k)]
    paired_sites = {h for h, *_ in fwd}
    by = defaultdict(list)
    for (h, k) in sorted(N): by[h].append(k)
    aa = [(h, N[(h, ks[0])]["new"], N[(h, ks[1])]["new"]) for h, ks in sorted(by.items())
          if len(ks) >= 2 and h in paired_sites]
    return fwd, rev, aa


def concentration(triples, rules, native_sites):
    """How much of a family's excess (Σwith - Σwithout) the sites with the
    largest excess supply, and the family share without them."""
    ex, w, wo = defaultdict(float), defaultdict(float), defaultdict(float)
    for h, a, b in triples:
        x, y = sum(a[r] for r in rules), sum(b[r] for r in rules)
        ex[h] += x - y; w[h] += x; wo[h] += y
    hosts = sorted(ex, key=lambda h: (-ex[h], h))
    total = sum(ex.values())
    out = {"sites": len(hosts), "total_excess": total,
           "native_pct_all_sites": round(100 * sum(h in native_sites for h in hosts) / len(hosts), 1)}
    for pct in (1, 5):
        k = max(1, round(len(hosts) * pct / 100))
        top, rest = hosts[:k], hosts[k:]
        sw, swo = sum(w[h] for h in rest), sum(wo[h] for h in rest)
        out[f"top{pct}"] = {"sites": k, "excess_share": round(100 * sum(ex[h] for h in top) / total, 1),
                            "share_without": round(100 * max(sw - swo, 0) / sw, 1) if sw else 0.0,
                            "native_pct": round(100 * sum(h in native_sites for h in top) / k, 1)}
    return out


def baseline_facts(recs):
    """How much already fails at load, and how much a visit adds."""
    nv = recs["normal"]
    by = defaultdict(list)
    for (h, _), r in nv.items(): by[h].append(r["base_nodes"] > 0)
    bn = [r["base_nodes"] for r in nv.values()]
    nw = [sum(r["new"].values()) for r in nv.values()]
    ratio = [sum(r["new"].values()) / r["base_nodes"] for r in nv.values() if r["base_nodes"]]
    return {"visits": len(nv),
            "visits_failing_at_load_pct": round(100 * sum(v > 0 for v in bn) / len(bn), 1),
            "sites_failing_at_load_every_visit_pct": round(100 * sum(all(v) for v in by.values()) / len(by), 1),
            "median_baseline_nodes": float(np.median(bn)), "median_new_nodes": float(np.median(nw)),
            "median_new_over_baseline_pct": round(100 * float(np.median(ratio)), 1)}


def scroll_check(recs):
    """The exercise scrolls until the page ends (6-20 steps), so the arms can
    scroll different distances. Δ restricted to pairs that scrolled the same."""
    N, Bl = recs["normal"], recs["blocked"]
    pairs = sorted(set(N) & set(Bl))
    more = sum(N[k]["scrolls"] > Bl[k]["scrolls"] for k in pairs)
    fewer = sum(N[k]["scrolls"] < Bl[k]["scrolls"] for k in pairs)
    by = defaultdict(list)
    for k in pairs:
        if N[k]["scrolls"] == Bl[k]["scrolls"]:
            by[k[0]].append(sum(N[k]["new"].values()) - sum(Bl[k]["new"].values()))
    d = [st.median(v) for v in by.values()]
    return {"pairs": len(pairs), "normal_more_pct": round(100 * more / len(pairs), 1),
            "normal_fewer_pct": round(100 * fewer / len(pairs), 1),
            "equal_pairs": sum(len(v) for v in by.values()), "equal_scroll_delta": sign_shares(d)}


def placebo_and_dose(recs, delta):
    """Δ by how many listed vendors a site requested in its included normal
    visits. With none, blocking removes nothing, so Δ shows only chance,
    order and time-of-day effects (a placebo)."""
    vend = defaultdict(set)
    for (h, _), r in recs["normal"].items(): vend[h] |= r["vendors"]
    bins = [("0", 0, 0), ("1-2", 1, 2), ("3-5", 3, 5), ("6-10", 6, 10), (">10", 11, 10 ** 6)]
    out = {}
    for name, lo, hi in bins:
        xs = [d for h, d in delta.items() if lo <= len(vend[h]) <= hi]
        s = sign_shares(xs) if xs else {"n": 0, "pos": 0, "neg": 0, "zero": 0}
        out[name] = s | {"net": round(s["pos"] - s["neg"], 1)}
    return out


def pass_then_fail(recs):
    """Visits whose load-time audit reports nothing, and how many of them
    report something after the exercise (WCAG-tagged rules, and all rules)."""
    nv = recs["normal"]
    out = {}
    for name, b, p in [("wcag", "base_wcag", "post_wcag"), ("all", "base_nodes", "post_nodes")]:
        clean = [(k, r) for k, r in nv.items() if r[b] == 0]
        later = [k for k, r in clean if r[p] > 0]
        sites = {h for h, _ in nv}
        out[name] = {"visits_clean_at_load": len(clean), "of_which_fail_later": len(later),
                     "sites_fail_later": len({h for h, _ in later}),
                     "sites_fail_later_pct": round(100 * len({h for h, _ in later}) / len(sites), 1)}
    return out


def new_rule_frames(recs):
    """Of the sites where a rule newly fails, the share where every such
    rule fails only inside frames."""
    any_new, top = set(), set()
    for (h, _), r in recs["normal"].items():
        if r["new_rule"]: any_new.add(h)
        if r["new_rule_top"]: top.add(h)
    only = any_new - top
    return {"sites_new_rule": len(any_new), "sites_frames_only": len(only),
            "frames_only_pct": round(100 * len(only) / len(any_new), 1)}


def structure_split(recs, fwd, rev):
    """Table I's structure family on the 5% of sites with the largest
    structure excess (forward pairs) and on the other 95%."""
    ex = defaultdict(float)
    for h, a, b in fwd:
        ex[h] += sum(a[r] for r in LAYOUT_RULES) - sum(b[r] for r in LAYOUT_RULES)
    hosts = sorted(ex, key=lambda h: (-ex[h], h))
    top = set(hosts[:round(len(hosts) * 0.05)])
    paired = sorted(set(recs["normal"]) & set(recs["blocked"]))
    out = {}
    for name, keep in [("top5", lambda h: h in top), ("rest", lambda h: h not in top)]:
        f = cf_table([t for t in fwd if keep(t[0])]); r_ = cf_table([t for t in rev if keep(t[0])])
        sel = selector_table(recs, [k for k in paired if keep(k[0])])["family"]["layout"]
        out[name] = {"sites": f["sites"], "cf_normal_first": f["family"]["layout"],
                     "cf_blocked_first": r_["family"]["layout"],
                     "selector_ad": sel["ad"], "selector_ad_or_sponsored": sel["ad_or_sponsored"]}
    return out


def time_of_day(recs):
    """When each arm's included visits started, in US Pacific daylight time
    (UTC-7; the crawls ran 8-20 October 2026)."""
    from datetime import timedelta
    out = {}
    for arm in ARMS:
        ts = [r["start"] - timedelta(hours=7) for r in recs[arm].values() if r["start"]]
        hrs = [t.hour for t in ts]
        out[arm] = {"visits": len(ts),
                    "h00_06_pct": round(100 * sum(h < 6 for h in hrs) / len(hrs), 1),
                    "h12_24_pct": round(100 * sum(h >= 12 for h in hrs) / len(hrs), 1),
                    "weekdays": dict(Counter(t.strftime("%a") for t in ts).most_common())}
    return out


def tracker_cost(recs):
    """Requests to listed vendors during the Tab and hover steps: per site,
    the median over its included normal visits; then the mean over sites,
    with a site-bootstrap CI."""
    by = defaultdict(list)
    for (h, _), r in recs["normal"].items(): by[h].append(r["tracker"])
    x = [st.median(v) for v in by.values()]
    return {"sites": len(x), "mean": round(float(np.mean(x)), 2), "mean_ci": boot(x, np.mean),
            "median": float(np.median(x))}


def order_check(recs, field="new"):
    """Visit order: pair each blocked visit k with the next normal visit k+1
    (blocked first) as well as the forward pairing (normal k first)."""
    tot = lambda r: sum(r[field].values())
    N, Bl = recs["normal"], recs["blocked"]

    def per_site(pairs, ok_only=False):
        by = defaultdict(list)
        for kn, kb in pairs:
            a, b = N[kn], Bl[kb]
            if ok_only and not (a["axe_ok"] and b["axe_ok"]): continue
            by[kn[0]].append(tot(a) - tot(b))
        return {h: st.median(v) for h, v in sorted(by.items())}

    def summ(d):
        x = np.asarray(list(d.values()), float)
        pos, neg = 100 * float(np.mean(x > 0)), 100 * float(np.mean(x < 0))
        return {"sites": len(x), "pos": round(pos, 1), "neg": round(neg, 1), "net": round(pos - neg, 1),
                "hl": round(hl(x), 2), "mean": round(float(np.mean(x)), 2)}

    fwd = [(k, k) for k in sorted(N) if k in Bl]
    rev = [(next_normal(N, h, k), (h, k)) for (h, k) in sorted(Bl) if next_normal(N, h, k)]
    gaps = [(N[kn]["start"] - Bl[kb]["start"]).total_seconds() / 3600 for kn, kb in rev
            if N[kn]["start"] and Bl[kb]["start"]]
    f, r = per_site(fwd), per_site(rev)
    common = sorted(set(f) & set(r))
    return {"reverse_pairs": len(rev), "reverse_gap_median_h": round(float(np.median(gaps)), 1),
            "forward": summ(f), "reverse": summ(r),
            "forward_common": summ({h: f[h] for h in common}), "reverse_common": summ({h: r[h] for h in common}),
            "crossover": summ({h: (f[h] + r[h]) / 2 for h in common}),
            "reverse_failed_audits_dropped": summ(per_site(rev, ok_only=True))}


def subset(recs, drop_hosts):
    return {a: {k: v for k, v in recs[a].items() if k[0] not in drop_hosts} for a in ARMS}


def brief(res, visits):
    return {"sites": res["sites"], "shares": res["shares"], "hl": res["hl"], "mean": res["mean"],
            "rq1_any_visit": rq1_any_visit(visits)}


def main():
    recs = load()
    main_v = view(recs)
    main_res, delta = analyse(main_v)
    m = main_res
    sampled, analysed, plat = read_sites("sites-crux-news.txt"), read_sites("sites-crux-news-filtered.txt"), platform_hosts()
    m["sample"] = {"sites_sampled": len(sampled), "platform_domains": len(plat), "sites_analysed": len(analysed),
                   "platform_domains_dropped": DROP_PLATFORM,
                   "platform_domains_included": len({h for h, _ in recs["normal"]} & plat)}
    m["rq1_any_visit"] = rq1_any_visit(main_v)
    m["rq1_majority"] = rq1_majority(main_v)
    m["rq1_selector_ad"] = rq1_any_visit(view(recs, "ad"))
    m["rq1_selector_ad_majority"] = rq1_majority(view(recs, "ad"))
    m["rq1_selector_ad_or_sponsored"] = rq1_any_visit(view(recs, "ad", combine="sp"))
    # Δ on the change in all violating nodes (post - baseline) per visit
    nn_ = defaultdict(list)
    for key in sorted(set(recs["normal"]) & set(recs["blocked"])):
        nn_[key[0]].append(recs["normal"][key]["net"] - recs["blocked"][key]["net"])
    x = [st.median(v) for v in nn_.values()]
    m["net_count"] = {"sites": len(x), "shares": sign_shares(x), "mean": round(float(np.mean(x)), 2),
                      "mean_ci": boot(x, np.mean)}
    # RQ1 counting only rules that did not fail at all at baseline
    m["rq1_new_rule_any"] = rq1_any_visit(view(recs, "new_rule"))
    m["rq1_new_rule_majority"] = rq1_majority(view(recs, "new_rule"))
    m["rq1_new_rule_wcag"] = rq1_any_visit(view(recs, "new_rule"), keep=is_wcag)
    m["baseline_facts"] = baseline_facts(recs)
    m["records"] = recs["_records"]
    m["platform_records_dropped"] = recs["_platform_records_dropped"]
    m["rounds_analysed"] = sorted(ROUNDS)
    m["round_records_skipped"] = recs["_round_records_skipped"]
    m["included_visits"] = {a: len(recs[a]) for a in ARMS}
    m["included_with_axe_error"] = {a: sum(not r["axe_ok"] for r in recs[a].values()) for a in ARMS}
    m["exclusions_normal"] = recs["_reasons"]["normal"]
    m["exclusions_blocked"] = recs["_reasons"]["blocked"]
    m["dns_failures_by_visit"] = recs["_dns_failures_by_visit"]
    m["inclusion_by_region"] = recs["_inclusion_by_region"]
    m["google_ad_frame_in_inventory"] = {a: wilson(sum(r["gads"] for r in recs[a].values()), len(recs[a]))
                                         for a in ARMS}
    m["google_ad_frame_blocked_sites"] = len({k[0] for k, r in recs["blocked"].items() if r["gads"]})
    m["pair_timing"] = pair_timing(recs)
    m["aa_timing"] = aa_timing(recs)
    m["order_check"] = order_check(recs)
    m["order_check_churn_filtered"] = order_check(recs, "churn")
    m["scroll_check"] = scroll_check(recs)
    starts = [r["start"] for a in ARMS for r in recs[a].values() if r["start"]]
    m["crawl_dates"] = [min(starts).date().isoformat(), max(starts).date().isoformat()]
    sps = sum(r["sp_struct"] for r in recs["normal"].values())
    m["sponsored_structure_native_pct"] = round(100 * sum(r["sp_struct_native"] for r in recs["normal"].values()) / sps, 1)
    m["hovers_per_visit"] = {a: round(float(np.mean([r["hovers"] for r in recs[a].values()])), 1) for a in ARMS}
    m["request_log_truncated"] = {a: sum(r["truncated"] for r in recs[a].values()) for a in ARMS}
    nn = Counter()
    for c in main_v["normal"].values(): nn.update(c)
    total = sum(nn.values())
    m["new_nodes_normal"] = {"total": total,
                             "non_wcag": sum(v for r, v in nn.items() if not is_wcag(r)),
                             "region": nn["region"]}
    paired = sorted(set(recs["normal"]) & set(recs["blocked"]))
    m["selector_table_paired"] = selector_table(recs, paired)
    m["selector_table_all_normal"] = selector_table(recs, sorted(recs["normal"]))
    m["selector_table_paired_blocked"] = selector_table(recs, paired, arm="blocked")
    fwd, rev, aa = pair_triples(recs)
    m["selector_table_reverse_normal"] = selector_table(recs, sorted({next_normal(recs["normal"], h, k) for (h, k) in recs["blocked"]
                                                                       if next_normal(recs["normal"], h, k)}))
    native_sites = {k[0] for k, r in recs["normal"].items() if r["native"]}
    m["table1_counterfactual"] = {"forward": cf_table(fwd), "reverse": cf_table(rev),
                                  "identical_visits": cf_table(aa)}
    m["table1_concentration"] = {"layout": concentration(fwd, LAYOUT_RULES, native_sites),
                                 "naming": concentration(fwd, NAMING_RULES, native_sites)}
    m["structure_split"] = structure_split(recs, fwd, rev)
    m["placebo_dose"] = placebo_and_dose(recs, delta)
    m["pass_then_fail"] = pass_then_fail(recs)
    m["new_rule_frames"] = new_rule_frames(recs)
    m["time_of_day"] = time_of_day(recs)
    m["tracker_cost"] = tracker_cost(recs)
    m["by_region"] = by_region(recs, delta)

    w, _ = analyse(main_v, keep=is_wcag)
    m["wcag_only"] = {"rq1_any_visit": rq1_any_visit(main_v, keep=is_wcag),
                      "sites": w["sites"], "shares": w["shares"], "hl": w["hl"]}

    sens = {}
    strict = view(recs, pred=lambda a, k, r: r["axe_ok"])
    r_, _ = analyse(strict)
    sens["failed_audits_dropped"] = brief(r_, strict) | {"noise": r_["noise"]}

    churn = view(recs, "churn")
    r_, _ = analyse(churn)
    cn = sum(sum(r["churn"].values()) for r in recs["normal"].values())
    sens["churn_filtered"] = brief(r_, churn) | {
        "noise": r_["noise"], "layout_counterfactual": r_["family"]["layout"], "naming_counterfactual": r_["family"]["naming"],
        "new_nodes_normal": cn, "share_looking_rerendered": round(100 * (1 - cn / total), 1)}

    nonad = {k[0] for k, r in recs["normal"].items() if r["vendors"] & NON_AD_UI}
    sub = view(subset(recs, nonad))
    r_, _ = analyse(sub)
    with_ = {h: d for h, d in delta.items() if h in nonad}
    sens["without_non_ad_ui_vendors"] = brief(r_, sub) | {
        "vendors": sorted(NON_AD_UI), "sites_dropped": len(nonad),
        "paired_sites_with_them": len(with_),
        "delta_pos_on_sites_with_them": round(100 * sum(v > 0 for v in with_.values()) / len(with_), 1)}

    walls = {k[0] for k, r in recs["normal"].items() if r["wall"]}
    wall_visits = view(recs, pred=lambda a, k, r: not r["wall"])
    r_, _ = analyse(wall_visits)
    all_walls = sorted(h for h in walls if all(
        r["wall"] for k, r in recs["normal"].items() if k[0] == h))
    final = {k[0]: r["final_host"] for k, r in recs["normal"].items() if r["wall"]}
    sens["walls_dropped"] = brief(r_, wall_visits) | {
        "sites_with_a_wall_visit": sorted(walls), "sites_all_visits_walls": all_walls,
        "dpg_media_gate_sites": sum("dpgmedia" in final[h] for h in all_walls)}

    out = {"definition": "new(normal,k) - new(blocked,k), median over rounds; both arms included;"
                         " new = violating nodes whose (rule, canonical selector) is absent at baseline",
           "main": m, "sensitivity": sens}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "delta.json").write_text(json.dumps(out, indent=2, default=str) + "\n")
    with (OUT / "delta-per-site.csv").open("w", newline="") as f:
        wr = csv.writer(f); wr.writerow(["host", "delta"])
        for h in sorted(delta): wr.writerow([h, delta[h]])
    # Per-site RQ1 flags (any visit / majority of visits).
    anyv, ad = per_site_flags(main_v), per_site_flags(view(recs, "ad"))
    nr = per_site_flags(view(recs, "new_rule"))
    with (OUT / "rq1-per-site.csv").open("w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["host", "visits", "new_any", "new_majority", "newrule_any", "newrule_majority",
                     "ad_any", "ad_majority"])
        for h in sorted(anyv):
            wr.writerow([h, len(anyv[h]), int(any(anyv[h])), int(sum(anyv[h]) > len(anyv[h]) / 2),
                         int(any(nr[h])), int(sum(nr[h]) > len(nr[h]) / 2),
                         int(any(ad[h])), int(sum(ad[h]) > len(ad[h]) / 2)])

    nz, st_, sp_, sa_ = m["noise"], m["selector_table_paired"], sens, m["selector_table_all_normal"]
    lines = [
        "Δ: new(normal) - new(blocked), both arms included, median over rounds",
        "=" * 72,
        f"sample {m['sample']}",
        f"records {m['records']}; included visits {m['included_visits']}, axe error {m['included_with_axe_error']}",
        f"normal-arm records by outcome: {m['exclusions_normal']}",
        f"blocked-arm records by outcome: {m['exclusions_blocked']}",
        f"normal-arm DNS failures (ERR_NAME_NOT_RESOLVED) by visit: {m['dns_failures_by_visit']}",
        f"normal-arm inclusion rate by region (%): {m['inclusion_by_region']}",
        "",
        f"RQ1 any new: any visit {m['rq1_any_visit']}  majority of visits {m['rq1_majority']}",
        f"RQ1 newly failing rule (did not fail at all at baseline): any visit {m['rq1_new_rule_any']}"
        f"  majority {m['rq1_new_rule_majority']}  WCAG-only {m['rq1_new_rule_wcag']}",
        f"RQ1 newly failing rules, frames only: {json.dumps(m['new_rule_frames'])}",
        f"RQ1 clean at load, failing after: {json.dumps(m['pass_then_fail'])}",
        f"RQ1 baseline facts: {json.dumps(m['baseline_facts'])}",
        f"RQ1 selector ad: any visit {m['rq1_selector_ad']}  majority {m['rq1_selector_ad_majority']};"
        f" ad or sponsored, any visit {m['rq1_selector_ad_or_sponsored']}",
        f"new nodes (normal, included): {m['new_nodes_normal']}",
        "",
        f"paired visits {m['pairs']}, sites {m['sites']}",
        f"Δ>0 {m['shares']['pos']}% {m['pos_ci']}   Δ<0 {m['shares']['neg']}%   Δ=0 {m['shares']['zero']}%",
        f"Hodges-Lehmann {m['hl']:+} {m['hl_ci']}   mean {m['mean']:+} {m['mean_ci']}   median {m['median']}"
        f"   d {m['cohen_d']}",
        "",
        f"chance check (sites with 2 normal visits and a pair: {nz['sites']})",
        f"  identical visits, earlier-later: >0 {nz['aa']['pos']}  <0 {nz['aa']['neg']}  disagree {nz['aa_disagree']}"
        f"  net {nz['aa_net']}",
        f"  first ads/blocked pair:          >0 {nz['ab']['pos']}  <0 {nz['ab']['neg']}  net {nz['ab_net']} {nz['ab_net_ci']}",
        f"  pair net - identical net: {nz['excess_minus_aa']} {nz['excess_minus_aa_ci']}",
        f"order check (blocked first = reverse): {json.dumps(m['order_check'])}",
        f"order check, without re-rendered-looking nodes: {json.dumps(m['order_check_churn_filtered'])}",
        f"placebo and dose (Δ by listed vendors requested): {json.dumps(m['placebo_dose'])}",
        "",
        f"Table I over the {st_['visits']} paired normal visits: rule  selector-ad  ad-or-sponsored  counterfactual",
        *[f"  {r:20s} {st_['per_rule'][r]['ad']:5.1f}  {st_['per_rule'][r]['ad_or_sponsored']:5.1f}"
          f"  {m['per_rule'][r]['share']:5.1f}  (new {st_['per_rule'][r]['new']}; normal {m['per_rule'][r]['normal']},"
          f" blocked {m['per_rule'][r]['blocked']})" for r in TABLE_RULES],
        f"  naming family {st_['family']['naming']['ad']} / {st_['family']['naming']['ad_or_sponsored']}"
        f" / {m['family']['naming']}",
        f"  structure family {st_['family']['layout']['ad']} / {st_['family']['layout']['ad_or_sponsored']}"
        f" / {m['family']['layout']}",
        f"  (selector shares over all {sa_['visits']} included normal visits: structure "
        f"{sa_['family']['layout']['ad']} / {sa_['family']['layout']['ad_or_sponsored']}, naming"
        f" {sa_['family']['naming']['ad']} / {sa_['family']['naming']['ad_or_sponsored']})",
        "Table I counterfactual by pairing (family shares with site-bootstrap CIs; layout = structure):",
        *[f"  {k}: {json.dumps(v)}" for k, v in m['table1_counterfactual'].items()],
        f"  selector shares on reverse-pair normal visits: {json.dumps(m['selector_table_reverse_normal']['family'])}",
        f"Table I concentration (forward pairs): {json.dumps(m['table1_concentration'])}",
        f"structure family, top 5% vs the rest: {json.dumps(m['structure_split'])}",
        f"sponsored structure-family new nodes with native-ad markup: {m['sponsored_structure_native_pct']}%",
        f"blocked arm, button-name new nodes {m['selector_table_paired_blocked']['per_rule']['button-name']}",
        "",
        "by region: " + json.dumps({g: v for g, v in m['by_region'].items() if g != 'europe_tlds'}),
        "",
        f"normal->blocked start gap within a round: {m['pair_timing']}",
        f"identical-visit gap: {m['aa_timing']}; crawl dates {m['crawl_dates']}",
        f"time of day (PDT): {json.dumps(m['time_of_day'])}",
        f"hovers per included visit: {m['hovers_per_visit']}",
        f"scroll check: {json.dumps(m['scroll_check'])}",
        f"request log truncated at the 5,000-request cap: {m['request_log_truncated']}",
        f"Google ad frame in post inventory: {m['google_ad_frame_in_inventory']};"
        f" blocked-arm sites with one: {m['google_ad_frame_blocked_sites']}",
        f"tracker requests during Tab and hover, per site: {json.dumps(m['tracker_cost'])}",
        "",
        "variants:",
        f"  WCAG-tagged rules only: RQ1 {m['wcag_only']['rq1_any_visit']}; Δ sites {m['wcag_only']['sites']}"
        f" >0 {m['wcag_only']['shares']['pos']}% <0 {m['wcag_only']['shares']['neg']}% HL {m['wcag_only']['hl']:+}",
        f"  net-count Δ (change in all violating nodes): sites {m['net_count']['sites']},"
        f" Δ>0 {m['net_count']['shares']['pos']}%, Δ<0 {m['net_count']['shares']['neg']}%,"
        f" mean {m['net_count']['mean']:+} {m['net_count']['mean_ci']}",
        f"  pairs that scrolled the same distance: {json.dumps(m['scroll_check']['equal_scroll_delta'])}",
        *[f"  {k}: " + json.dumps({kk: vv for kk, vv in v.items() if kk not in
                                   ('sites_with_a_wall_visit', 'noise', 'vendors')})
          for k, v in sp_.items()],
        f"  failed audits dropped, chance check: identical net {sp_['failed_audits_dropped']['noise']['aa_net']},"
        f" pair net {sp_['failed_audits_dropped']['noise']['ab_net']},"
        f" difference {sp_['failed_audits_dropped']['noise']['excess_minus_aa']}"
        f" {sp_['failed_audits_dropped']['noise']['excess_minus_aa_ci']}",
        f"  without re-rendered-looking nodes, chance check: identical net {sp_['churn_filtered']['noise']['aa_net']},"
        f" pair net {sp_['churn_filtered']['noise']['ab_net']} {sp_['churn_filtered']['noise']['ab_net_ci']},"
        f" difference {sp_['churn_filtered']['noise']['excess_minus_aa']}"
        f" {sp_['churn_filtered']['noise']['excess_minus_aa_ci']}",
        f"  wall sites (any included normal visit): {sp_['walls_dropped']['sites_with_a_wall_visit']}",
    ]
    (OUT / "delta.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

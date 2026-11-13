#!/usr/bin/env python3
"""Numbers in the paper that analysis/delta.py does not compute.

  - RQ3 (in-frame crawl): sites whose visible cross-origin frames fail a
    rule, split into frames served from a host on the block list
    (data/ad-vendors.json) and other frames; node counts per rule in
    visible frames (figure fig7-oopif-vs-mainframe); stricter visibility
    definitions; the 25-frame cap; crawl dates. The in-frame crawl uses the
    main crawl's inclusion rule. Rules that axe-core applies only to the top-level
    document (`matches: 'is-initiator-matches'` in the installed axe-core
    bundle: document-title, html-has-lang, meta-viewport, ...) are reported
    separately, because a per-frame axe.run() makes every frame an
    initiator and so evaluates them where a normal audit never would.
  - Main crawl: share of post-exercise violating nodes that axe-core's
    Playwright integration reported from inside frames, and how many of
    those frames' selectors look like ads; vendors seen in request logs.
  - The author's 50 hand labels (data/labels/hand-labels-50.json): share of
    news publishers.
  - Facts: the Chromium build that Playwright ships, the regional quotas of
    the sample and how popular each region's sites are.

Reads the crawl records; writes results/extra-numbers.{json,txt}. Reads
axe-core and Playwright metadata from node_modules (run `npm ci`, or set
NODE_MODULES). Usage (repo root): python3 analysis/extra_numbers.py
"""
import json, math, re, sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (DATA, DROP_PLATFORM, INFRAME_NORMAL, MAIN_ARMS, NODE_MODULES, OUT, ROUNDS,  # noqa: E402
                    classify_match, platform_hosts, region_of)
from delta import GADS, included  # noqa: E402

DROP = platform_hosts() if DROP_PLATFORM else set()
FRAME_CAP = 25
GOOGLE = {"Google GPT/DoubleClick", "AdSense (Google)", "Google IMA SDK (ads in video)"}


def node_file(rel):
    p = NODE_MODULES / rel
    if not p.exists():
        sys.exit(f"{p} not found: run `npm ci` at the repository root, or set NODE_MODULES")
    return p


def initiator_only_rules():
    src = node_file("axe-core/axe.js").read_text()
    return sorted({m.group(1) for m in re.finditer(
        r"id: '([a-z0-9-]+)',(.{0,600}?)(?=id: '|\Z)", src, re.S)
        if "is-initiator-matches" in m.group(2)})


def vendor_matcher():
    pats = []
    for v in json.loads((DATA / "ad-vendors.json").read_text())["vendors"]:
        for p in v["patterns"]:
            host, _, path = p.partition("/")
            pats.append((host.lower(), "/" + path if path else "", v["vendor"]))

    def match(url):
        try: u = urlsplit(url)
        except ValueError: return None
        h = (u.hostname or "").lower()
        for host, path, name in pats:
            if (h == host or h.endswith("." + host)) and u.path.startswith(path):
                return name
        return None
    return match


def wilson(k, n, z=1.96):
    p = k / n; d = 1 + z * z / n; c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return {"k": k, "n": n, "pct": round(100 * p, 1),
            "lo": round(100 * (c - h), 1), "hi": round(100 * (c + h), 1)}


def inframe_files():
    if not INFRAME_NORMAL.is_dir() or not any(INFRAME_NORMAL.glob("*.json")):
        sys.exit(f"no crawl records in {INFRAME_NORMAL} (see README: Crawl data)")
    return sorted(f for f in INFRAME_NORMAL.glob("*.json") if f.name.split("__v")[0] not in DROP)


def inframe(skip_rules):
    vendor_of = vendor_matcher()
    files = inframe_files()
    n = 0
    stats = {key: {"any": set(), "ad": set(), "other": set(), "rule_nodes": Counter(),
                   "rule_nodes_vendor": Counter(), "rule_nodes_other": Counter(),
                   "rule_sites": defaultdict(set), "ad_nodes": 0, "google_nodes": 0, "other_nodes": 0,
                   "strict": {10000: set(), 50000: set()}}
             for key in ("all_rules", "frame_rules")}
    capped, started, main_post_nodes, frames_seen = 0, [], 0, 0
    by_viewport = set()   # variant: visible = the frame's viewport, not its <body>, > 2,500 px²
    for p in files:
        try: d = json.loads(p.read_text())
        except Exception: continue
        if not included(d):
            continue
        n += 1
        host = d.get("host", p.stem.split("__v")[0])
        frames = (d.get("post") or {}).get("oopifAxe") or []
        frames_seen += len(frames)
        capped += len(frames) >= FRAME_CAP
        main_post_nodes += sum(len(v.get("nodes") or []) for v in
                               ((d.get("post") or {}).get("axe") or {}).get("violations") or [])
        if d.get("startedAt"): started.append(d["startedAt"])
        for o in frames:
            vendor = vendor_of(o.get("frameUrl") or o.get("origin") or "")
            area = (o.get("body") or {}).get("w", 0) * (o.get("body") or {}).get("h", 0)
            vp = (o.get("viewport") or {}).get("w", 0) * (o.get("viewport") or {}).get("h", 0)
            if vp > 2500 and any(v["id"] not in skip_rules and v.get("nodes") for v in o.get("violations") or []):
                by_viewport.add(host)
            for key, s in stats.items():
                viol = [v for v in o.get("violations") or []
                        if key == "all_rules" or v["id"] not in skip_rules]
                k = sum(len(v.get("nodes") or []) for v in viol)
                if not k: continue
                for t in s["strict"]:
                    if area > t: s["strict"][t].add(host)
                if not o.get("visible"): continue
                s["any"].add(host)
                if vendor:
                    s["ad"].add(host); s["ad_nodes"] += k
                    s["google_nodes"] += k if vendor in GOOGLE else 0
                else:
                    s["other"].add(host); s["other_nodes"] += k
                for v in viol:
                    s["rule_nodes"][v["id"]] += len(v.get("nodes") or [])
                    s["rule_nodes_vendor" if vendor else "rule_nodes_other"][v["id"]] += len(v.get("nodes") or [])
                    s["rule_sites"][v["id"]].add(host)
    res = {"records": len(files), "sites": n, "frames_recorded": frames_seen,
           "frame_cap_reached": capped, "crawl_dates": [min(started)[:10], max(started)[:10]],
           "main_audit_post_nodes": main_post_nodes,
           "any_visible_by_viewport": wilson(len(by_viewport), n),
           "initiator_only_rules": sorted(skip_rules)}
    for key, s in stats.items():
        nodes = sum(s["rule_nodes"].values())
        res[key] = {"any_visible": wilson(len(s["any"]), n), "ad_vendor": wilson(len(s["ad"]), n),
                    "other": wilson(len(s["other"]), n), "visible_nodes": nodes,
                    "ad_nodes": s["ad_nodes"], "google_nodes": s["google_nodes"],
                    "google_pct_of_nodes": round(100 * s["google_nodes"] / nodes, 1),
                    "other_nodes": s["other_nodes"],
                    "ad_sites_pct_of_any": round(100 * len(s["ad"]) / len(s["any"]), 1),
                    "stricter_thresholds": {str(t): wilson(len(h), n) for t, h in s["strict"].items()},
                    "per_rule": [{"rule": r, "nodes": k, "sites": len(s["rule_sites"][r]),
                                  "vendor_nodes": s["rule_nodes_vendor"][r], "other_nodes": s["rule_nodes_other"][r]}
                                 for r, k in s["rule_nodes"].most_common()]}
    return res


def main_crawl():
    tot = inside = ad = visits = 0
    vendors, google = Counter(), set()
    sites = set()
    for p in sorted(MAIN_ARMS["normal"].glob("*.json")):
        if p.name.split("__v")[0] in DROP: continue
        if int(p.stem.split("__v")[1]) not in ROUNDS: continue
        try: d = json.loads(p.read_text())
        except Exception: continue
        if not included(d): continue
        visits += 1
        sites.add(d["host"])
        vs = set((d.get("vendors") or {}).keys())
        vendors.update({(d["host"], v) for v in vs})
        if "Google GPT/DoubleClick" in vs: google.add(d["host"])
        for v in ((d.get("post") or {}).get("axe") or {}).get("violations") or []:
            for nd in v.get("nodes") or []:
                tot += 1
                t = nd.get("target")
                if isinstance(t, list) and len(t) > 1:
                    inside += 1
                    sel = str(t[0])
                    ad += bool(GADS.search(sel) or classify_match(sel) == "ad")
    names = {v for _, v in vendors}
    return {"visits": visits, "sites": len(sites), "post_nodes": tot, "in_frame": inside,
            "in_frame_pct": round(100 * inside / tot, 1),
            "ad_looking_pct_of_in_frame": round(100 * ad / max(inside, 1), 1),
            "vendors_seen": len(names),
            "google_gpt_doubleclick_sites": wilson(len(google), len(sites))}


def news_precision():
    # The author's hand labels of 50 sites (annotators/hand_label_sites.py
    # draws them): news_publisher / not_news_publisher / ambiguous.
    u = json.loads((DATA / "labels/hand-labels-50.json").read_text())
    lab = [v["label"] for v in u.values()]
    return {"labelled": len(lab), "news": lab.count("news_publisher"),
            "not": lab.count("not_news_publisher"), "ambiguous": lab.count("ambiguous"),
            "strict": wilson(lab.count("news_publisher"), len(lab))}


def stratum_popularity():
    # Share of each region's sampled sites in CrUX rank buckets <= 50k
    # (the sample fills regional quotas in CrUX rank order, sample/sample.py).
    prov = json.loads((DATA / "sites-crux-news.provenance.json").read_text())
    by = defaultdict(list)
    for s in prov["sites"]:
        by[region_of(s["domain"])].append(s["rank"] <= 50000)
    return {r: {"sites": len(v), "top50k_pct": round(100 * sum(v) / len(v), 1)} for r, v in sorted(by.items())}


def facts():
    br = json.loads(node_file("playwright-core/browsers.json").read_text())["browsers"]
    return {"chromium": next(b["browserVersion"] for b in br if b["name"] == "chromium"),
            "stratum_popularity": stratum_popularity()}


def main():
    res = {"inframe": inframe(set(initiator_only_rules())), "main_crawl": main_crawl(),
           "news_precision": news_precision(), "facts": facts()}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "extra-numbers.json").write_text(json.dumps(res, indent=2) + "\n")
    fr, ma = res["inframe"], res["main_crawl"]
    w = lambda x: f"{x['k']}/{x['n']} = {x['pct']}% [{x['lo']}, {x['hi']}]"
    lines = ["Numbers not computed by analysis/delta.py", "=" * 60, "",
             f"RQ3 in-frame crawl: {fr['records']} records, {fr['sites']} included sites, "
             f"{fr['frames_recorded']} frames recorded; dates {fr['crawl_dates']}",
             f"  25-frame cap reached (25 frames recorded) on {fr['frame_cap_reached']} sites",
             f"  main-audit post nodes on these visits: {fr['main_audit_post_nodes']}",
             f"  top-document-only rules: {fr['initiator_only_rules']}",
             f"  variant, visible = frame viewport > 2,500 px2: {w(fr['any_visible_by_viewport'])}"]
    for key, label in (("frame_rules", "rules that apply inside frames"), ("all_rules", "all rules")):
        s = fr[key]
        lines += [f"  -- {label}",
                  f"     any visible frame with a violation: {w(s['any_visible'])}",
                  f"     block-list vendor frame:            {w(s['ad_vendor'])}"
                  f"  ({s['ad_sites_pct_of_any']}% of the sites above)",
                  f"     other frame:                        {w(s['other'])}  ({s['other_nodes']} nodes)",
                  f"     visible violating nodes {s['visible_nodes']}; vendor-frame nodes {s['ad_nodes']};"
                  f" Google {s['google_nodes']} ({s['google_pct_of_nodes']}%)",
                  f"     stricter visibility: " + ", ".join(f"> {t} px2: {x['pct']}%" for t, x in
                                                          s["stricter_thresholds"].items()),
                  "     per rule (nodes, sites): " + "; ".join(f"{r['rule']} {r['nodes']}/{r['sites']}"
                                                            for r in s["per_rule"][:10])]
    lines += ["",
              f"Main crawl, {ma['visits']} included normal visits on {ma['sites']} sites: {ma['in_frame']}/"
              f"{ma['post_nodes']} post nodes inside frames ({ma['in_frame_pct']}%),"
              f" {ma['ad_looking_pct_of_in_frame']}% of them in frames whose selector looks like an ad",
              f"  block-list vendors seen in request logs: {ma['vendors_seen']};"
              f" Google GPT/DoubleClick entry requested on {w(ma['google_gpt_doubleclick_sites'])} sites", "",
              f"Hand labels (news publishers): {res['news_precision']}",
              f"Facts: {res['facts']}"]
    (OUT / "extra-numbers.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

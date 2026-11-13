#!/usr/bin/env python3
"""Numbers behind three figures that analysis/delta.py and
analysis/extra_numbers.py do not compute:

  rq1_rules    which rules newly fail (fail after the exercise, failed nowhere
               at load), on how many sites, whether only inside frames, and how
               often a rule recurs on both of a site's included visits
               (figure fig16-rq1-new-rules)
  delta_tails  how large the normal-minus-blocked difference in new violating
               nodes is, against the difference between two identical normal
               visits, on the chance-check sites (first paired round)
               (figure fig17-rq2-delta-size)
  frame_orgs   for each organisation serving visible cross-origin frames on at
               least MIN_REACH sites of the in-frame crawl: sites reached, and
               sites where its frame fails a rule that applies inside frames
               (figure fig18-rq3-frame-orgs)

Writes results/figure-numbers.{json,txt}. It reuses the loaders of delta.py
and extra_numbers.py, so inclusion, rounds and the platform-domain filter
are the same, and it asserts that its overlap with delta.json and
extra-numbers.json agrees. Run after both scripts.
"""
import json, sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import delta as S              # noqa: E402
import extra_numbers as E      # noqa: E402
from common import OUT         # noqa: E402

THRESHOLDS = [1, 5, 10, 25, 50, 100]   # |difference| in new violating nodes; 1 = "> 0"
MIN_REACH = 30                         # frame_orgs: sites reached
# second-level public suffixes seen among frame hosts
SLD2 = {"co.uk", "com.au", "co.jp", "com.br", "co.in", "co.il", "com.tr", "co.nz", "co.za",
        "com.mx", "com.ar", "com.sg"}


def registrable(url):
    h = (urlsplit(url).hostname or "").lower()
    parts = h.split(".")
    return ".".join(parts[-(3 if ".".join(parts[-2:]) in SLD2 else 2):])


def rq1_rules(recs):
    by_site = defaultdict(list)
    for (h, _), r in sorted(recs["normal"].items()):
        by_site[h].append(r)
    n = len(by_site)
    any_, top, both, either = Counter(), Counter(), Counter(), Counter()
    sites_any = 0
    for h, rs in by_site.items():
        rules = set().union(*[set(r["new_rule"]) for r in rs])
        sites_any += bool(rules)
        for x in rules: any_[x] += 1
        for x in set().union(*[r["new_rule_top"] for r in rs]): top[x] += 1
        if len(rs) >= 2:
            a, b = set(rs[0]["new_rule"]), set(rs[1]["new_rule"])
            for x in a | b:
                either[x] += 1
                both[x] += x in a and x in b
    rows = [{"rule": x, "wcag": S.is_wcag(x), "sites": c, "pct": round(100 * c / n, 1),
             "top_doc_sites": top[x], "frames_only_sites": c - top[x],
             "recur_both": both[x], "recur_either": either[x],
             "recur_pct": round(100 * both[x] / either[x], 1) if either[x] else None}
            for x, c in sorted(any_.items(), key=lambda kv: (-kv[1], kv[0]))]
    return {"sites": n, "sites_with_new_rule": sites_any, "rules": rows[:12],
            "region": next((r for r in rows if r["rule"] == "region"), None)}


def delta_tails(recs):
    fwd, _, aa = S.pair_triples(recs)
    aa_sites = {h for h, *_ in aa}
    first = {}
    for h, a, b in fwd:                       # sorted by (host, round): first paired round
        if h in aa_sites and h not in first:
            first[h] = sum(a.values()) - sum(b.values())
    ident = {h: sum(a.values()) - sum(b.values()) for h, a, b in aa}   # earlier - later
    out = {"sites": len(first), "thresholds": THRESHOLDS}
    for name, vals in (("ads_minus_blocked", list(first.values())), ("identical_visits", list(ident.values()))):
        out[name] = [{"t": t, "up": E.wilson(sum(v >= t for v in vals), len(vals)),
                      "down": E.wilson(sum(v <= -t for v in vals), len(vals))} for t in THRESHOLDS]
    return out


def frame_orgs():
    skip = set(E.initiator_only_rules())
    vendor_of = E.vendor_matcher()
    files = E.inframe_files()
    reach, fail, listed = defaultdict(set), defaultdict(set), {}
    n, failing = 0, set()
    for p in files:
        try: d = json.loads(p.read_text())
        except Exception: continue
        if not S.included(d): continue
        n += 1
        host = d.get("host", p.stem.split("__v")[0])
        for o in (d.get("post") or {}).get("oopifAxe") or []:
            if not o.get("visible"): continue
            url = o.get("frameUrl") or o.get("origin") or ""
            v = vendor_of(url)
            g = v or registrable(url)
            listed[g] = bool(v)
            reach[g].add(host)
            if any(x["id"] not in skip and x.get("nodes") for x in o.get("violations") or []):
                fail[g].add(host); failing.add(host)
    rows = [{"org": g, "listed": listed[g], "reach": len(reach[g]),
             "failing": E.wilson(len(fail[g]), len(reach[g]))}
            for g in sorted(reach, key=lambda g: (-len(reach[g]), g)) if len(reach[g]) >= MIN_REACH]
    return {"sites": n, "failing_sites": len(failing), "min_reach": MIN_REACH, "orgs": rows}


def main():
    recs = S.load()
    res = {"rq1_rules": rq1_rules(recs), "delta_tails": delta_tails(recs), "frame_orgs": frame_orgs()}

    # agreement with the other summaries
    dl = json.loads((OUT / "delta.json").read_text())["main"]
    ex = json.loads((OUT / "extra-numbers.json").read_text())["inframe"]["frame_rules"]["any_visible"]
    r1, dt, fo = res["rq1_rules"], res["delta_tails"], res["frame_orgs"]
    assert r1["sites"] == dl["rq1_any_visit"][3], (r1["sites"], dl["rq1_any_visit"])
    assert r1["sites_with_new_rule"] == dl["new_rule_frames"]["sites_new_rule"], r1["sites_with_new_rule"]
    nz = dl["noise"]
    assert dt["sites"] == nz["sites"], dt["sites"]
    for name, key in (("ads_minus_blocked", "ab"), ("identical_visits", "aa")):
        t1 = dt[name][0]
        assert (t1["up"]["pct"], t1["down"]["pct"]) == (nz[key]["pos"], nz[key]["neg"]), (name, t1, nz[key])
    assert (fo["sites"], fo["failing_sites"]) == (ex["n"], ex["k"]), (fo["sites"], fo["failing_sites"], ex)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "figure-numbers.json").write_text(json.dumps(res, indent=2) + "\n")
    pct = lambda w: f"{w['pct']}% [{w['lo']}, {w['hi']}]"
    lines = ["Numbers behind the RQ1-rules, RQ2-size and RQ3-host figures",
             "=" * 78, "",
             f"RQ1 newly failing rules ({r1['sites']} sites; {r1['sites_with_new_rule']} with any):",
             *[f"  {r['rule']:22s} {'wcag' if r['wcag'] else 'bp  '} sites {r['sites']:4d} ({r['pct']}%)"
               f"  frames only {r['frames_only_sites']:4d}  recurs on both visits {r['recur_both']}/{r['recur_either']}"
               f" ({r['recur_pct']}%)" for r in r1["rules"]],
             f"  region: {r1['region']}", "",
             f"RQ2 size of the difference in new violating nodes ({dt['sites']} chance-check sites, first paired round):",
             *[f"  t={a['t']:<3d} ads-blocked >=t {pct(a['up'])}  <=-t {pct(a['down'])}   |   identical"
               f" earlier-later >=t {pct(b['up'])}  <=-t {pct(b['down'])}"
               for a, b in zip(dt["ads_minus_blocked"], dt["identical_visits"])], "",
             f"RQ3 organisations serving visible frames on >= {fo['min_reach']} of {fo['sites']} sites"
             f" ({fo['failing_sites']} sites with a failing visible frame):",
             *[f"  {o['org']:32s} {'listed' if o['listed'] else 'other '} reach {o['reach']:4d}"
               f"  failing {o['failing']['k']:4d} = {pct(o['failing'])}" for o in fo["orgs"]]]
    (OUT / "figure-numbers.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

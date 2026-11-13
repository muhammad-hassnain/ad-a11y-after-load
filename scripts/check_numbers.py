#!/usr/bin/env python3
"""Check every number the paper reports against the regenerated results.

Reads results/{delta,extra-numbers,figure-numbers}.json, results/rq1-per-site.csv,
results/annotators/agreement.json, the data files, package-lock.json and the
harness source (for the protocol constants the paper states). Expected
values are the ones printed in the paper, grouped by section; a value that
the paper rounds is compared after the same rounding. Exits non-zero on any
mismatch. Stdlib only.

Usage (repo root, after the numbers scripts): python3 scripts/check_numbers.py
"""
import csv, json, os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = Path(os.environ.get("RESULTS_DIR") or ROOT / "results")
dl = json.loads((RES / "delta.json").read_text())
ex = json.loads((RES / "extra-numbers.json").read_text())
fn = json.loads((RES / "figure-numbers.json").read_text())
ag = json.loads((RES / "annotators/agreement.json").read_text())
m, nz, sens = dl["main"], dl["main"]["noise"], dl["sensitivity"]
fr, ma, facts = ex["inframe"], ex["main_crawl"], ex["facts"]
fv = fr["frame_rules"]                        # rules that apply inside frames
pd, oc, ss, cf = m["placebo_dose"], m["order_check"], m["structure_split"], m["table1_counterfactual"]
sel = m["selector_table_paired"]
rr = {r["rule"]: r for r in fn["rq1_rules"]["rules"]}
og = {o["org"]: o for o in fn["frame_orgs"]["orgs"]}
tl = {r["t"]: r for r in fn["delta_tails"]["ads_minus_blocked"]}
ti = {r["t"]: r for r in fn["delta_tails"]["identical_visits"]}
visits_per_site = [int(r["visits"]) for r in csv.DictReader(open(RES / "rq1-per-site.csv"))]
vendors = json.loads((ROOT / "data/ad-vendors.json").read_text())["vendors"]
prov = json.loads((ROOT / "data/sites-crux-news.provenance.json").read_text())
lock = json.loads((ROOT / "package-lock.json").read_text())["packages"]
harness_main = (ROOT / "harness/crawl-main.js").read_text()
harness_inframe = (ROOT / "harness/crawl-inframe.js").read_text()
failures = []


def check(label, got, want, tol=0.05):
    """Numeric check: |got - want| <= tol (default: equal at one decimal)."""
    ok = got is not None and abs(got - want) <= tol
    print(f"  [{'ok' if ok else 'FAIL'}] {label}: got {got}, paper {want}")
    if not ok:
        failures.append(label)


def holds(label, cond):
    """A qualitative claim of the paper, as a condition on the results."""
    print(f"  [{'ok' if cond else 'FAIL'}] {label}")
    if not cond:
        failures.append(label)


def in_both(rx):
    """A protocol constant, found in both crawlers' source."""
    return bool(re.search(rx, harness_main)) and bool(re.search(rx, harness_inframe))


names = {"link-name", "image-alt", "frame-title", "aria-command-name", "button-name", "role-img-alt",
         "input-image-alt", "svg-img-alt", "aria-input-field-name", "aria-toggle-field-name", "label",
         "select-name", "object-alt", "area-alt", "input-button-name", "summary-name", "aria-meter-name"}

print("Abstract, Introduction, Conclusion")
check("sites in the study", m["rq1_any_visit"][3], 2298)
check("a rule that did not fail at load fails after a minute of use, % of sites", m["rq1_new_rule_any"][0], 40.7)
holds("... on half of these only inside frames (45-55%)", 45 <= m["new_rule_frames"]["frames_only_pct"] <= 55)
check("Δ>0 exceeds Δ<0 after correcting for visit order, low end (pp)", round(nz["excess_minus_aa"]), 30, tol=0)
check("... high end, blocked visit first (pp)", round(oc["reverse"]["net"]), 33, tol=0)
holds("the excess vanishes on sites that load no listed vendor (|net| <= 2 pp)", abs(pd["0"]["net"]) <= 2)
holds("the excess grows with the number of listed vendors",
      [pd[k]["net"] for k in ("0", "1-2", "3-5", "6-10", ">10")]
      == sorted(pd[k]["net"] for k in ("0", "1-2", "3-5", "6-10", ">10")))
holds("selector matching agrees with blocking on naming rules (within 8 pp of both orders)",
      max(abs(sel["family"]["naming"]["ad"] - cf[o]["family"]["naming"]) for o in ("forward", "reverse")) <= 8)
holds("... but not on structure rules (ad-token share far below both orders)",
      sel["family"]["layout"]["ad"] < min(cf[o]["family"]["layout"] for o in ("forward", "reverse")) / 2)
check("a visible cross-origin frame fails a WCAG rule, % of sites", fv["any_visible"]["pct"], 36.0)
check("every included visit fails at load, % of sites", m["baseline_facts"]["sites_failing_at_load_every_visit_pct"], 99.2)
check("of the sites with a failing frame, % where a listed vendor serves it", round(fv["ad_sites_pct_of_any"]), 60, tol=0)

print("Fig. 1 (pipeline)")
check("sampled sites", sum(p["sites"] for p in facts["stratum_popularity"].values()), 2500)
check("listed vendors", len(vendors), 59)
check("block-list patterns", sum(len(v["patterns"]) for v in vendors), 121)
holds("main crawl ran 8-11 Oct 2026", m["crawl_dates"] == ["2026-10-08", "2026-10-11"])
holds("in-frame crawl ran 12-20 Oct 2026", fr["crawl_dates"] == ["2026-10-12", "2026-10-20"])
check("main-crawl records analysed (sessions)", m["records"]["normal"] + m["records"]["blocked"], 9848)
check("in-frame visits", fr["records"], 2460)

print("Study Design: sites")
check("Playwright major.minor", float(".".join(lock["node_modules/playwright"]["version"].split(".")[:2])), 1.63, tol=0)
check("axe-core major.minor", float(".".join(lock["node_modules/axe-core"]["version"].split(".")[:2])), 4.13, tol=0)
check("@axe-core/playwright major.minor",
      float(".".join(lock["node_modules/@axe-core/playwright"]["version"].split(".")[:2])), 4.13, tol=0)
check("Chromium major version", int(facts["chromium"].split(".")[0]), 153, tol=0)
check("CrUX top-1M", prov["cruxTopUsed"], 1000000, tol=0)
sp = facts["stratum_popularity"]
check("US or generic quota", sp["us-en"]["sites"], 1250, tol=0)
check("European quota", sp["eu"]["sites"], 625, tol=0)
check("rest-of-world quota", sp["row"]["sites"], 625, tol=0)
check("platform and government domains excluded", m["sample"]["platform_domains"], 35, tol=0)
check("sites left", m["sample"]["sites_analysed"], 2465, tol=0)
check("... none of them analysed", m["sample"]["platform_domains_included"], 0, tol=0)
kappas = [v["kappa"] for k, v in ag["three_category"].items() if k != "fleiss"]
check("pairwise Cohen's κ, largest (paper: κ ≤ 0.27)", round(max(kappas), 2), 0.27, tol=0.001)
check("pairwise Cohen's κ, largest (unrounded)", max(kappas), 0.265, tol=0.0005)
check("κ between the two rule annotators on all 2,465 sites", ag["full_AC"]["kappa"], 0.008, tol=0.0005)
check("... N", ag["full_AC"]["n"], 2465, tol=0)
np_ = ex["news_precision"]
check("hand-labelled sites", np_["labelled"], 50, tol=0)
check("news publishers among them", np_["news"], 41, tol=0)
check("... %", np_["strict"]["pct"], 82.0)
check("... CI low", np_["strict"]["lo"], 69.2)
check("... CI high", np_["strict"]["hi"], 90.2)

print("Study Design: audit, exercise, measures (protocol constants in harness/)")
holds("1366x900 viewport", in_both(r"viewport: \{ width: 1366, height: 900 \}"))
holds("baseline audit 7 s after load", in_both(r"waitForLoadState\('load'.*\n\s*await sleep\(7000\)"))
holds("scroll steps of 540 px (0.6 x 900), up to 20, 0.9 s pause",
      in_both(r"const vh = 900;\n\s*for \(let y = 0; y < 20; y\+\+\) \{\n\s*try \{ await page\.mouse\.wheel\(0, Math\.round\(vh \* 0\.6\)\)")
      and in_both(r"interactionLog\.scrolls\+\+;\n\s*await sleep\(900\)"))
holds("25 s wait", in_both(r"argv\.dwell \|\| '25000'"))
holds("40 Tab presses", in_both(r"argv\['tab-stops'\] \|\| '40'"))
holds("hover over up to 15 ad-like elements", in_both(r"return out\.slice\(0, 15\);"))
holds("up to ten more Tab presses per new overlay", in_both(r"argv\['close-reach-stops'\] \|\| '10'"))
holds("2.5 s pause before the post audit", in_both(r"phase\.current = 'post_settle';\n\s*await sleep\(2500\)"))
holds("selectors: hash-like tokens of four or more hex digits normalised", in_both(r"\[0-9a-f\]\{4,\}"))
check("new nodes that look re-rendered, %", sens["churn_filtered"]["share_looking_rerendered"], 42.7)
check("sites with two included visits", visits_per_site.count(2), 2006, tol=0)
holds("no site has more than two included visits", max(visits_per_site) == 2)

print("Study Design: with and without ads")
check("services on the block list that draw their own interface", len(sens["without_non_ad_ui_vendors"]["vendors"]), 6, tol=0)
holds("rounds analysed: 1 and 3 (two per arm)", m["rounds_analysed"] == [1, 3])
check("pairs", m["pair_timing"]["pairs"], 4113, tol=0)
check("pairs with the blocked visit first", m["pair_timing"]["blocked_first"], 3, tol=0)
check("median hours from normal to blocked visit", m["pair_timing"]["median_h"], 15.3)

print("Study Design: inside frames, ethics")
wcag_top_doc = {"aria-hidden-body", "document-title", "html-has-lang", "meta-viewport"}
check("top-document-only WCAG rules excluded", len(wcag_top_doc & set(fr["initiator_only_rules"])), 4, tol=0)
holds("in-frame audit runs WCAG A and AA rules",
      "runOnly: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']" in harness_inframe)
holds("visible frame: body > 2,500 px²", "visible: r.bw * r.bh > 2500" in harness_inframe)
holds("5 s per frame, at most 25 frames",
      "OOPIF_PER_FRAME_MS = 5000" in harness_inframe and "OOPIF_MAX_FRAMES = 25" in harness_inframe)
check("sites at the 25-frame cap", fr["frame_cap_reached"], 461, tol=0)
check("tracker requests to listed vendors per site, mean", round(m["tracker_cost"]["mean"], 1), 12.5)
check("... median", m["tracker_cost"]["median"], 0, tol=0)

print("Results: Fig. 2 (sample flow)")
ex_n = m["exclusions_normal"]
check("normal-arm visits included, %", round(100 * ex_n["included"] / m["records"]["normal"]), 87, tol=0)
check("normal-arm visits included", m["included_visits"]["normal"], 4304, tol=0)
check("normal-arm records", m["records"]["normal"], 4927, tol=0)
check("blocked-arm visits included", m["included_visits"]["blocked"], 4400, tol=0)
check("blocked-arm records", m["records"]["blocked"], 4921, tol=0)
check("RQ2 paired sites", m["sites"], 2254, tol=0)
check("RQ2 sites with two identical visits", nz["sites"], 1980, tol=0)
check("RQ2 reversed pairs", oc["reverse_pairs"], 1996, tol=0)
check("RQ3 sites", fr["sites"], 2273, tol=0)

print("RQ1 (text, Figs. 3-4, finding box)")
bf = m["baseline_facts"]
check("median failing nodes per visit at load", bf["median_baseline_nodes"], 61, tol=0)
pf = m["pass_then_fail"]["wcag"]
check("sites with no WCAG failure at load that fail a WCAG rule after", pf["sites_fail_later"], 24, tol=0)
check("... %", pf["sites_fail_later_pct"], 1.0)
check("at least one new violation, % of sites", m["rq1_any_visit"][0], 66.8)
check("... without nodes that look re-rendered", sens["churn_filtered"]["rq1_any_visit"][0], 59.8)
check("newly failing rule, CI low", m["rq1_new_rule_any"][1], 38.7)
check("newly failing rule, CI high", m["rq1_new_rule_any"][2], 42.8)
check("newly failing WCAG rule, %", m["rq1_new_rule_wcag"][0], 37.6)
check("newly failing rule on every visit, %", m["rq1_new_rule_majority"][0], 24.4)
check("new violation on a majority of visits, % (Fig. 3)", m["rq1_majority"][0], 57.1)
check("sites where newly failing rules fail only inside frames (Fig. 3)", m["new_rule_frames"]["sites_frames_only"], 486, tol=0)
check("frame-title newly fails, %", rr["frame-title"]["pct"], 12.7)
check("aria-command-name newly fails, %", rr["aria-command-name"]["pct"], 12.2)
check("image-alt newly fails, %", rr["image-alt"]["pct"], 10.3)
holds("these three are WCAG rules about names, mostly failing inside frames",
      all(rr[r]["wcag"] and r in names and rr[r]["frames_only_sites"] > rr[r]["sites"] / 2
          for r in ("frame-title", "aria-command-name", "image-alt")))
check("region newly fails, %", fn["rq1_rules"]["region"]["pct"], 1.5)
holds("region adds many new nodes (more than 30% of all new nodes)",
      m["new_nodes_normal"]["region"] > 0.3 * m["new_nodes_normal"]["total"])
rec = [r["recur_pct"] for r in fn["rq1_rules"]["rules"][:8] if r["wcag"]]
check("WCAG rules in Fig. 4: newly fail on both visits, lowest %", round(min(rec)), 18, tol=0)
check("... highest %", round(max(rec)), 41, tol=0)
check("Fig. 4 shows the sites of the study", fn["rq1_rules"]["sites"], 2298, tol=0)

print("RQ2 (text, Figs. 5-7, Table I, finding box)")
check("Δ>0, %", m["shares"]["pos"], 50.5)
check("Δ>0, CI low", m["pos_ci"][1], 48.4)
check("Δ>0, CI high", m["pos_ci"][2], 52.5)
check("Δ<0, %", m["shares"]["neg"], 9.2)
check("Hodges-Lehmann estimate", m["hl"], 3.5)
check("... CI low", m["hl_ci"][0], 3.0)
check("... CI high", m["hl_ci"][1], 4.0)
variants = {"WCAG rules only": m["wcag_only"]["shares"], "no re-rendered-looking nodes": sens["churn_filtered"]["shares"],
            "same scroll distance": m["scroll_check"]["equal_scroll_delta"], "all violating nodes": m["net_count"]["shares"]}
pos, neg = [v["pos"] for v in variants.values()], [v["neg"] for v in variants.values()]
check("variants: Δ>0 lowest %", round(min(pos)), 44, tol=0)
check("variants: Δ>0 highest %", round(max(pos)), 50, tol=0)
check("variants: Δ<0 lowest %", round(min(neg)), 7, tol=0)
check("variants: Δ<0 highest %", round(max(neg)), 16, tol=0)
holds("in every variant, Δ>0 is more common than Δ<0", all(p > n for p, n in zip(pos, neg)))
check("identical visits differ, % of sites", nz["aa_disagree"], 52.8)
check("identical visits: earlier higher minus lower (pp)", nz["aa_net"], 7.1)
holds("... the earlier visit is the higher one more often", nz["aa"]["pos"] > nz["aa"]["neg"])
check("pairs on the same sites: Δ>0 minus Δ<0 (pp)", nz["ab_net"], 37.0)
check("difference after subtracting chance and order (pp)", nz["excess_minus_aa"], 29.9)
check("... CI low", nz["excess_minus_aa_ci"][0], 26.2)
check("... CI high", nz["excess_minus_aa_ci"][1], 33.7)
check("blocked visit first: Δ>0 minus Δ<0 (pp)", oc["reverse"]["net"], 32.8)
check("with ads: >= 10 more new violating nodes, %", tl[10]["up"]["pct"], 24.0)
check("with ads: >= 10 fewer, %", tl[10]["down"]["pct"], 1.8)
check("identical visits: >= 10 more, %", ti[10]["up"]["pct"], 14.3)
check("identical visits: >= 10 fewer, %", ti[10]["down"]["pct"], 7.7)
holds("by chance large changes go both ways; with ads they almost always go up",
      ti[10]["down"]["pct"] >= 5 and tl[10]["up"]["pct"] / (tl[10]["up"]["pct"] + tl[10]["down"]["pct"]) >= 0.9)
check("Fig. 5 sites", fn["delta_tails"]["sites"], 1980, tol=0)
check("placebo: sites with no listed vendor", pd["0"]["n"], 255, tol=0)
check("placebo: Δ>0 %", pd["0"]["pos"], 10.2)
check("placebo: Δ<0 %", pd["0"]["neg"], 11.0)
for b, net in [("0", -0.8), ("1-2", 12.5), ("3-5", 22.3), ("6-10", 40.6), (">10", 68.1)]:
    check(f"vendor dose {b}: net (pp, Fig. 6)", pd[b]["net"], net)
na = sens["without_non_ad_ui_vendors"]
check("sites that load none of the six non-ad services", na["sites"], 1267, tol=0)
check("... Δ>0 %", na["shares"]["pos"], 45.1)
check("... Δ<0 %", na["shares"]["neg"], 9.8)
check("Table I paired visits", sel["visits"], 4113, tol=0)
for rule, ad, sp_, fwd, rev in [("role-img-alt", 83.1, 83.1, 94.5, 92.9), ("frame-title", 70.3, 70.3, 87.0, 80.4),
                                ("aria-command-name", 74.4, 74.4, 89.5, 86.1), ("image-alt", 61.8, 62.1, 67.8, 55.2),
                                ("link-name", 53.7, 55.4, 56.7, 57.6), ("button-name", 17.5, 17.5, 18.2, 7.5),
                                ("region", 12.7, 40.2, 43.8, 39.3), ("color-contrast", 5.3, 33.0, 33.7, 23.4)]:
    check(f"Table I {rule}: Ad", sel["per_rule"][rule]["ad"], ad)
    check(f"Table I {rule}: +Sp.", sel["per_rule"][rule]["ad_or_sponsored"], sp_)
    check(f"Table I {rule}: N first", cf["forward"]["per_rule"][rule], fwd)
    check(f"Table I {rule}: B first", cf["reverse"]["per_rule"][rule], rev)
for fam, ad, sp_, fwd, rev in [("naming", 59.5, 60.1, 67.1, 59.7), ("layout", 11.0, 37.6, 40.1, 34.3)]:
    label = "structure" if fam == "layout" else fam
    check(f"Table I {label} family: Ad", sel["family"][fam]["ad"], ad)
    check(f"Table I {label} family: +Sp.", sel["family"][fam]["ad_or_sponsored"], sp_)
    check(f"Table I {label} family: N first", cf["forward"]["family"][fam], fwd)
    check(f"Table I {label} family: B first", cf["reverse"]["family"][fam], rev)
sys.path.insert(0, str(ROOT / "analysis"))
from common import NAMING_RULES, LAYOUT_RULES  # noqa: E402  (stdlib-only module)
check("naming rules", len(NAMING_RULES), 17, tol=0)
check("structure rules", len(LAYOUT_RULES), 23, tol=0)
conc = m["table1_concentration"]["layout"]
check("share of the structure effect from the top 5% of sites", conc["top5"]["excess_share"], 91.7)
check("... 5% of sites", round(100 * conc["top5"]["sites"] / conc["sites"]), 5, tol=0)
check("... with native-ad widgets, % (two thirds)", conc["top5"]["native_pct"], 65.5)
check("top 5%: blocking, normal visit first (about 70%)", ss["top5"]["cf_normal_first"], 70.8)
check("top 5%: blocking, blocked visit first (about 70%)", ss["top5"]["cf_blocked_first"], 69.8)
check("top 5%: ad-token pattern", ss["top5"]["selector_ad"], 11.7)
check("top 5%: + sponsored (Fig. 7)", ss["top5"]["selector_ad_or_sponsored"], 59.2)
check("other 95%: ad-token pattern", ss["rest"]["selector_ad"], 10.3)
holds("other 95%: blocking finds almost no ad effect (below the identical-visit level)",
      max(ss["rest"]["cf_normal_first"], ss["rest"]["cf_blocked_first"]) <= cf["identical_visits"]["family"]["layout"])
holds("the ad-token pattern finds about 10% on both groups",
      abs(ss["top5"]["selector_ad"] - 10) <= 2 and abs(ss["rest"]["selector_ad"] - 10) <= 1)

print("RQ3 (text, Figs. 8-9, finding box)")
check("violating nodes inside frames after the exercise, %", ma["in_frame_pct"], 13.7)
check("... of which in frames that look like ads, % (two thirds)", ma["ad_looking_pct_of_in_frame"], 65.5)
check("sites with a failing visible cross-origin frame", fv["any_visible"]["k"], 818, tol=0)
check("... of the in-frame sites", fv["any_visible"]["n"], 2273, tol=0)
check("... CI low", fv["any_visible"]["lo"], 34.0)
check("... CI high", fv["any_visible"]["hi"], 38.0)
check("from a listed vendor, % of sites", fv["ad_vendor"]["pct"], 21.6)
check("from another host, % of sites", fv["other"]["pct"], 20.9)
alt = [fv["stricter_thresholds"]["10000"]["pct"], fv["stricter_thresholds"]["50000"]["pct"],
       fr["any_visible_by_viewport"]["pct"]]
check("stricter visible-frame definitions, lowest %", min(alt), 33.8)
check("... highest %", max(alt), 37.9)
holds("most in-frame failures are missing names",
      sum(r["nodes"] for r in fv["per_rule"] if r["rule"] in names) > fv["visible_nodes"] / 2)
holds("the most common rules are link-name and image-alt",
      [r["rule"] for r in fv["per_rule"][:2]] == ["link-name", "image-alt"])
check("in-frame nodes as % of the main audit's nodes on the same visits (about 3%)",
      round(100 * fv["visible_nodes"] / fr["main_audit_post_nodes"]), 3, tol=0)
check("Google's ad frames: share of in-frame violating nodes, %", fv["google_pct_of_nodes"], 43.5)
check("sites requesting Google's ad and tag hosts, %", ma["google_gpt_doubleclick_sites"]["pct"], 84.3)
check("Google ad frames: sites that show them", og["Google GPT/DoubleClick"]["reach"], 492, tol=0)
check("... fail on, %", round(og["Google GPT/DoubleClick"]["failing"]["pct"]), 90, tol=0)
check("YouTube embeds fail on, %", round(og["youtube.com"]["failing"]["pct"]), 94, tol=0)
check("Piano frames fail on, %", round(og["Piano"]["failing"]["pct"]), 29, tol=0)
check("Google IMA frames fail on, %", round(og["Google IMA SDK (ads in video)"]["failing"]["pct"]), 9, tol=0)
holds("youtube.com, google.com and 2mdn.net are hosts not on the list",
      all(h in og and not og[h]["listed"] for h in ("youtube.com", "google.com", "2mdn.net")))
check("Fig. 9: hosts on at least this many sites", fn["frame_orgs"]["min_reach"], 30, tol=0)
check("Fig. 9: of this many sites", fn["frame_orgs"]["sites"], 2273, tol=0)
for rule, nodes in [("link-name", 2449), ("image-alt", 1754), ("color-contrast", 1048), ("frame-title", 948),
                    ("aria-command-name", 568)]:
    check(f"Fig. 8: {rule} nodes in visible frames", {r["rule"]: r for r in fv["per_rule"]}[rule]["nodes"], nodes, tol=0)

print("Discussion")
check("a visible vendor frame fails a WCAG rule, % of sites", fv["ad_vendor"]["pct"], 21.6)

print("Threats to Validity")
g = m["google_ad_frame_in_inventory"]
check("Google ad frame on blocked visits, %", g["blocked"][0], 2.8)
check("... on normal visits, %", g["normal"][0], 58.7)
holds("the hover step runs more often with ads",
      m["hovers_per_visit"]["normal"] > m["hovers_per_visit"]["blocked"])
td = m["time_of_day"]
holds("normal visits mostly at night (00-06 PDT)", td["normal"]["h00_06_pct"] > 50)
holds("blocked visits mostly after noon (PDT)", td["blocked"]["h12_24_pct"] > 50)
holds("identical visits are further apart than a pair", m["aa_timing"]["median_h"] > m["pair_timing"]["median_h"])
check("top 5%, other visit order (blocked first)", ss["top5"]["cf_blocked_first"], 69.8)
check("top 5%, normal visit first", ss["top5"]["cf_normal_first"], 70.8)
check("identical visits: naming share assigned to ads, %", cf["identical_visits"]["family"]["naming"], 25.0)
check("identical visits: structure share assigned to ads, %", cf["identical_visits"]["family"]["layout"], 7.4)
holds("... well below the estimates",
      min(cf[o]["family"]["naming"] for o in ("forward", "reverse")) > 2 * cf["identical_visits"]["family"]["naming"]
      and min(cf[o]["family"]["layout"] for o in ("forward", "reverse")) > 4 * cf["identical_visits"]["family"]["layout"])
check("normal-arm visits removed by the inclusion rule, %",
      round(100 - 100 * ex_n["included"] / m["records"]["normal"]), 13, tol=0)
rg = m["by_region"]
check("new violation, US or generic sites, %", rg["us-en"]["any_new"][0], 74.6)
check("new violation, European sites, %", rg["eu"]["any_new"][0], 45.8)
check("Δ>0, US or generic sites, %", rg["us-en"]["delta_pos"], 58.6)
check("Δ>0, European sites, %", rg["eu"]["delta_pos"], 34.2)
holds("about one in five sampled sites is not a news publisher (15-25%)",
      15 <= 100 - np_["strict"]["pct"] <= 25)

if failures:
    print(f"\n{len(failures)} mismatch(es):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("\nall numbers in the paper reproduce")

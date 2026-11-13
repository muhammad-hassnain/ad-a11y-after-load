// crawl-main.js — the crawler of the main crawl: normal and blocked arm,
// three rounds each (scripts/crawl_main.sh). The in-frame crawl used
// harness/crawl-inframe.js, which adds an audit inside each cross-origin
// frame.
//
// For each (host, visit, arm):
//   1) Launch Chromium (recycled every N=200 contexts), inject
//      instrumentation + TCF v2.2 consent stub + axe-core.
//   2) In arm=blocked, install CDP Network.setBlockedURLs with all
//      hostname and path patterns from data/ad-vendors.json.
//   3) Navigate, settle 7 s. Baseline-time gating probe (bot and consent
//      walls).
//   4) Baseline axe + inventory.
//   5) Exercise: wheel-scroll (not JS scrollBy, so wheel listeners
//      fire); dwell; tab; hover; close-reachability probe (non-mutating,
//      i18n-aware).
//   6) Post axe + inventory; harvest instrumentation from all
//      same-origin frames (not just top).
//   7) Diff + attribution. JSONL ledger entry.
//
// Each visit writes <out>/per-site/<host>__v<visit>.json.

import { chromium } from 'playwright';
import { AxeBuilder } from '@axe-core/playwright';
import { readFileSync, writeFileSync, appendFileSync, mkdirSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { INSTRUMENT_SRC } from './instrument.js';
import { vendorFor } from './attribution.js';
import pLimit from 'p-limit';

const argv = Object.fromEntries(process.argv.slice(2).flatMap((v, i, a) => v.startsWith('--') ? [[v.slice(2), a[i + 1] && !a[i + 1].startsWith('--') ? a[i + 1] : 'true']] : []));

const SITES_FILE = argv.sites || 'data/sites-crux-news.txt';
const OUT_DIR = argv.out || 'crawl/run';
const CONC = parseInt(argv.concurrency || '3', 10);
const MAX_SITES = parseInt(argv['max-sites'] || '0', 10) || 0;
const PER_SITE_TIMEOUT = parseInt(argv.timeout || '150000', 10);
const DWELL_MS = parseInt(argv.dwell || '25000', 10);
const TAB_STOPS = parseInt(argv['tab-stops'] || '40', 10);
const HEADFUL = argv.headful === 'true';
const VISITS = parseInt(argv.visits || '1', 10);
// --visit N : run only visit N (not 1..N). The drivers run the crawler
// once per arm and round, so that visits of the same host are hours apart.
const VISIT_ONLY = argv.visit ? parseInt(argv.visit, 10) : null;
const ARM = String(argv.arm || 'normal').toLowerCase();
const CLOSE_REACH_STOPS = parseInt(argv['close-reach-stops'] || '10', 10);
const BROWSER_RECYCLE_EVERY = parseInt(argv['recycle-every'] || '200', 10);
const RETRY_NAV = parseInt(argv['retry-nav'] || '1', 10);
const REQUESTS_CAP = parseInt(argv['requests-cap'] || '5000', 10);

if (!['normal', 'blocked'].includes(ARM)) throw new Error(`--arm must be normal|blocked`);

mkdirSync(OUT_DIR, { recursive: true });
mkdirSync(resolve(OUT_DIR, 'per-site'), { recursive: true });
const LEDGER = resolve(OUT_DIR, 'crawl.jsonl');

const sites = readFileSync(SITES_FILE, 'utf8').split('\n').map(l => l.trim()).filter(l => l && !l.startsWith('#'));
const todo = MAX_SITES ? sites.slice(0, MAX_SITES) : sites;

// ---- Compile ad-vendors.json into hostname + path pattern lists for
// the blocked arm (CDP Network.setBlockedURLs supports URL patterns
// with wildcards and short-circuits in the browser process, so blocking
// adds no per-request work in the page).
let BLOCK_PATTERNS = null;
if (ARM === 'blocked') {
  const vj = JSON.parse(readFileSync(resolve('data/ad-vendors.json'), 'utf8'));
  const patterns = [];
  for (const v of vj.vendors) {
    for (const p of v.patterns) {
      if (p.includes('/')) {
        // path pattern: match host+path containing substring, both schemes
        patterns.push(`*://*${p}*`);
      } else {
        patterns.push(`*://${p}/*`);
        patterns.push(`*://*.${p}/*`);
      }
    }
  }
  BLOCK_PATTERNS = patterns;
  console.log(`[arm=blocked] ${patterns.length} URL patterns (hostname + path, via CDP Network.setBlockedURLs)`);
}

console.log(`[crawl] ${todo.length} sites × ${VISITS} visits, concurrency ${CONC}, arm=${ARM}, out ${OUT_DIR}`);

const sleep = ms => new Promise(r => setTimeout(r, ms));

// Always tag per-site files with the visit number, so that the rounds of
// one arm can share one OUT_DIR.
function outFileFor(host, visit, _ignored) {
  const safe = host.replace(/[^a-z0-9.-]/gi, '_');
  return resolve(OUT_DIR, 'per-site', `${safe}__v${visit}.json`);
}

// ---- Per-TLD Accept-Language. Minimal; falls back to en-US.
function acceptLanguageFor(host) {
  const map = {
    'fr': 'fr-FR,fr;q=0.9,en;q=0.5',
    'de': 'de-DE,de;q=0.9,en;q=0.5',
    'at': 'de-AT,de;q=0.9,en;q=0.5',
    'ch': 'de-CH,de;q=0.9,fr;q=0.5,en;q=0.3',
    'it': 'it-IT,it;q=0.9,en;q=0.5',
    'es': 'es-ES,es;q=0.9,en;q=0.5',
    'pt': 'pt-PT,pt;q=0.9,en;q=0.5',
    'br': 'pt-BR,pt;q=0.9,en;q=0.5',
    'nl': 'nl-NL,nl;q=0.9,en;q=0.5',
    'be': 'nl-BE,fr-BE;q=0.9,en;q=0.5',
    'se': 'sv-SE,sv;q=0.9,en;q=0.5',
    'no': 'nb-NO,no;q=0.9,en;q=0.5',
    'dk': 'da-DK,da;q=0.9,en;q=0.5',
    'fi': 'fi-FI,fi;q=0.9,en;q=0.5',
    'pl': 'pl-PL,pl;q=0.9,en;q=0.5',
    'ru': 'ru-RU,ru;q=0.9,en;q=0.5',
    'ua': 'uk-UA,uk;q=0.9,ru;q=0.5,en;q=0.3',
    'tr': 'tr-TR,tr;q=0.9,en;q=0.5',
    'jp': 'ja-JP,ja;q=0.9,en;q=0.5',
    'kr': 'ko-KR,ko;q=0.9,en;q=0.5',
    'cn': 'zh-CN,zh;q=0.9,en;q=0.5',
    'tw': 'zh-TW,zh;q=0.9,en;q=0.5',
    'hk': 'zh-HK,zh;q=0.9,en;q=0.5',
    'th': 'th-TH,th;q=0.9,en;q=0.5',
    'id': 'id-ID,id;q=0.9,en;q=0.5',
    'ar': 'ar-SA,ar;q=0.9,en;q=0.5',
    'sa': 'ar-SA,ar;q=0.9,en;q=0.5',
    'in': 'en-IN,hi;q=0.9,en;q=0.5',
    'au': 'en-AU,en;q=0.9',
    'nz': 'en-NZ,en;q=0.9',
    'uk': 'en-GB,en;q=0.9',
    'ca': 'en-CA,fr-CA;q=0.9,en;q=0.5',
    'mx': 'es-MX,es;q=0.9,en;q=0.5',
    'ar': 'es-AR,es;q=0.9,en;q=0.5',
  };
  const parts = host.toLowerCase().split('.');
  // handle co.uk, com.au, com.mx, co.jp — use the first part as the hint
  const tld = parts[parts.length - 1];
  const sld = parts.length >= 3 ? parts[parts.length - 2] : null;
  if (sld && sld.length === 2 && map[sld]) return map[sld];
  return map[tld] || 'en-US,en;q=0.9';
}

// ---- TCF v2.2 + USP + GPP consent stub, injected before navigation, so
// that publishers that hold back ads until the user consents load them.
// The API reports consent to all purposes and vendors, but the hardcoded
// TC string it returns encodes consent to purposes 1-10 and to no vendor
// (paper, Consent and ethics). It is a static string, not user data.
const TCF_STUB_SRC = `
(() => {
  if (window.__tcfapiStub) return;
  window.__tcfapiStub = true;
  // Minimal in-page CMP API with a hardcoded TCString. Not for production
  // use; it only lets ad code that waits for consent run.
  const TC_STRING = 'CP5uNUAP5uNUAAfdBBDEBMCsAP_AAEPAACiQAAATAACAAAAAAAAAAAAAAgAAAAAAAAAAAAAAAAAAEoABIAEAAEAAIABEgAAAIAEIAAEAAEgAAEEgAAAgAEAAA';
  window.__tcfapi = function(cmd, version, callback, parameter) {
    try {
      if (cmd === 'addEventListener' || cmd === 'getTCData') {
        return callback && callback({
          tcString: TC_STRING, cmpId: 999, cmpVersion: 1, tcfPolicyVersion: 4,
          cmpStatus: 'loaded', eventStatus: 'tcloaded', gdprApplies: true,
          purpose: { consents: { 1:true,2:true,3:true,4:true,5:true,6:true,7:true,8:true,9:true,10:true }, legitimateInterests: {} },
          vendor: { consents: new Proxy({}, { get: () => true }), legitimateInterests: new Proxy({}, { get: () => true }) },
          publisher: { consents: {1:true,2:true,3:true,4:true,5:true,6:true,7:true,8:true,9:true,10:true}, legitimateInterests: {}, customPurpose: {} }
        }, true);
      }
      if (cmd === 'removeEventListener') return callback && callback(true, true);
      if (cmd === 'ping') return callback && callback({ gdprApplies: true, cmpLoaded: true, cmpStatus: 'loaded', displayStatus: 'hidden', apiVersion: '2.2', cmpVersion: 1, cmpId: 999, tcfPolicyVersion: 4 }, true);
      return callback && callback(null, false);
    } catch { return callback && callback(null, false); }
  };
  // US CCPA
  window.__uspapi = function(cmd, version, callback) {
    try {
      if (cmd === 'getUSPData') return callback && callback({ version: 1, uspString: '1---' }, true);
      return callback && callback(null, false);
    } catch { return callback && callback(null, false); }
  };
  // GPP (Global Privacy Platform) tiny stub — many CMPs probe this now
  window.__gpp = function(cmd, callback) {
    try { if (callback) callback({ pingData: { cmpStatus: 'loaded', signalStatus: 'ready' } }, true); } catch {}
  };
})();
`;

// ---- Close-control tokens for the overlay close-reach probe, in several
// languages, so that non-English overlays are recognised.
const CLOSE_TOKENS_RX =
  /close|dismiss|skip|no thanks|continue to site|accept|decline|fermer|rejeter|ignorer|non merci|schließen|ablehnen|zustimmen|cerrar|rechazar|aceptar|no gracias|chiudi|rifiuta|accetta|fechar|rejeitar|aceitar|закрыть|отклонить|згода|関閉|닫기|关闭|關閉|×|✕|✖/i;

async function closeOnce(ctx) {
  if (!ctx || ctx.__closed) return;
  ctx.__closed = true;
  try { await ctx.close(); } catch {}
}

async function crawlOne(browser, host, visit, signal) {
  const url = host.startsWith('http') ? host : 'https://' + host;
  const outFile = outFileFor(host, visit);
  if (existsSync(outFile) && argv.resume) return { skipped: true };

  const ctx = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
    viewport: { width: 1366, height: 900 },
    locale: 'en-US',
    timezoneId: 'America/New_York',
    extraHTTPHeaders: { 'Accept-Language': acceptLanguageFor(host) },
  });

  // Cooperative abort: close the ctx if the orchestrator cancels us.
  if (signal) signal.addEventListener('abort', () => closeOnce(ctx), { once: true });

  await ctx.addInitScript(TCF_STUB_SRC);
  await ctx.addInitScript(INSTRUMENT_SRC);

  // ---- Blocked arm via CDP Network.setBlockedURLs. Set per-page in a
  // CDP session; browser-process short-circuit, no per-request JS cost.
  const page = await ctx.newPage();
  if (ARM === 'blocked' && BLOCK_PATTERNS) {
    try {
      const client = await page.context().newCDPSession(page);
      await client.send('Network.enable');
      await client.send('Network.setBlockedURLs', { urls: BLOCK_PATTERNS });
    } catch (e) {
      // fall back to ctx.route if CDP unavailable
      await ctx.route('**/*', route => {
        try {
          const u = new URL(route.request().url());
          const hp = u.hostname + u.pathname;
          for (const p of BLOCK_PATTERNS) {
            // naive glob to regex
            const rx = new RegExp('^' + p.replace(/[.+^${}()|[\\\\\\]\\\\]/g, '\\\\$&').replace(/\\*/g, '.*') + '$');
            if (rx.test(u.protocol + '//' + hp)) return route.abort();
          }
        } catch {}
        return route.continue();
      });
    }
  }

  page.setDefaultNavigationTimeout(55_000);
  page.setDefaultTimeout(15_000);

  const site = { host, url, visit, arm: ARM, startedAt: new Date().toISOString(), errors: [], tcf_stub: true };
  const started = Date.now();

  // Request log, capped.
  const requests = [];
  let requestsTruncated = false;
  const phase = { current: 'init' };
  ctx.on('request', req => {
    if (requests.length >= REQUESTS_CAP) { requestsTruncated = true; return; }
    try {
      const u = new URL(req.url());
      requests.push({ host: u.hostname, type: req.resourceType(), method: req.method(), phase: phase.current });
    } catch {}
  });

  const flush = async () => { writeFileSync(outFile, JSON.stringify(site, null, 2)); };
  const bail = async (reason, err) => {
    site.errors.push({ reason, msg: err && err.message || String(err) });
    await flush();
    await closeOnce(ctx);
    return { bailed: reason };
  };

  phase.current = 'nav';
  let resp = null;
  for (let attempt = 1; attempt <= RETRY_NAV + 1; attempt++) {
    try {
      resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 55_000 });
      site.status = resp && resp.status();
      site.finalUrl = page.url();
      site.navAttempts = attempt;
      break;
    } catch (e) {
      if (attempt > RETRY_NAV) return bail('nav', e);
      await sleep(10_000);
    }
  }

  phase.current = 'baseline_settle';
  try { await page.waitForLoadState('load', { timeout: 20_000 }); } catch {}
  await sleep(7000);
  // SPA hydration wait: if the body is still sparse at 7s (Next.js, React
  // sites that hydrate after `load`), poll up to another 15s. Baseline
  // axe + gating both benefit — the baseline that follows is otherwise
  // measured on an empty app shell.
  try {
    const started = Date.now();
    while (Date.now() - started < 15_000) {
      const n = await page.evaluate(() => (document.body ? document.body.getElementsByTagName('*').length : 0)).catch(() => 0);
      if (n >= 50) break;
      await sleep(1000);
    }
  } catch {}

  const gating = await detectGating(page).catch(() => ({ error: 'gating' }));
  site.gating = gating;
  await page.evaluate(g => window.__adAccessSetGating && window.__adAccessSetGating(g), gating).catch(() => {});

  const baselineAxe = await runAxe(page).catch(e => ({ error: String(e) }));
  const baselineInventory = await inventoryAds(page).catch(() => ({ error: 'inv' }));
  site.baseline = { axe: summarizeAxe(baselineAxe), inventory: baselineInventory, at: Date.now() - started };

  const interactionLog = { scrolls: 0, tabs: 0, hovers: 0 };

  // ---- (a) Scroll via page.mouse.wheel (fires wheel/pointer events,
  // so Taboola-style wheel-throttled lazy loaders also fire). Also
  // scroll inner overflow:auto containers that trap scroll.
  phase.current = 'scroll';
  try {
    const vh = 900;
    for (let y = 0; y < 20; y++) {
      try { await page.mouse.wheel(0, Math.round(vh * 0.6)); } catch {}
      interactionLog.scrolls++;
      await sleep(900);
      const atBottom = await page.evaluate(() => (window.scrollY + window.innerHeight + 10 >= document.documentElement.scrollHeight)).catch(() => true);
      if (y > 4 && atBottom) break;
    }
    // also drive inner scroll containers one time each
    await page.evaluate(() => {
      try {
        const scrollers = Array.from(document.querySelectorAll('main,section,article,div'))
          .filter(el => {
            const cs = getComputedStyle(el);
            return (cs.overflowY === 'auto' || cs.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 50;
          }).slice(0, 6);
        scrollers.forEach(s => { s.scrollTop = s.scrollHeight; });
      } catch {}
    });
  } catch (e) { site.errors.push({ reason: 'scroll', msg: String(e) }); }

  phase.current = 'dwell';
  await sleep(DWELL_MS);

  phase.current = 'tab';
  try {
    for (let i = 0; i < TAB_STOPS; i++) {
      await page.keyboard.press('Tab');
      await sleep(80);
      await page.evaluate(() => {
        const el = document.activeElement;
        if (!el) return;
        const inIframe = el.tagName === 'IFRAME';
        window.__adAccessMarkTab && window.__adAccessMarkTab({
          tag: el.tagName, role: el.getAttribute('role') || null,
          name: el.getAttribute('aria-label') || el.textContent?.slice(0, 60) || null,
          inIframe, iframeSrc: inIframe ? el.src : null, id: el.id || null
        });
      }).catch(() => {});
      interactionLog.tabs++;
    }
  } catch (e) { site.errors.push({ reason: 'tab', msg: String(e) }); }

  phase.current = 'hover';
  try {
    const adBoxes = await page.evaluate(() => {
      const AD = /(^|[-_])ad(s|slot|unit|container|wrap)?([-_]|$)|googlesyndication|doubleclick|googletag|taboola|trc_rbox|trc_related|outbrain|outbrain_container|OB_|revcontent|safeframe|sf_[0-9]|google_ads_frame|amp-ad|amp-sticky-ad|data-outbrain-id|data-widget-id/i;
      const out = [];
      document.querySelectorAll('div,section,aside,iframe,ins,amp-ad,amp-sticky-ad').forEach(el => {
        const bag = (el.id || '') + ' ' + (typeof el.className === 'string' ? el.className : '') +
          ' ' + (el.getAttribute('data-ad-slot') || '') + ' ' + (el.getAttribute('data-outbrain-id') || '') +
          ' ' + (el.getAttribute('data-widget-id') || '') + ' ' + (el.getAttribute('name') || '') +
          ' ' + (el.tagName || '');
        if (AD.test(bag)) {
          const r = el.getBoundingClientRect();
          if (r.width > 50 && r.height > 50) out.push({ x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) });
        }
      });
      return out.slice(0, 15);
    });
    for (const b of adBoxes) {
      try { await page.mouse.move(b.x, Math.max(1, Math.min(850, b.y))); await sleep(300); interactionLog.hovers++; } catch {}
    }
  } catch (e) { site.errors.push({ reason: 'hover', msg: String(e) }); }

  site.interactions = interactionLog;

  // ---- (e) Close-reach probe. For each ad-like overlay that instrument.js
  // saw inserted after DOMContentLoaded, focus its first focusable element
  // and press Tab up to CLOSE_REACH_STOPS times, looking for a close control.
  //  - Does not mutate the page (no tabindex changes leak into the post audit).
  //  - Snapshots and restores the active element.
  //  - Close tokens in several languages.
  //  - Records focusStaysInside: whether Tab stays within the overlay, which
  //    is the correct behaviour for aria-modal="true" overlays.
  phase.current = 'close_reach';
  try {
    const overlays = await page.evaluate(() => (window.__adAccessCollect?.().newOverlays || [])
      .filter(o => o.overlayId && o.visible !== false));
    for (const ov of overlays) {
      try {
        const prevActive = await page.evaluate(() => {
          const a = document.activeElement;
          return a && a.hasAttribute ? { tag: a.tagName, id: a.id || null } : null;
        });
        // Start probe by focusing on the first focusable inside the overlay if any,
        // otherwise shift focus into the viewport by pressing Tab from document root.
        await page.evaluate((oid) => {
          const ov = document.querySelector(`[data-adaccess-overlay="${oid}"]`);
          if (!ov) return;
          const first = ov.querySelector('a[href],button,[tabindex]:not([tabindex="-1"]),input,select,textarea');
          if (first && first.focus) first.focus({ preventScroll: true });
        }, ov.overlayId);

        let found = null;
        let focusEverLeft = false;
        for (let i = 1; i <= CLOSE_REACH_STOPS; i++) {
          await page.keyboard.press('Tab');
          await sleep(60);
          const hit = await page.evaluate(({ oid, re }) => {
            const el = document.activeElement;
            if (!el) return null;
            const ov = document.querySelector(`[data-adaccess-overlay="${oid}"]`);
            if (!ov) return { exists: false };
            const inside = ov.contains(el);
            if (!inside) return { exists: true, insideOverlay: false };
            const name = (el.getAttribute('aria-label') || el.getAttribute('title') || el.textContent || '').trim().slice(0, 120);
            const cls = (el.className || '').toString();
            const CLOSE = new RegExp(re, 'i');
            const looksClose = CLOSE.test(name) || /close|dismiss|modal-?close|c-dialog__close|c-modal__close|btn-?close/i.test(cls) ||
              (el.getAttribute('aria-label') || '').toLowerCase().includes('close');
            return { exists: true, insideOverlay: true, name, looksClose };
          }, { oid: ov.overlayId, re: CLOSE_TOKENS_RX.source });
          if (hit && hit.exists === false) break;
          if (hit && hit.insideOverlay === false) { focusEverLeft = true; }
          if (hit && hit.insideOverlay && hit.looksClose) { found = { stop: i, name: hit.name }; break; }
        }
        await page.evaluate(({ oid, found, focusEverLeft }) => window.__adAccessUpdateOverlay &&
          window.__adAccessUpdateOverlay(oid, {
            closeReachableInStops: found ? found.stop : null,
            closeReachable: !!found,
            closeControlName: found ? found.name : null,
            focusStaysInside: !focusEverLeft
          }), { oid: ov.overlayId, found, focusEverLeft });
        // restore focus if we had one
        if (prevActive && prevActive.id) {
          await page.evaluate((id) => { const e = document.getElementById(id); if (e && e.focus) e.focus({ preventScroll: true }); }, prevActive.id).catch(() => {});
        }
      } catch (e) { site.errors.push({ reason: 'close_reach', msg: String(e) }); }
    }
  } catch (e) { site.errors.push({ reason: 'close_reach_outer', msg: String(e) }); }

  phase.current = 'post_settle';
  await sleep(2500);

  phase.current = 'post_axe';
  const postAxe = await runAxe(page).catch(e => ({ error: String(e) }));
  const postInventory = await inventoryAds(page).catch(() => ({ error: 'inv' }));
  site.post = { axe: summarizeAxe(postAxe), inventory: postInventory };

  // ---- Multi-frame instrumentation harvest.
  // addInitScript installs the IIFE in every realm; cross-origin iframes
  // early-exit per instrument.js. For same-origin frames, we harvest
  // their __adAccess and merge arrays into the top-frame log.
  const instr = await harvestAllFrames(page);
  site.instrumentation = instr;

  const vendorHits = {};
  const vendorHitsByPhase = {};
  for (const r of requests) {
    const v = vendorFor(r.host);
    if (v) {
      vendorHits[v.vendor] = (vendorHits[v.vendor] || 0) + 1;
      const ph = r.phase || 'unknown';
      vendorHitsByPhase[ph] = (vendorHitsByPhase[ph] || 0) + 1;
    }
  }
  site.vendors = vendorHits;
  site.vendorHitsByPhase = vendorHitsByPhase;
  site.trackerRequestsFromInteraction = (vendorHitsByPhase.tab || 0) + (vendorHitsByPhase.hover || 0);
  site.requestsTruncated = requestsTruncated;

  site.diff = diff(site.baseline.axe, site.post.axe, site.baseline.inventory, site.post.inventory, site.instrumentation, requests);

  site.endedAt = new Date().toISOString();
  site.durationMs = Date.now() - started;
  await flush();
  await closeOnce(ctx);

  // JSONL ledger line
  appendFileSync(LEDGER, JSON.stringify({
    host, visit, arm: ARM, startedAt: site.startedAt, endedAt: site.endedAt,
    durationMs: site.durationMs, status: site.status, gating: gating.classification,
    navAttempts: site.navAttempts || 1, errors: site.errors.length,
    newViol: site.diff.newViolationCount, resize: site.diff.iframeResizeCount,
    realRefresh: site.diff.realRefreshCount, obscured: site.diff.focusObscuredCount
  }) + '\n');

  console.log(`[ok] ${host} v${visit} base=${site.baseline.axe.totalViolations} post=${site.post.axe.totalViolations} Δ=${site.diff.newViolationCount} resize=${site.diff.iframeResizeCount} refresh=${site.diff.realRefreshCount} obscured=${site.diff.focusObscuredCount}`);
  return { ok: true };
}

async function harvestAllFrames(page) {
  const frames = page.frames();
  const perFrame = [];
  for (const f of frames) {
    try {
      const data = await f.evaluate(() => (typeof window.__adAccessCollect === 'function') ? window.__adAccessCollect() : null);
      if (data) perFrame.push({ frameUrl: f.url(), ...data });
    } catch { /* cross-origin frame — expected */ }
  }
  if (perFrame.length === 0) return null;
  // Merge into one record (top first, then children)
  const merged = perFrame[0];
  for (let i = 1; i < perFrame.length; i++) {
    const fp = perFrame[i];
    for (const k of ['mutations','focusEvents','iframeResizes','newOverlays','lazyLoads','focusObscured','tabStops','readingOrderDisruptions','slotRenderEnded','adRefreshEvents']) {
      if (Array.isArray(fp[k])) {
        merged[k] = (merged[k] || []).concat(fp[k].map(x => ({ ...x, __frame: fp.frameOrigin || fp.frameUrl })));
      }
    }
  }
  merged.frameCount = perFrame.length;
  merged.frameUrls = perFrame.map(p => p.frameUrl);
  return merged;
}

async function runAxe(page) {
  const builder = new AxeBuilder({ page }).options({ resultTypes: ['violations'] });
  return await builder.analyze();
}

async function detectGating(page) {
  return page.evaluate(() => {
    const cmps = [
      { v: 'OneTrust',    sel: '#onetrust-banner-sdk, #onetrust-consent-sdk, .onetrust-pc-dark-filter' },
      { v: 'Didomi',      sel: '#didomi-host, #didomi-notice, .didomi-popup-container' },
      { v: 'Cookiebot',   sel: '#CybotCookiebotDialog, [id^="CybotCookiebotDialog"]' },
      { v: 'Sourcepoint', sel: '[id^="sp_message_container"], iframe[id^="sp_message_iframe"]' },
      { v: 'TrustArc',    sel: '#truste-consent-track, .trustarc-banner-container' },
      { v: 'Usercentrics',sel: '#usercentrics-root, [data-testid^="uc-"]' },
      { v: 'Quantcast',   sel: '.qc-cmp2-container, #qc-cmp2-ui' },
      { v: 'Generic-CMP', sel: '[class*="cookie-banner"], [id*="cookie-banner"], [class*="consent-banner"], [id*="consent-banner"]' },
    ];
    const cmpHits = [];
    for (const c of cmps) {
      try {
        const el = document.querySelector(c.sel);
        if (el) {
          const r = el.getBoundingClientRect();
          cmpHits.push({ vendor: c.v, visible: r.width > 50 && r.height > 50, w: Math.round(r.width), h: Math.round(r.height) });
        }
      } catch {}
    }
    const txt = (document.body?.innerText || '').slice(0, 4000).toLowerCase();
    const botSignals = [];
    if (/cloudflare|cf-chl|cf_chl_jschl|checking your browser|just a moment/i.test(txt)) botSignals.push('cloudflare_challenge');
    if (/perimeterx|px-captcha/i.test(txt)) botSignals.push('perimeterx');
    if (/please verify you are human|unusual traffic|not a robot/i.test(txt)) botSignals.push('human_verification');
    if (/access denied|forbidden|403 forbidden|error 1020/i.test(txt)) botSignals.push('access_denied');
    if (document.querySelector('iframe[src*="challenges.cloudflare.com"]')) botSignals.push('cloudflare_turnstile');
    const visibleTextLen = (document.body?.innerText || '').trim().length;
    // Structural signal — heavy SPAs often return 0 from .innerText at 7s
    // even when the DOM is substantially populated. Count descendants and
    // content containers to avoid false positives on nytimes / reuters /
    // msn-style client-rendered pages.
    const totalEls = document.body ? document.body.getElementsByTagName('*').length : 0;
    const contentContainers = document.querySelectorAll('article, main, section, [role="main"], [role="article"], .content, #content, .article, .story').length;
    const hasRealContent = totalEls > 100 || contentContainers > 0;
    const shortPage = visibleTextLen < 500 && !hasRealContent;
    const anyBot = botSignals.length > 0;
    const anyCmpVisible = cmpHits.some(c => c.visible);
    let classification = 'ok';
    if (anyBot) classification = 'bot_challenge';
    else if (shortPage && anyCmpVisible) classification = 'consent_wall_hard';
    else if (anyCmpVisible) classification = 'consent_banner_soft';
    else if (shortPage) classification = 'short_page';
    return { classification, cmpHits, botSignals, visibleTextLen, totalEls, contentContainers, shortPage, title: document.title.slice(0, 160) };
  });
}

function summarizeAxe(ax) {
  if (!ax || ax.error) return { error: ax && ax.error || 'unknown', totalViolations: 0, violations: [] };
  const vs = (ax.violations || []).map(v => ({
    id: v.id, impact: v.impact, tags: v.tags, description: v.description,
    nodes: (v.nodes || []).map(n => ({
      target: n.target,
      html: (n.html || '').slice(0, 400),
      failureSummary: (n.failureSummary || '').slice(0, 400)
    }))
  }));
  const totalViolations = vs.reduce((s, v) => s + v.nodes.length, 0);
  const perRule = {};
  for (const v of vs) perRule[v.id] = (perRule[v.id] || 0) + v.nodes.length;
  return { violations: vs, totalViolations, perRule };
}

async function inventoryAds(page) {
  return page.evaluate(() => {
    const AD = /(^|[-_])ad(s|slot|unit|container|wrap)?([-_]|$)|googlesyndication|doubleclick|googletag|taboola|trc_rbox|trc_related|outbrain|outbrain_container|OB_|revcontent|criteo|adnxs|pubmatic|openx|rubicon|magnite|yieldmo|teads|connatix|primis|smartadserver|triplelift|sharethrough|adform|appnexus|moatads|adsafe|doubleverify|amazon-adsystem|media\.net|safeframe|sf_[0-9]|google_ads_frame|omid|amp-ad|amp-sticky-ad|data-outbrain-id|data-widget-id/i;
    const items = [];
    document.querySelectorAll('div,section,aside,iframe,ins,amp-ad,amp-sticky-ad,amp-embed').forEach(el => {
      const bag = (el.id || '') + ' ' + (typeof el.className === 'string' ? el.className : '') +
        ' ' + (el.getAttribute('data-ad-slot') || '') + ' ' + (el.getAttribute('data-outbrain-id') || '') +
        ' ' + (el.getAttribute('data-widget-id') || '') + ' ' + (el.getAttribute('name') || '') +
        ' ' + (el.tagName || '');
      const isAmp = /^amp-/i.test(el.tagName || '');
      if (!AD.test(bag) && !(el.tagName === 'IFRAME' && el.src && AD.test(el.src)) && !isAmp) return;
      const r = el.getBoundingClientRect();
      const title = el.tagName === 'IFRAME' ? el.getAttribute('title') : null;
      items.push({
        tag: el.tagName,
        id: el.id || null,
        cls: typeof el.className === 'string' ? el.className.slice(0, 160) : null,
        src: el.getAttribute('src') || el.getAttribute('data-src') || null,
        name: el.getAttribute('name') || null,
        title,
        role: el.getAttribute('role') || null,
        ariaLabel: el.getAttribute('aria-label') || null,
        rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
        childCount: el.childElementCount,
        empty: el.childElementCount === 0 && r.width * r.height === 0
      });
    });
    return { count: items.length, items };
  });
}

function diff(baseAxe, postAxe, baseInv, postInv, instr, requests) {
  // ---- Stable new-violation signature. Canonicalise target by
  // stripping digit-and-hex-ish suffixes from id/class segments so a
  // re-fired violation on a hashed selector doesn't look "new" at every
  // visit.
  const canon = sel => {
    if (!sel) return '';
    let s = String(sel);
    // normalise id selectors: #foo_1a2b3c → #foo_HASH
    s = s.replace(/#[A-Za-z_-][\w-]*/g, m => m.replace(/[_-]?[0-9a-f]{4,}$/i, '_HASH'));
    // normalise class selectors: .ad_xyz_1a2b3c → .ad_xyz_HASH
    s = s.replace(/\.[A-Za-z_-][\w-]*/g, m => m.replace(/[_-]?[0-9a-f]{4,}$/i, '_HASH'));
    // normalise :nth-of-type/:nth-child(N) → :nth(N)
    s = s.replace(/:nth-(?:of-type|child)\(\d+\)/g, ':nth(N)');
    return s;
  };
  const sig = v => v.nodes.map(n => v.id + '|' + canon(n.target && n.target[0]));
  const baseSet = new Set((baseAxe.violations || []).flatMap(sig));
  const postSet = new Set((postAxe.violations || []).flatMap(sig));
  const newSigs = [...postSet].filter(s => !baseSet.has(s));
  const removedSigs = [...baseSet].filter(s => !postSet.has(s));

  const hosts = requests.map(r => r.host);
  const vendorsSeen = Array.from(new Set(hosts.map(h => vendorFor(h)?.vendor).filter(Boolean)));
  const perRuleNew = {};
  for (const s of newSigs) {
    const rule = s.split('|')[0];
    perRuleNew[rule] = (perRuleNew[rule] || 0) + 1;
  }

  const newOverlays = (instr && instr.newOverlays && instr.newOverlays.length) || 0;
  const focusObscuredCount = (instr && instr.focusObscured && instr.focusObscured.length) || 0;
  // iframe resize breakdown: realRefresh (correlated with iframeSrc mut
  // OR slotRenderEnded) vs wrapperReflow.
  const resizes = (instr && instr.iframeResizes) || [];
  const iframeResizeCount = resizes.length;
  const realResizeCount = resizes.filter(r => r.realRefresh).length;
  // Deduped real refresh events (GAM slotRenderEnded preferred over mut)
  const realRefreshCount = (instr && instr.adRefreshEvents && instr.adRefreshEvents.length) || 0;
  const slotRenderEndedCount = (instr && instr.slotRenderEnded && instr.slotRenderEnded.length) || 0;
  const adMutationCount = (instr && instr.mutations && instr.mutations.length) || 0;
  const readingOrderDisruptions = (instr && instr.readingOrderDisruptions && instr.readingOrderDisruptions.length) || 0;
  const headerBidding = (instr && instr.headerBidding) || null;

  const ovs = (instr && instr.newOverlays) || [];
  const overlayCloseStats = {
    total: ovs.length,
    probed: ovs.filter(o => o.closeReachable !== null).length,
    reachable: ovs.filter(o => o.closeReachable === true).length,
    unreachable: ovs.filter(o => o.closeReachable === false).length,
    focusStaysInside: ovs.filter(o => o.focusStaysInside === true).length,
    meanStopsWhenReachable: (() => {
      const xs = ovs.filter(o => o.closeReachable === true).map(o => o.closeReachableInStops);
      return xs.length ? xs.reduce((a,b)=>a+b,0) / xs.length : null;
    })()
  };

  let lazyLoaded = 0;
  try {
    const baseItems = (baseInv && baseInv.items) || [];
    const postItems = (postInv && postInv.items) || [];
    for (const b of baseItems) {
      if (!b.empty) continue;
      const match = postItems.find(p =>
        Math.abs((p.rect?.x || 0) - (b.rect?.x || 0)) < 10 &&
        Math.abs((p.rect?.y || 0) - (b.rect?.y || 0)) < 10 && !p.empty);
      if (match) lazyLoaded++;
    }
  } catch {}

  const iframeTitleMissing = {
    base: ((baseInv && baseInv.items) || []).filter(i => i.tag === 'IFRAME' && !i.title).length,
    post: ((postInv && postInv.items) || []).filter(i => i.tag === 'IFRAME' && !i.title).length
  };

  return {
    newViolationCount: newSigs.length,
    removedViolationCount: removedSigs.length,
    newViolationsPerRule: perRuleNew,
    baselineTotal: baseAxe.totalViolations,
    postTotal: postAxe.totalViolations,
    delta: postAxe.totalViolations - baseAxe.totalViolations,
    newOverlays,
    focusObscuredCount,
    iframeResizeCount,
    realResizeCount,     // resize events correlated with real refresh signals
    realRefreshCount,    // real refresh events (GAM slotRenderEnded or iframeSrc mut)
    slotRenderEndedCount,
    adMutationCount,
    readingOrderDisruptions,
    overlayCloseStats,
    lazyLoaded,
    iframeTitleMissing,
    vendorsSeen,
    headerBidding
  };
}

// ---- Orchestrator with AbortController, browser recycling, timeout
// cancellation and transient retry.
(async () => {
  let browser = await chromium.launch({ headless: !HEADFUL });
  let ctxCount = 0;

  const maybeRecycle = async () => {
    if (ctxCount >= BROWSER_RECYCLE_EVERY) {
      console.log(`[recycle] closing browser after ${ctxCount} contexts`);
      try { await browser.close(); } catch {}
      browser = await chromium.launch({ headless: !HEADFUL });
      ctxCount = 0;
    }
  };

  const visitStart = VISIT_ONLY ?? 1;
  const visitEnd = VISIT_ONLY ?? VISITS;
  for (let v = visitStart; v <= visitEnd; v++) {
    console.log(`[visit ${v}${VISIT_ONLY ? ' (single)' : `/${VISITS}`}]`);
    const limit = pLimit(CONC);
    const tasks = todo.map(h => limit(async () => {
      const outFile = outFileFor(h, v);
      if (existsSync(outFile) && argv.resume) return;
      await maybeRecycle();
      ctxCount++;
      const ac = new AbortController();
      let timer;
      const timeoutP = new Promise((_, rej) => { timer = setTimeout(() => { ac.abort(); rej(new Error('site_timeout')); }, PER_SITE_TIMEOUT); });
      try {
        await Promise.race([
          crawlOne(browser, h, v, ac.signal).catch(e => {
            if (e.message !== 'site_timeout') console.log(`[err] ${h} v${v} ${e.message}`);
          }),
          timeoutP.catch(() => { console.log(`[timeout] ${h} v${v}`); })
        ]);
      } finally {
        clearTimeout(timer);
        ac.abort();  // ensure ctx is closed if crawlOne still has it
      }
    }));
    await Promise.all(tasks);
  }
  try { await browser.close(); } catch {}
  console.log('[done]');
})();

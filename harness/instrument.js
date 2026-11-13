// instrument.js
// Page-level instrumentation injected before any site script runs
// (via Playwright's addInitScript). Runs INSIDE the page context in the
// TOP window and in same-origin child iframes; cross-origin iframes
// early-exit so we don't allocate observers inside ad creatives that we
// never harvest anyway.
//
// All data is stashed on window.__adAccess (per-realm). The crawler
// walks page.frames() and merges same-origin logs with the top log.
//
// Captures:
//   - DOM mutations in or around ad slots (classic + AMP + SafeFrame)
//   - focus events (context for the focus-obscured check)
//   - a WCAG 2.4.11 Focus Not Obscured check — 6px expansion (approximates
//     Chromium default outline+offset) with 9-sample perimeter + forced-
//     colors flag. The paper reports no results from it.
//   - iframe resizes (correlated with iframeSrc mutation or GAM
//     slotRenderEnded within ±500 ms → marked as realRefresh=true,
//     otherwise wrapperReflow)
//   - late-arriving dialogs/overlays
//   - reading-order disruptions (compare against a virtual reading
//     position = last-tabbed element OR top-visible text node, so the
//     dwell phase still counts)
//   - ad slots that start empty and lazy-load on scroll
//   - GAM slotRenderEnded (true ad-refresh signal; `src` typically does
//     not change on modern GAM refresh)
//   - header-bidding wrapper presence (pbjs / apstag / IX / PWT)
//   - Shadow DOM — attachShadow is patched to open roots, so that closed
//     shadow roots (used by some ad slots) are reachable, by axe-core too

export const INSTRUMENT_SRC = `
(() => {
  // ---- Early exit in cross-origin iframes. We only want to
  // instrument the top realm and same-origin children (which crawl.js
  // harvests via page.frames()). In a cross-origin iframe, accessing
  // window.top.location throws, so this catch is the gate.
  try { void window.top.location.href; } catch { return; }
  if (window.__adAccess) return;

  const log = {
    mutations: [],       // {t, kind, target, info}
    focusEvents: [],     // {t, where, info}  (context for the 2.4.11 check)
    iframeResizes: [],   // {t, src, w, h, realRefresh, srcMutWithin500ms, slotRenderWithin500ms}
    newOverlays: [],     // {t, selector, role, visible, overlayId}
    lazyLoads: [],       // {t, slotId, bytes}
    focusObscured: [],   // {t, focused, by}  WCAG 2.4.11
    tabStops: [],        // {t, tag, role, name, inIframe, iframeSrc}
    readingOrderDisruptions: [],  // {t, insertedNode, virtualReadingPosition}
    slotRenderEnded: [], // {t, slotName, isEmpty, size, advertiserId, lineItemId}
    adRefreshEvents: [], // deduped real refresh (either slotRenderEnded with isRefresh or iframeSrc mutation on known ad iframe)
    headerBidding: null, // filled by probe on collect: {pbjs, apstag, IX, PWT, OpenWrap}
    forcedColors: null,  // boolean, filled at init
    isTopFrame: window === window.top,
    gating: null,        // filled from node side (top only)
    startedAt: Date.now(),
    url: location.href,
    frameOrigin: location.origin
  };
  window.__adAccess = log;
  let overlaySeq = 0;

  try { log.forcedColors = window.matchMedia && window.matchMedia('(forced-colors: active)').matches || false; } catch {}

  // ---- helpers ----

  // Ad hints: ad-slot tokens, ad networks, SafeFrame (sf_), OMID friendly
  // iframes (google_ads_*), AMP tags, Taboola/Outbrain structural classes.
  const AD_HINTS = /(^|[-_])ad(s|slot|unit|container|wrap)?([-_]|$)|googlesyndication|doubleclick|googletag|taboola|trc_rbox|trc_related|taboola-mod|outbrain|outbrain_container|OB_|revcontent|criteo|adnxs|pubmatic|openx|rubicon|magnite|yieldmo|teads|connatix|primis|smartadserver|triplelift|sharethrough|liveramp|adform|appnexus|moatads|adsafe|doubleverify|amazon-adsystem|media\\.net|amp-ad|amp-sticky-ad|safeframe|sf_[0-9]|google_ads_frame|omid/i;
  const AX_IGNORE = /SCRIPT|STYLE|LINK|META|NOSCRIPT/;

  const looksAdLike = el => {
    if (!el || !el.tagName) return false;
    // AMP ad tags are ad slots by tag alone
    const tag = (el.tagName || '').toLowerCase();
    if (tag === 'amp-ad' || tag === 'amp-sticky-ad' || tag === 'amp-embed') return true;
    const id = (el.id || '') + ' ' + (el.className && typeof el.className === 'string' ? el.className : '') +
               ' ' + (el.getAttribute && (el.getAttribute('data-ad-slot') || el.getAttribute('data-slotname') || el.getAttribute('data-outbrain-id') || el.getAttribute('data-widget-id') || ''));
    if (AD_HINTS.test(id)) return true;
    // SafeFrame / OMID iframe name heuristic
    if (tag === 'iframe') {
      const nm = el.name || '';
      if (/^sf_|^google_ads_|omid/i.test(nm)) return true;
    }
    return false;
  };

  const describe = el => {
    if (!el) return null;
    const t = el.tagName;
    const id = el.id || null;
    const cls = (el.className && typeof el.className === 'string') ? el.className.slice(0, 120) : null;
    const src = el.getAttribute && (el.getAttribute('src') || el.getAttribute('data-src'));
    const name = el.getAttribute && (el.getAttribute('aria-label') || el.getAttribute('title'));
    const role = el.getAttribute && el.getAttribute('role');
    const ifname = el.getAttribute && el.getAttribute('name');
    const rect = (el.getBoundingClientRect && el.getBoundingClientRect()) || null;
    return { t, id, cls, src, role, name, ifname,
      rect: rect ? { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) } : null,
      adLike: looksAdLike(el) };
  };

  // ---- Shadow DOM capture. Patch attachShadow so both OPEN and
  // (otherwise closed) roots are reachable via element.__adAccessShadow.
  const origAttachShadow = Element.prototype.attachShadow;
  try {
    Element.prototype.attachShadow = function(init) {
      // Force mode:'open' for our observation; store the original init
      // mode on the host so we can note it, but don't change the paint.
      const forced = Object.assign({}, init || {}, { mode: 'open' });
      const root = origAttachShadow.call(this, forced);
      this.__adAccessShadow = root;
      try {
        // Observe mutations inside the shadow root too
        if (window.__adAccessObserveSubtree) window.__adAccessObserveSubtree(root);
      } catch {}
      return root;
    };
  } catch {}

  // Walk shadow roots too
  function* walkAllNodes(root) {
    const stk = [root];
    while (stk.length) {
      const n = stk.pop();
      if (!n) continue;
      yield n;
      if (n.shadowRoot) stk.push(n.shadowRoot);
      const kids = n.children || [];
      for (let i = kids.length - 1; i >= 0; i--) stk.push(kids[i]);
    }
  }

  // ---- Virtual reading position.
  // During the Tab phase this is document.activeElement; during dwell/
  // scroll it falls back to the last-tabbed element, then to the topmost
  // visible text-bearing element (approximates "what the user is reading
  // right now"). This lets reading-order disruption fire outside the
  // Tab phase.
  let lastTabbedElement = null;
  const getVirtualReadingPosition = () => {
    const a = document.activeElement;
    if (a && a !== document.body && a !== document.documentElement) return a;
    if (lastTabbedElement && lastTabbedElement.isConnected) return lastTabbedElement;
    // top-visible text-bearing element
    try {
      const y = Math.max(1, Math.min(window.innerHeight - 2, Math.round(window.innerHeight * 0.25)));
      const stack = document.elementsFromPoint ? document.elementsFromPoint(Math.round(window.innerWidth / 2), y) : [];
      for (const s of stack) {
        if (s && s.tagName && !AX_IGNORE.test(s.tagName) && (s.textContent || '').trim().length > 20) return s;
      }
    } catch {}
    return null;
  };

  // ---- iframe resize correlation.
  // Mark a resize as a "real refresh/expand" iff an iframeSrc mutation
  // on the SAME iframe fired within ±500 ms OR a GAM slotRenderEnded for
  // the same slot fired within ±500 ms. Everything else is wrapperReflow,
  // not an ad refresh.
  const CORRELATION_MS = 500;
  const recentSrcMutations = []; // {t, iframe}
  const recentSlotRender = [];   // {t, slotName}
  const stampResize = (iframe, src) => {
    const now = Date.now() - log.startedAt;
    const srcMutWithin = recentSrcMutations.some(e => Math.abs(e.t - now) <= CORRELATION_MS && e.iframe === iframe);
    const slotRenderWithin = recentSlotRender.some(e => Math.abs(e.t - now) <= CORRELATION_MS);
    return { srcMutWithin, slotRenderWithin, realRefresh: srcMutWithin || slotRenderWithin };
  };

  // ---- MutationObserver setup, re-attachable, with Shadow DOM support.
  const observeSubtree = (root) => {
    const mo = new MutationObserver(list => {
      for (const m of list) {
        // iframe src change
        if (m.type === 'attributes' && m.target && m.target.tagName === 'IFRAME' && m.attributeName === 'src') {
          const iframe = m.target;
          const src = iframe.src || '';
          const parentAd = looksAdLike(iframe.parentElement);
          if (AD_HINTS.test(src) || parentAd || looksAdLike(iframe)) {
            const now = Date.now() - log.startedAt;
            recentSrcMutations.push({ t: now, iframe });
            if (recentSrcMutations.length > 500) recentSrcMutations.shift();
            log.mutations.push({ t: now, kind: 'iframeSrc', target: describe(iframe.parentElement),
              node: describe(iframe), oldSrc: m.oldValue, newSrc: src });
          }
          continue;
        }
        if (m.type !== 'childList') continue;
        for (const n of m.addedNodes) {
          if (!n || n.nodeType !== 1) continue;
          if (AX_IGNORE.test(n.tagName)) continue;
          const parentAdLike = looksAdLike(m.target) || (m.target && /adsbygoogle|google_ads|gpt-ad|dfp-ad|ad-slot|safeframe/i.test((m.target.id || '') + ' ' + (m.target.className || '')));
          const selfAdLike = looksAdLike(n) || (n.src && AD_HINTS.test(n.src));
          if (!parentAdLike && !selfAdLike) continue;
          log.mutations.push({ t: Date.now() - log.startedAt, kind: 'add', target: describe(m.target), node: describe(n) });

          // ---- Reading-order disruption (relative to the virtual reading position)
          try {
            const vrp = getVirtualReadingPosition();
            if (vrp && n.ownerDocument === vrp.ownerDocument) {
              const rel = n.compareDocumentPosition(vrp);
              const DISCONNECTED = Node.DOCUMENT_POSITION_DISCONNECTED;
              if (!(rel & DISCONNECTED) && (rel & Node.DOCUMENT_POSITION_FOLLOWING)) {
                log.readingOrderDisruptions.push({
                  t: Date.now() - log.startedAt,
                  insertedNode: describe(n),
                  virtualReadingPosition: describe(vrp),
                  positionSource: (document.activeElement && document.activeElement !== document.body) ? 'activeElement'
                    : (lastTabbedElement && lastTabbedElement.isConnected) ? 'lastTabbed' : 'topVisible',
                  positionRelation: 'inserted-before-reading-position'
                });
              }
            }
          } catch {}

          // overlay / interstitial detection
          const role = n.getAttribute && n.getAttribute('role');
          const style = window.getComputedStyle ? window.getComputedStyle(n) : null;
          const isFixed = style && (style.position === 'fixed' || style.position === 'sticky');
          const bigEnough = n.getBoundingClientRect && n.getBoundingClientRect().width > 200;
          const ariaModal = n.getAttribute && n.getAttribute('aria-modal') === 'true';
          if ((role === 'dialog' || role === 'alertdialog' || ariaModal || isFixed) && bigEnough) {
            const oid = 'adAccessOv_' + (++overlaySeq);
            try { n.setAttribute('data-adaccess-overlay', oid); } catch {}
            log.newOverlays.push({
              t: Date.now() - log.startedAt,
              overlayId: oid,
              selector: n.tagName + (n.id ? '#' + n.id : ''),
              role, ariaModal: !!ariaModal,
              visible: style ? style.visibility !== 'hidden' && style.display !== 'none' : null,
              rect: describe(n).rect,
              closeReachableInStops: null,
              closeReachable: null,
              focusStaysInside: null  // set by close-reach probe
            });
          }

          // iframe resize observer
          if (n.tagName === 'IFRAME') {
            try {
              const ro = new ResizeObserver(entries => {
                for (const e of entries) {
                  const stamp = stampResize(n, n.src);
                  log.iframeResizes.push({
                    t: Date.now() - log.startedAt,
                    src: n.src || n.getAttribute('data-src') || null,
                    ifname: n.name || null,
                    w: Math.round(e.contentRect.width),
                    h: Math.round(e.contentRect.height),
                    ...stamp
                  });
                }
              });
              ro.observe(n);
            } catch {}
          }
        }
      }
    });
    // Observe root with widest scope that still works for the given root
    // type. For Document / ShadowRoot we can observe directly.
    try {
      mo.observe(root, { childList: true, subtree: true, attributes: true, attributeOldValue: true, attributeFilter: ['src'] });
    } catch {}
    return mo;
  };
  window.__adAccessObserveSubtree = observeSubtree;

  const setupDomObservers = () => {
    // ---- Observe document (survives documentElement
    // replace), plus a watchdog that re-observes on readystatechange.
    try { observeSubtree(document); } catch {}
    document.addEventListener('readystatechange', () => { try { observeSubtree(document); } catch {} });

    // Observe existing shadow roots and iframes
    try {
      for (const n of walkAllNodes(document.documentElement)) {
        if (n.shadowRoot) observeSubtree(n.shadowRoot);
      }
    } catch {}

    // Watch existing iframes for resize
    document.querySelectorAll('iframe').forEach(f => {
      try {
        const ro = new ResizeObserver(entries => {
          for (const e of entries) {
            const stamp = stampResize(f, f.src);
            log.iframeResizes.push({
              t: Date.now() - log.startedAt,
              src: f.src || null,
              ifname: f.name || null,
              w: Math.round(e.contentRect.width),
              h: Math.round(e.contentRect.height),
              ...stamp
            });
          }
        });
        ro.observe(f);
      } catch {}
    });
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupDomObservers, { once: true });
  } else {
    setupDomObservers();
  }

  // ---- WCAG 2.4.11 Focus Not Obscured (Minimum).
  // Expands the focused element by 6px (approximates Chromium default
  // outline(2px) + offset(2px) + typical box-shadow spread(~2px)). The
  // real indicator is browser- and CSS-dependent. The paper reports no
  // results from this check.
  const OUT = 6;
  const anyAncestorSticky = (node) => {
    let cur = node, depth = 0;
    while (cur && depth++ < 5) {
      const cs = window.getComputedStyle(cur);
      if (cs.position === 'fixed' || cs.position === 'sticky') return cur;
      cur = cur.parentElement;
    }
    return null;
  };
  const addFocus = () => document.addEventListener('focusin', ev => {
    const el = ev.target;
    const d = describe(el);
    const inIframe = el && el.tagName === 'IFRAME';
    const iframeSrc = inIframe ? el.src : null;
    log.focusEvents.push({ t: Date.now() - log.startedAt, where: inIframe ? 'iframe' : 'doc', info: d, iframeSrc });

    try {
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) return;
      const left = Math.max(0, r.left - OUT), right = Math.min(window.innerWidth - 1, r.right + OUT);
      const top = Math.max(0, r.top - OUT), bottom = Math.min(window.innerHeight - 1, r.bottom + OUT);
      const samples = [
        [left + 1, top + 1], [right - 1, top + 1],
        [left + 1, bottom - 1], [right - 1, bottom - 1],
        [(left + right) / 2, top + 1], [(left + right) / 2, bottom - 1],
        [left + 1, (top + bottom) / 2], [right - 1, (top + bottom) / 2],
        [(left + right) / 2, (top + bottom) / 2]
      ];
      const stacks = samples.map(([x, y]) => (document.elementsFromPoint ? document.elementsFromPoint(x, y) : []));
      const obscurerFor = (stack) => {
        for (const s of stack) {
          if (!s || s === el || el.contains(s) || s.contains(el)) continue;
          const cs = window.getComputedStyle(s);
          if (cs.visibility === 'hidden' || cs.display === 'none') continue;
          if (cs.pointerEvents === 'none') continue;
          const stickyAnc = anyAncestorSticky(s);
          if (stickyAnc) return { by: describe(s), stickyAt: describe(stickyAnc) };
          const sr = s.getBoundingClientRect();
          if ((cs.position === 'absolute' || cs.position === 'fixed') &&
              sr.width * sr.height > window.innerWidth * window.innerHeight * 0.08) {
            return { by: describe(s), stickyAt: null };
          }
        }
        return null;
      };
      let perimeterObscured = 0, obscuredByFirst = null;
      for (const stack of stacks) {
        const o = obscurerFor(stack);
        if (o) { perimeterObscured++; if (!obscuredByFirst) obscuredByFirst = o; }
      }
      if (perimeterObscured >= 1 && obscuredByFirst) {
        log.focusObscured.push({
          t: Date.now() - log.startedAt,
          focused: d,
          by: obscuredByFirst.by,
          stickyAt: obscuredByFirst.stickyAt,
          perimeterPointsObscured: perimeterObscured,
          perimeterPointsSampled: samples.length,
          fullyObscured: perimeterObscured === samples.length,
          detectorVersion: 'r4-6px-forcedcolors=' + log.forcedColors
        });
      }
    } catch {}
  }, true);
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', addFocus, { once: true });
  } else {
    addFocus();
  }

  // ---- GAM slotRenderEnded hook.
  // The TRUE ad-refresh signal. On modern GAM the iframe is reused and
  // src doesn't change; only slotRenderEnded fires with isEmpty=false
  // and (optionally) an isRefresh flag we infer from being the Nth event
  // on the same slot.
  const slotRenderCountBySlot = Object.create(null);
  const hookGoogletag = () => {
    try {
      if (!window.googletag || !window.googletag.pubads) return false;
      window.googletag.cmd = window.googletag.cmd || [];
      window.googletag.cmd.push(() => {
        try {
          window.googletag.pubads().addEventListener('slotRenderEnded', ev => {
            const slot = ev.slot;
            const slotName = (slot && slot.getSlotElementId && slot.getSlotElementId()) || null;
            slotRenderCountBySlot[slotName || '__anon'] = (slotRenderCountBySlot[slotName || '__anon'] || 0) + 1;
            const isRefresh = slotRenderCountBySlot[slotName || '__anon'] > 1;
            const now = Date.now() - log.startedAt;
            recentSlotRender.push({ t: now, slotName });
            if (recentSlotRender.length > 500) recentSlotRender.shift();
            log.slotRenderEnded.push({
              t: now, slotName, isEmpty: !!ev.isEmpty,
              size: ev.size || null,
              advertiserId: ev.advertiserId || null, lineItemId: ev.lineItemId || null,
              isRefresh
            });
            if (!ev.isEmpty) log.adRefreshEvents.push({ t: now, source: 'slotRenderEnded', slotName, isRefresh });
          });
        } catch {}
      });
      return true;
    } catch { return false; }
  };
  if (!hookGoogletag()) {
    let tries = 0;
    const poll = setInterval(() => {
      tries++;
      if (hookGoogletag() || tries > 60) clearInterval(poll);
    }, 500);
  }

  // ---- Header-bidding wrapper presence probe (filled at collect)
  const probeHeaderBidding = () => ({
    pbjs: !!window.pbjs,
    pbjs_version: (window.pbjs && window.pbjs.version) || null,
    apstag: !!window.apstag,                 // Amazon TAM / UAM
    IX: !!window.headertag || !!window.IX,   // Index Exchange wrapper
    PWT: !!window.PWT,                       // PubMatic OpenWrap
    OpenWrap: !!window.PWT,
    googletag: !!window.googletag,
    criteoPubtag: !!window.Criteo
  });

  // ---- Utilities exposed to the node side ----
  window.__adAccessCollect = () => {
    try { log.headerBidding = probeHeaderBidding(); } catch {}
    return JSON.parse(JSON.stringify(log));
  };
  window.__adAccessMarkTab = (info) => {
    log.tabStops.push({ t: Date.now() - log.startedAt, ...info });
    try {
      const a = document.activeElement;
      if (a && a !== document.body) lastTabbedElement = a;
    } catch {}
  };
  window.__adAccessMarkLazy = (info) => log.lazyLoads.push({ t: Date.now() - log.startedAt, ...info });
  window.__adAccessSetGating = (g) => { log.gating = g; };
  window.__adAccessUpdateOverlay = (overlayId, patch) => {
    const ov = log.newOverlays.find(o => o.overlayId === overlayId);
    if (ov) Object.assign(ov, patch);
  };
})();
`;

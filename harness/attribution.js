// attribution.js — map hosts and element src to ad-tech vendors
// (data/ad-vendors.json).
//
// Every regex metachar in a pattern is escaped (so `media.net` does not
// match `mediaxnet`), and pure hostname patterns also match exactly or as
// a domain suffix.
import { readFileSync } from 'node:fs';

const raw = JSON.parse(readFileSync(new URL('../data/ad-vendors.json', import.meta.url), 'utf8'));

export const VENDORS = raw.vendors;

const esc = s => s.replace(/[.*+?^${}()|[\]\\\/]/g, '\\$&');

const COMPILED = VENDORS.map(v => {
  const hostPatterns = v.patterns.filter(p => !/[\/*]/.test(p));
  const pathPatterns = v.patterns.filter(p => /[\/]/.test(p));
  return {
    ...v,
    hostSet: new Set(hostPatterns),           // for exact hostname match
    hostRx: hostPatterns.length ? new RegExp('(^|\\.)(' + hostPatterns.map(esc).join('|') + ')$', 'i') : null,
    pathRx: pathPatterns.length ? new RegExp(pathPatterns.map(esc).join('|'), 'i') : null
  };
});

export function vendorFor(urlOrHost) {
  if (!urlOrHost) return null;
  const s = String(urlOrHost);
  // Try to parse as URL to isolate hostname and pathname
  let host = s, path = '';
  try {
    const u = new URL(s.startsWith('http') ? s : 'https://' + s);
    host = u.hostname;
    path = u.pathname;
  } catch { /* not a URL — treat as bare hostname */ }
  for (const v of COMPILED) {
    if (v.hostSet.has(host)) return { vendor: v.vendor, kind: v.kind };
    if (v.hostRx && v.hostRx.test(host)) return { vendor: v.vendor, kind: v.kind };
    if (v.pathRx && v.pathRx.test(host + path)) return { vendor: v.vendor, kind: v.kind };
  }
  return null;
}

// Given a request-URL list and an element src, pick the most specific vendor
export function attributeElement(el, networkHosts) {
  const direct = vendorFor(el.src) || vendorFor(el.id) || vendorFor(el.cls);
  if (direct) return direct;
  for (const h of networkHosts || []) {
    const v = vendorFor(h);
    if (v) return v;
  }
  return null;
}

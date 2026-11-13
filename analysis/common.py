"""Shared definitions for the analysis scripts: paths, the selector-matching
patterns, the naming and structure rule families, and the regions of the
sample. Stdlib only.

Paths can be redirected with environment variables:
  CRAWL_DIR      crawl data root (default: <repo>/crawl), laid out as
                 main/arm-normal/per-site, main/arm-blocked/per-site and
                 inframe/arm-normal/per-site
  RESULTS_DIR    where the numbers scripts write (default: <repo>/results)
  NODE_MODULES   installed npm packages (default: <repo>/node_modules); the
                 in-frame analysis reads axe-core's rule metadata from it
"""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRAWL = Path(os.environ.get("CRAWL_DIR") or ROOT / "crawl")
MAIN_ARMS = {"normal": CRAWL / "main/arm-normal/per-site",
             "blocked": CRAWL / "main/arm-blocked/per-site"}
INFRAME_NORMAL = CRAWL / "inframe/arm-normal/per-site"
OUT = Path(os.environ.get("RESULTS_DIR") or ROOT / "results")
NODE_MODULES = Path(os.environ.get("NODE_MODULES") or ROOT / "node_modules")
DATA = ROOT / "data"

# The paper analyses rounds 1 and 3 of the main crawl in both arms: a DNS
# outage on the crawl machine disrupted the second normal-arm round.
# ANALYSIS_ROUNDS=1,2,3 includes all three.
ROUNDS = {int(x) for x in (os.environ.get("ANALYSIS_ROUNDS") or "1,3").split(",")}
# The 35 platform and government domains of the 2,500-site sample
# (amazon.com, cdc.gov, ...) are left out of every analysis;
# KEEP_PLATFORM_DOMAINS=1 keeps them.
DROP_PLATFORM = os.environ.get("KEEP_PLATFORM_DOMAINS") != "1"


def read_sites(name):
    """Domains in a data/ site list (one per line, '#' comments)."""
    return {l.strip() for l in open(DATA / name) if l.strip() and not l.startswith('#')}


def platform_hosts():
    """The platform and government domains: the sample minus the analysed
    sites (sample/filter_platforms.py)."""
    return read_sites("sites-crux-news.txt") - read_sites("sites-crux-news-filtered.txt")


# ---- Selector matching (paper, Markup-based attribution). A failing node is
# tested on its CSS selector and, if no pattern matches, on its HTML, with
# the patterns in this order: consent (cookie banners are never ads), ad
# token, sponsored content.
#
# Ad-token pattern: an ad-slot token ('ad', 'ads', 'adslot', ...) delimited
# by anything but a letter or digit, ad-network names and Google ad-slot ids.
AD_RE = re.compile(
    r'(?<![a-z0-9])ad(s|slot|unit|container|wrap|vert)?(?![a-z0-9])'
    r'|google_?ad|gpt-?ad|dfp|doubleclick|taboola|outbrain|revcontent|criteo'
    r'|adnxs|pubmatic|openx|rubicon|magnite|yieldmo|teads|connatix|primis'
    r'|smartadserver|triplelift|sharethrough|adform|appnexus|moatads|adsafe'
    r'|doubleverify|amazon-adsystem|media\.net|adsbygoogle'
    r'|advertisement|advertorial',
    re.IGNORECASE
)
# Sponsored-content pattern: native ads (recommendation widgets) label their
# items as sponsored rather than as ads.
SPONSORED_RE = re.compile(r'sponsor|paid-?content|branded-?content|partner-?content', re.IGNORECASE)
# Consent pattern: consent-management platforms and cookie banners.
CONSENT_RX = re.compile(
    r'didomi|onetrust|cookiebot|quantcast|sp_message|sp-message|trustarc|sourcepoint'
    r'|usercentrics|consentmanager|cookiescript|iubenda|cookieinformation|privacyconsent'
    r'|consent|cookie|gdpr|ccpa|cmp|privacy-pref|privacy_?banner',
    re.IGNORECASE
)


def classify_match(s):
    """'consent', 'ad', 'sponsored' or None for one selector or HTML string."""
    if not s: return None
    if CONSENT_RX.search(s): return 'consent'
    if AD_RE.search(s): return 'ad'
    if SPONSORED_RE.search(s): return 'sponsored'
    return None


# ---- Rule families (paper, Table I). Naming rules require an accessible
# name; structure rules concern page structure and presentation.
NAMING_RULES = {
    "role-img-alt", "aria-command-name", "frame-title", "label", "image-alt",
    "link-name", "button-name", "aria-input-field-name", "aria-text",
    "input-image-alt", "svg-img-alt", "aria-tooltip-name",
    "aria-progressbar-name", "aria-dialog-name", "aria-meter-name",
    "input-button-name", "object-alt",
}
LAYOUT_RULES = {   # the structure family
    "heading-order", "color-contrast", "region", "landmark-one-main",
    "landmark-complementary-is-top-level", "landmark-no-duplicate-banner",
    "landmark-contentinfo-is-top-level", "landmark-main-is-top-level",
    "landmark-no-duplicate-contentinfo", "landmark-no-duplicate-main",
    "landmark-unique", "page-has-heading-one", "scrollable-region-focusable",
    "css-orientation-lock", "duplicate-id-aria", "duplicate-id",
    "duplicate-id-active", "html-has-lang", "html-lang-valid",
    "document-title", "nested-interactive", "no-autoplay-audio",
    "video-caption",
}

# ---- Regions, by top-level domain (the sample's quotas, sample/sample.py).
# us-en: generic TLDs and .us ("US or generic"); eu: 18 EU member-state
# ccTLDs plus .uk .ch .no .eu ("Europe"); row: the rest of the world.
EU_TLDS = {"de", "fr", "it", "es", "nl", "se", "no", "dk", "fi", "pl", "pt", "be", "at", "ch",
           "ie", "gr", "cz", "hu", "ro", "bg", "eu", "uk"}
US_LIKE = {"com", "net", "org", "us", "gov", "edu", "io", "tv", "co"}


def region_of(domain):
    p = domain.split(".")
    tld = p[-1].lower()
    sld = p[-2].lower() if len(p) >= 2 else ""
    if len(p) >= 3 and sld in {"co", "com"}:
        if tld in EU_TLDS: return "eu"
        if tld == "us": return "us-en"
        return "row"
    if tld in EU_TLDS: return "eu"
    if tld in US_LIKE: return "us-en"
    return "row"

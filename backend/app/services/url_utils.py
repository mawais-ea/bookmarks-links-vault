"""URL cleanup and platform detection."""

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

TRACKING = {
    "igsh", "igshid", "si", "feature", "fbclid", "gclid", "dclid", "msclkid", "ref", "ref_src",
    "ref_url", "s", "t", "share_id", "mibextid", "rdt", "_r", "_t", "is_from_webapp", "sender_device",
    "share_app_id", "utm", "mc_cid", "mc_eid", "spm", "xmt", "wt_mc", "cmpid",
}
# Query params that identify the content itself and must be kept even though they look like tracking.
KEEP = {"youtube.com": {"v", "list", "t"}, "facebook.com": {"v", "story_fbid", "id", "fbid"}}

PLATFORMS = {
    "instagram.com": "instagram", "tiktok.com": "tiktok", "youtube.com": "youtube",
    "youtu.be": "youtube", "x.com": "x", "twitter.com": "x", "facebook.com": "facebook",
    "fb.watch": "facebook", "fb.com": "facebook", "reddit.com": "reddit", "redd.it": "reddit",
    "linkedin.com": "linkedin", "lnkd.in": "linkedin", "pinterest.com": "pinterest",
    "pin.it": "pinterest", "threads.net": "threads", "threads.com": "threads",
}

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def extract_url(text: str) -> str | None:
    """Pull the first URL out of shared text like 'Check this out! https://...'."""
    if not text:
        return None
    m = _URL_RE.search(text)
    if m:
        return m.group(0).rstrip(").,!?;:'\"")
    text = text.strip()
    if " " not in text and "." in text:
        return "https://" + text
    return None


def _host(netloc: str) -> str:
    host = netloc.lower().split("@")[-1].split(":")[0]
    for prefix in ("www.", "m.", "mobile.", "old.", "new."):
        if host.startswith(prefix) and host.count(".") >= 2:
            host = host[len(prefix):]
    return host


def normalize(url: str) -> str:
    p = urlparse(url.strip())
    if not p.scheme:
        p = urlparse("https://" + url.strip())
    host = _host(p.netloc)
    keep = next((k for dom, k in KEEP.items() if host.endswith(dom)), set())
    q = [
        (k, v) for k, v in parse_qsl(p.query, keep_blank_values=False)
        if k in keep or (k.lower() not in TRACKING and not k.lower().startswith("utm_"))
    ]
    path = p.path.rstrip("/") or ""
    # youtu.be/<id> and youtube.com/shorts/<id> -> youtube.com/watch?v=<id> so duplicates collapse.
    if host == "youtu.be" and path:
        host, q = "youtube.com", [("v", path.lstrip("/"))] + [(k, v) for k, v in q if k != "v"]
        path = "/watch"
    elif host.endswith("youtube.com") and path.startswith("/shorts/"):
        q = [("v", path.split("/")[2])] + [(k, v) for k, v in q if k != "v"]
        path = "/watch"
    scheme = "https" if p.scheme in ("http", "https", "") else p.scheme
    return urlunparse((scheme, host, path, "", urlencode(q), ""))


def detect_platform(url: str) -> str:
    host = _host(urlparse(url).netloc)
    return next((name for dom, name in PLATFORMS.items() if host == dom or host.endswith("." + dom)), "web")


SHORTENERS = {
    "vm.tiktok.com", "vt.tiktok.com", "pin.it", "fb.watch", "t.co", "bit.ly", "lnkd.in",
    "redd.it", "tinyurl.com", "ow.ly", "buff.ly", "goo.gl", "amzn.to", "a.co",
}


def is_short_link(url: str) -> bool:
    host = _host(urlparse(url).netloc)
    return host in SHORTENERS or (host.endswith("reddit.com") and "/s/" in urlparse(url).path)


def domain_of(url: str) -> str:
    return _host(urlparse(url).netloc)

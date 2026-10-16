"""Metadata extraction: oEmbed -> OpenGraph -> yt-dlp -> trafilatura.

Each step only fills fields that earlier steps left empty. Nothing here
raises: a link with no metadata still gets saved with its domain as title.
"""

import asyncio
import html
import logging
import re
from dataclasses import dataclass, field, fields
from urllib.parse import quote, urlparse

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..config import get_settings
from .recipes import parse_jsonld_recipe
from .transcriber import captions_from_info
from .url_utils import domain_of

log = logging.getLogger(__name__)

VIDEO_PLATFORMS = {"youtube", "tiktok", "instagram", "facebook", "x", "reddit", "threads"}
_HASHTAG_RE = re.compile(r"#(\w{2,40})", re.UNICODE)
META_GRAPH_VERSION = "v24.0"


@dataclass
class Metadata:
    title: str | None = None
    description: str | None = None
    thumbnail_url: str | None = None
    author: str | None = None
    site_name: str | None = None
    raw_text: str | None = None
    hashtags: list[str] = field(default_factory=list)
    final_url: str | None = None
    # Video only: duration in seconds and the full transcript from captions (if published).
    is_video: bool = False
    duration: float | None = None
    transcript: str | None = None
    transcript_source: str | None = None
    recipe: dict | None = None  # schema.org Recipe published by the page

    def merge(self, other: "Metadata") -> None:
        for f in fields(self):
            if f.name == "hashtags":
                self.hashtags = list(dict.fromkeys(self.hashtags + other.hashtags))
            elif not getattr(self, f.name) and getattr(other, f.name):
                setattr(self, f.name, getattr(other, f.name))

    @property
    def complete(self) -> bool:
        return bool(self.title and self.thumbnail_url and (self.raw_text or self.description))

    def text_blob(self) -> str:
        parts = [self.title, self.description, self.raw_text, " ".join("#" + h for h in self.hashtags)]
        return "\n".join(p for p in parts if p)


def _clean(s: str | None, limit: int = 5000) -> str | None:
    if not s:
        return None
    s = html.unescape(s).strip()
    return s[:limit] or None


def hashtags_in(text: str | None) -> list[str]:
    return [h.lower() for h in _HASHTAG_RE.findall(text or "")]


def _client() -> httpx.AsyncClient:
    s = get_settings()
    return httpx.AsyncClient(
        timeout=s.fetch_timeout,
        follow_redirects=True,
        headers={"User-Agent": s.user_agent, "Accept-Language": "en-US,en;q=0.9"},
    )


async def resolve_redirects(url: str) -> str:
    """Follow short links (vm.tiktok.com, pin.it, t.co...) to the real URL."""
    try:
        async with _client() as c:
            r = await c.head(url)
            if r.status_code >= 400 or str(r.url) == url:
                r = await c.get(url)
            return str(r.url)
    except httpx.HTTPError as e:
        log.info("redirect resolve failed for %s: %s", url, e)
        return url


# ---------------------------------------------------------------- oEmbed


async def _oembed(c: httpx.AsyncClient, url: str, platform: str) -> Metadata:
    s = get_settings()
    q = quote(url, safe="")
    if platform == "youtube":
        endpoint = f"https://www.youtube.com/oembed?url={q}&format=json"
    elif platform == "tiktok":
        endpoint = f"https://www.tiktok.com/oembed?url={q}"
    elif platform == "x":
        endpoint = f"https://publish.twitter.com/oembed?url={q}&omit_script=1&dnt=true"
    elif platform == "reddit":
        # .json has the full post text but is often blocked; oEmbed is official and reliable.
        m = await _reddit_json(c, url)
        if not m.title:
            m.merge(await _reddit_oembed(c, url))
        return m
    elif platform in ("instagram", "facebook") and s.meta_oembed_token:
        kind = "instagram_oembed" if platform == "instagram" else (
            "oembed_video" if "/videos/" in url or "/reel" in url or "fb.watch" in url else "oembed_post"
        )
        endpoint = (
            f"https://graph.facebook.com/{META_GRAPH_VERSION}/{kind}"
            f"?url={q}&access_token={quote(s.meta_oembed_token, safe='')}&omitscript=true"
        )
    else:
        return Metadata()

    r = await c.get(endpoint)
    if r.status_code != 200:
        return Metadata()
    d = r.json()
    m = Metadata(
        title=_clean(d.get("title"), 500),
        author=_clean(d.get("author_name"), 255),
        thumbnail_url=d.get("thumbnail_url"),
        site_name=_clean(d.get("provider_name"), 255),
    )
    embed_html = d.get("html")
    if embed_html and platform in ("x", "instagram", "facebook"):
        # The post text lives inside the embed HTML (<p> for tweets, <blockquote> captions).
        node = HTMLParser(embed_html).css_first("p") or HTMLParser(embed_html).body
        text = node.text(separator=" ").strip() if node else ""
        m.raw_text = _clean(text)
        if not m.title and text:
            m.title = _clean(text, 140)
    if platform == "tiktok":
        m.raw_text = m.title
    m.hashtags = hashtags_in(m.raw_text or m.title)
    return m


async def _reddit_oembed(c: httpx.AsyncClient, url: str) -> Metadata:
    r = await c.get("https://www.reddit.com/oembed", params={"url": url})
    if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
        return Metadata()
    d = r.json()
    subreddit = None
    for a in HTMLParser(d.get("html") or "").css("a"):
        href = a.attributes.get("href") or ""
        if href.rstrip("/").count("/") == 4 and "/r/" in href:  # https://www.reddit.com/r/<name>/
            subreddit = "r/" + href.rstrip("/").rsplit("/", 1)[-1]
    return Metadata(
        title=_clean(d.get("title"), 500),
        author=f"u/{d['author_name']}" if d.get("author_name") else None,
        site_name=subreddit or "Reddit",
    )


async def _reddit_json(c: httpx.AsyncClient, url: str) -> Metadata:
    p = urlparse(url)
    r = await c.get(f"https://www.reddit.com{p.path.rstrip('/')}.json", params={"raw_json": 1})
    if r.status_code != 200:
        return Metadata()
    try:
        post = r.json()[0]["data"]["children"][0]["data"]
    except (ValueError, KeyError, IndexError, TypeError):
        return Metadata()
    thumb = None
    try:
        thumb = post["preview"]["images"][0]["source"]["url"]
    except (KeyError, IndexError, TypeError):
        t = post.get("thumbnail")
        thumb = t if t and t.startswith("http") else None
    return Metadata(
        title=_clean(post.get("title"), 500),
        raw_text=_clean(post.get("selftext")),
        author=f"u/{post['author']}" if post.get("author") else None,
        site_name=f"r/{post['subreddit']}" if post.get("subreddit") else "Reddit",
        thumbnail_url=thumb,
    )


# ---------------------------------------------------------------- OpenGraph


def parse_open_graph(page_html: str) -> Metadata:
    tree = HTMLParser(page_html)
    meta: dict[str, str] = {}
    for node in tree.css("meta"):
        key = (node.attributes.get("property") or node.attributes.get("name") or "").lower()
        val = node.attributes.get("content")
        if key and val and key not in meta:
            meta[key] = val
    title_node = tree.css_first("title")
    m = Metadata(
        title=_clean(meta.get("og:title") or meta.get("twitter:title") or (title_node.text() if title_node else None), 500),
        description=_clean(meta.get("og:description") or meta.get("twitter:description") or meta.get("description"), 1000),
        thumbnail_url=meta.get("og:image") or meta.get("og:image:url") or meta.get("twitter:image") or meta.get("twitter:image:src"),
        site_name=_clean(meta.get("og:site_name") or meta.get("application-name"), 255),
        author=_clean(meta.get("author") or meta.get("article:author") or meta.get("twitter:creator"), 255),
    )
    keywords = meta.get("keywords") or ""
    m.hashtags = hashtags_in(m.description) + [k.strip().lower() for k in keywords.split(",") if 1 < len(k.strip()) < 30][:10]
    return m


async def _fetch_html(c: httpx.AsyncClient, url: str) -> tuple[str | None, str]:
    r = await c.get(url)
    if r.status_code in (403, 429):
        # Some sites (e.g. Wikipedia) reject browser-looking clients but allow an honest bot UA.
        r = await c.get(url, headers={"User-Agent": get_settings().bot_user_agent})
    ctype = r.headers.get("content-type", "")
    if r.status_code >= 400 or "html" not in ctype:
        return None, str(r.url)
    return r.text, str(r.url)


# ---------------------------------------------------------------- yt-dlp / trafilatura


def _ytdlp_sync(url: str) -> Metadata:
    import yt_dlp  # imported lazily: heavy, and optional

    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
        "socket_timeout": 10, "extract_flat": False, "ignore_no_formats_error": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False) or {}
    caption = info.get("description") or ""
    tags = [t.lower() for t in (info.get("tags") or []) if isinstance(t, str)]
    is_video = bool(info.get("duration") or info.get("formats") or info.get("url"))
    s = get_settings()
    # Without Claude nothing can translate, so ask the platform for captions in the summary language.
    prefer = None if s.llm_ready() else s.summary_language_code
    transcript = captions_from_info(info, prefer) if (is_video and s.enable_transcripts) else None
    return Metadata(
        is_video=is_video,
        duration=info.get("duration"),
        transcript=transcript[0] if transcript else None,
        transcript_source=transcript[1] if transcript else None,
        title=_clean(info.get("title") or info.get("fulltitle"), 500),
        raw_text=_clean(caption),
        author=_clean(info.get("uploader") or info.get("channel") or info.get("creator"), 255),
        thumbnail_url=info.get("thumbnail"),
        site_name=_clean(info.get("extractor_key"), 255),
        hashtags=hashtags_in(caption) + tags[:10],
    )


async def _ytdlp(url: str) -> Metadata:
    try:
        return await asyncio.wait_for(asyncio.to_thread(_ytdlp_sync, url), timeout=90)
    except Exception as e:  # yt-dlp raises many error types; never fail the pipeline
        log.info("yt-dlp failed for %s: %s", url, e)
        return Metadata()


def _readable_text(page_html: str) -> str | None:
    try:
        import trafilatura
    except ImportError:
        return None
    try:
        return _clean(trafilatura.extract(page_html, include_comments=False, include_tables=False))
    except Exception as e:
        log.info("trafilatura failed: %s", e)
        return None


# ---------------------------------------------------------------- main entry


async def extract(url: str, platform: str) -> Metadata:
    s = get_settings()
    meta = Metadata()
    page_html: str | None = None

    async with _client() as c:
        try:
            meta.merge(await _oembed(c, url, platform))
        except (httpx.HTTPError, ValueError) as e:
            log.info("oEmbed failed for %s: %s", url, e)

        if not meta.complete:
            try:
                page_html, final = await _fetch_html(c, url)
                meta.final_url = final
                if page_html:
                    meta.merge(parse_open_graph(page_html))
                    meta.recipe = parse_jsonld_recipe(page_html)
            except httpx.HTTPError as e:
                log.info("page fetch failed for %s: %s", url, e)

    # yt-dlp runs for every video platform when transcripts are on (it carries the caption tracks).
    need_ytdlp = not (meta.title and meta.raw_text) or s.enable_transcripts
    if s.enable_ytdlp and platform in VIDEO_PLATFORMS and need_ytdlp:
        meta.merge(await _ytdlp(url))

    if s.enable_trafilatura and platform == "web" and page_html and not meta.raw_text:
        meta.raw_text = await asyncio.to_thread(_readable_text, page_html)

    meta.title = better_title(meta, platform) or domain_of(url)
    return meta


GENERIC_TITLES = {"instagram", "tiktok", "facebook", "youtube", "x", "twitter", "threads", "pinterest",
                  "linkedin", "reddit", "log in", "login", "sign in", "watch", "post", "reel", "video"}


def better_title(meta: Metadata, platform: str) -> str | None:
    """Instagram & co. often give just "Instagram" as the title; use the caption's first line instead."""
    title = (meta.title or "").strip()
    generic = not title or title.lower().strip(" •|-") in GENERIC_TITLES or title.lower() == (meta.site_name or "").lower()
    if not generic:
        return title
    for text in (meta.description, meta.raw_text):
        if not text:
            continue
        for line in text.splitlines():
            line = _HASHTAG_RE.sub("", line).strip(" -–•|:")
            if len(line) >= 4:
                return line if len(line) <= 100 else line[:97].rsplit(" ", 1)[0] + "…"
    if meta.author:
        return f"{PLATFORM_LABELS.get(platform, meta.site_name or 'Post')} by {meta.author}"
    return title or None


PLATFORM_LABELS = {"instagram": "Instagram post", "tiktok": "TikTok video", "facebook": "Facebook post",
                   "youtube": "YouTube video", "x": "Post on X", "threads": "Threads post",
                   "pinterest": "Pin", "linkedin": "LinkedIn post", "reddit": "Reddit post"}

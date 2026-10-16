"""Receives links from the OS.

- Android: the share intent is turned into a deep link `linkvault://app/save?text=...`
  by the native snippet in native/android, which Flet delivers as a route.
- iOS: the Share Extension (native/ios-share-extension) posts to the API directly;
  the same deep link also works from Shortcuts ("Open URL linkvault://app/save?url=...").
- Windows / everywhere: when the app gains focus and the clipboard holds a new URL,
  offer to save it.
"""

from urllib.parse import parse_qs, urlparse


_URL_SCHEMES = ("http://", "https://")


def url_from_route(route: str) -> str | None:
    """Extract the shared text from '/save?url=...' (Flutter turns linkvault://app/save?url=... into that)."""
    if not route or "?" not in route:
        return None
    qs = parse_qs(urlparse(route).query)
    for key in ("url", "text"):
        if qs.get(key):
            return qs[key][0]
    return None


def looks_like_url(text: str | None) -> bool:
    if not text:
        return False
    text = text.strip()
    return text.startswith(_URL_SCHEMES) and " " not in text and len(text) < 4096


class ClipboardWatcher:
    """Remembers what it already offered so the prompt appears once per link."""

    def __init__(self, get_setting, set_setting):
        self._get = get_setting
        self._set = set_setting

    def new_url(self, clip: str | None) -> str | None:
        if not looks_like_url(clip):
            return None
        clip = clip.strip()
        if clip == self._get("last_clipboard_offer"):
            return None
        self._set("last_clipboard_offer", clip)
        return clip

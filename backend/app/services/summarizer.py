"""Quick extractive summary, used when Claude isn't available.

Claude writes the real summary (see categorizer.py). Without an API key we still give
something useful: the most representative sentences of the captions or text, in order.
"""

import re
from collections import Counter

_TIMESTAMP_RE = re.compile(r"\[\d+(?::\d{2}){1,2}\]\s*")
# Caption sound tags: (Laughter), [Music], (Applause), ♪
_SOUND_TAG_RE = re.compile(r"\([^()]{1,30}\)|\[[^\[\]]{1,30}\]|[♪♫]")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
STOPWORDS = set("""
the and for are but not you all any can had her was one our out has him his how its may new now old see two
way who did get got let put say she too use that this with have from they will would there their what about
which when make like time just know take into your some could them than then look only come over also back
after well want because these give most very even here more were been being does doing really going gonna
yeah okay right thing things stuff something actually basically literally just
""".split())

MIN_CHARS = 400
_HASHTAG_RE = re.compile(r"#\w+")
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)


def mostly_latin(text: str | None) -> bool:
    """Rough check that text is in a Latin-script language (e.g. English), not Hindi/Urdu/Arabic etc."""
    letters = _LETTER_RE.findall(_HASHTAG_RE.sub("", text or ""))
    if not letters:
        return False
    return sum(ch.isascii() for ch in letters) / len(letters) >= 0.8


def _units(text: str) -> list[str]:
    """Sentences; auto-captions often have no punctuation, so fall back to ~25-word chunks."""
    text = _HASHTAG_RE.sub(" ", _SOUND_TAG_RE.sub(" ", _TIMESTAMP_RE.sub("", text)))
    text = " ".join(text.replace(" -- ", " ").split())
    sentences = [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]
    if len(sentences) >= 4 and max(len(s.split()) for s in sentences) <= 80:
        return sentences
    words = text.split()
    return [" ".join(words[i:i + 25]) for i in range(0, len(words), 25)]


def extractive_summary(text: str | None, max_points: int = 6) -> str | None:
    if not text or len(text) < MIN_CHARS:
        return None
    units = [u for u in _units(text) if len(u.split()) >= 8]
    if len(units) < 3:
        return None
    freq = Counter(w for w in _WORD_RE.findall(" ".join(units).lower()) if w not in STOPWORDS)
    if not freq:
        return None

    def score(u: str) -> float:
        words = [w for w in _WORD_RE.findall(u.lower()) if w not in STOPWORDS]
        return sum(freq[w] for w in words) / (len(words) + 5) if words else 0.0

    n = max(3, min(max_points, len(units) // 6))
    best = sorted(sorted(range(len(units)), key=lambda i: -score(units[i]))[:n])
    return "\n".join(f"- {units[i].rstrip()}" for i in best)

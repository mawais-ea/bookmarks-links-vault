"""Screenshots: store the image, read the text in it, describe it.

Every screenshot lands in the "Screenshots" category. With Claude, the image is read
(title, one-line description, summary, all visible text, tags). Without Claude, the
text is read locally with OCR (pip install rapidocr-onnxruntime) when available.

Images are served from /files/<user>/<name>?sig=..., an HMAC-signed path, so the app can
show them with a plain image URL (no auth header needed) and nobody can guess other paths.
"""

import base64
import hashlib
import hmac
import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import db as db_module
from ..config import get_settings
from ..models import Bookmark, Category

log = logging.getLogger(__name__)

try:  # iPhone photos are HEIC; optional dependency (pip install pillow-heif)
    import pillow_heif

    pillow_heif.libheif_version()  # loads the native library now, so a blocked DLL is noticed here
    pillow_heif.register_heif_opener()
    HEIC_SUPPORTED = True
except Exception:  # missing, or its DLL is blocked (e.g. Windows Application Control)
    HEIC_SUPPORTED = False


def _looks_like_heic(data: bytes) -> bool:
    return data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"hevc")

CATEGORY = ("Screenshots", "screenshot", "#00897B")
KEEP_FORMATS = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp", "GIF": "gif"}  # anything else (HEIC, BMP) -> PNG
CLAUDE_MAX_BYTES = 4_500_000  # stay under the API's 5 MB per-image limit
CLAUDE_MAX_SIDE = 2000


# ---------------------------------------------------------------- storage


def files_root() -> Path:
    root = Path(get_settings().files_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def file_path(user_id: int, name: str) -> Path:
    return files_root() / str(user_id) / name


def sign(user_id: int, name: str) -> str:
    msg = f"{user_id}/{name}".encode()
    return hmac.new(get_settings().secret_key.encode(), msg, hashlib.sha256).hexdigest()[:32]


def signed_url(user_id: int, name: str) -> str:
    return f"/files/{user_id}/{name}?sig={sign(user_id, name)}"


def verify(user_id: int, name: str, sig: str) -> bool:
    return hmac.compare_digest(sign(user_id, name), sig or "")


class BadImage(ValueError):
    pass


def store_image(user_id: int, data: bytes) -> tuple[str, str]:
    """Save the image; returns (sha256, file name). Identical images share one file."""
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        img = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError, ImportError) as e:
        if _looks_like_heic(data):
            raise BadImage("HEIC photos can't be read on this server. In the Shortcut, add "
                           "'Convert Image' to JPEG before uploading.") from e
        raise BadImage("That file isn't an image") from e
    ext = KEEP_FORMATS.get(img.format or "")
    if not ext:  # BMP, HEIC via plugins, etc.: store as PNG
        buf = io.BytesIO()
        img.save(buf, "PNG")
        data, ext = buf.getvalue(), "png"
    sha = hashlib.sha256(data).hexdigest()
    name = f"{sha[:32]}.{ext}"
    path = file_path(user_id, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(data)
    return sha, name


def screenshots_category(db: Session, user_id: int) -> Category:
    """Get or create the user's Screenshots category (placed just before Other)."""
    name, icon, color = CATEGORY
    cat = db.scalar(select(Category).where(Category.user_id == user_id, Category.name == name))
    if cat:
        return cat
    other = db.scalar(select(Category).where(Category.user_id == user_id, Category.name == "Other"))
    if other:
        order = other.sort_order
        other.sort_order += 1
    else:
        order = (db.scalar(select(func.max(Category.sort_order)).where(Category.user_id == user_id)) or 0) + 1
    cat = Category(user_id=user_id, name=name, icon=icon, color=color, sort_order=order, is_default=True)
    db.add(cat)
    db.flush()
    return cat


def ensure_category_for_all_users() -> None:
    """Accounts created before screenshots existed get the category too."""
    from ..models import User

    db = db_module.SessionLocal()
    try:
        for user_id in db.scalars(select(User.id)):
            screenshots_category(db, user_id)
        db.commit()
    finally:
        db.close()


def default_title(taken_at: datetime | None) -> str:
    when = (taken_at or datetime.now(timezone.utc)).astimezone()
    return "Screenshot " + when.strftime("%d %b %Y, %H:%M")


# ---------------------------------------------------------------- reading the image


def _image_for_claude(path: Path) -> tuple[str, str]:
    """(media_type, base64). Downscales big screenshots so they fit the API limit."""
    data = path.read_bytes()
    img = Image.open(io.BytesIO(data))
    media = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}.get(img.format)
    if media and len(data) <= CLAUDE_MAX_BYTES and max(img.size) <= CLAUDE_MAX_SIDE * 4:
        return media, base64.b64encode(data).decode()
    img.thumbnail((CLAUDE_MAX_SIDE, CLAUDE_MAX_SIDE))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=85)
    return "image/jpeg", base64.b64encode(buf.getvalue()).decode()


SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "summary": {"type": "string"},
        "text": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "description", "summary", "text", "tags"],
    "additionalProperties": False,
}


def _prompt() -> str:
    lang = get_settings().summary_language
    return f"""This is a screenshot a person saved to look at later. Read it carefully.

Return:
- title: a short, specific title (under 70 characters), e.g. "Flight booking: Lahore to Dubai, 3 Nov" or "Python error: KeyError in pandas".
- description: one sentence (under 160 characters) saying what the screenshot shows and why it might matter.
- summary: the useful content, so the person doesn't have to open the image: one or two sentences, then bullet lines ("- ") with the key details it contains (names, numbers, dates, prices, steps, code, messages). Only what is visible; don't invent anything. Empty string if there's nothing beyond the description.
- text: all readable text in the screenshot, transcribed as-is in its original language, keeping line breaks. Empty string if there is none.
- tags: up to 4 short lowercase tags.

Write title, description, summary and tags in {lang} (translate if needed)."""


async def read_with_claude(path: Path) -> dict | None:
    from .categorizer import _llm  # shares the configured client

    s = get_settings()
    if not s.llm_ready():
        return None
    media, b64 = _image_for_claude(path)
    try:
        msg = await _llm().messages.create(
            model=s.llm_model,
            max_tokens=8000,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}},
                {"type": "text", "text": _prompt()},
            ]}],
        )
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
        log.warning("Claude couldn't read screenshot: %s", e)
        return None
    except TypeError as e:  # no credentials configured
        if "authentication" not in str(e).lower():
            raise
        return None
    if msg.stop_reason != "end_turn":
        log.warning("screenshot read stopped with %s", msg.stop_reason)
        return None
    text = next((b.text for b in msg.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


_ocr = None


def read_text_locally(path: Path) -> str | None:
    """Local OCR fallback. Optional dependency: rapidocr-onnxruntime."""
    global _ocr
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return None
    if _ocr is None:
        _ocr = RapidOCR()
    try:
        result, _ = _ocr(str(path))
    except Exception as e:
        log.info("local OCR failed: %s", e)
        return None
    if not result:
        return None
    # result rows: [box, text, score]; group into lines by vertical position.
    rows = sorted(result, key=lambda r: (round(r[0][0][1] / 15), r[0][0][0]))
    lines, last_y = [], None
    for box, text, score in rows:
        y = round(box[0][1] / 15)
        if lines and y == last_y:
            lines[-1] += " " + text
        else:
            lines.append(text)
        last_y = y
    return "\n".join(lines).strip() or None


# ---------------------------------------------------------------- background job


async def process_screenshot(bookmark_id: int) -> None:
    import asyncio

    from .summarizer import extractive_summary, mostly_latin

    db = db_module.SessionLocal()
    try:
        bm = db.get(Bookmark, bookmark_id)
        if not bm or bm.deleted_at or not bm.file_name:
            return
        path = file_path(bm.user_id, bm.file_name)
        try:
            info = await read_with_claude(path)
            if info:
                bm.title = (info.get("title") or "").strip()[:200] or bm.title
                bm.description = (info.get("description") or "").strip()[:300] or None
                bm.summary = (info.get("summary") or "").strip() or None
                bm.summary_source = "ai" if bm.summary else None
                bm.raw_text = (info.get("text") or "").strip() or None
                bm.tags = [t.strip().lower() for t in info.get("tags") or [] if t.strip()][:4]
            else:
                text = await asyncio.to_thread(read_text_locally, path)
                bm.raw_text = text
                if text:
                    first = next((line for line in text.splitlines() if len(line.strip()) > 3), "")
                    bm.description = first[:160] or None
                    quick = extractive_summary(text) if mostly_latin(text) else None
                    bm.summary, bm.summary_source = quick, ("quick" if quick else None)
            bm.status = "ready"
        except Exception:
            log.exception("reading screenshot %s failed", bookmark_id)
            bm.status = "ready"  # the image itself is saved; reading it is a bonus
        db.commit()
    finally:
        db.close()

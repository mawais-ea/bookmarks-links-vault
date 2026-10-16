"""Runs the enrichment steps for one bookmark in the background."""

import asyncio
import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import db as db_module
from ..config import get_settings
from ..models import Bookmark, Category, CategoryRule, Correction, User, utcnow
from . import categorizer, extractor, recipe_job, transcriber
from .recipes import recipe_from_text
from .summarizer import extractive_summary, mostly_latin

RECIPES = "Recipes & Food"
from .url_utils import detect_platform, domain_of, is_short_link, normalize

log = logging.getLogger(__name__)


def seed_default_categories(db: Session, user: User) -> None:
    for i, (name, icon, color) in enumerate(categorizer.DEFAULT_CATEGORIES):
        db.add(Category(user_id=user.id, name=name, icon=icon, color=color, sort_order=i, is_default=True))


def user_categories(db: Session, user_id: int) -> list[Category]:
    return list(db.scalars(select(Category).where(Category.user_id == user_id).order_by(Category.sort_order)))


def correction_examples(db: Session, user_id: int, limit: int = 10) -> list[str]:
    rows = db.execute(
        select(Bookmark.title, Bookmark.canonical_url, Category.name)
        .join(Correction, Correction.bookmark_id == Bookmark.id)
        .join(Category, Category.id == Correction.to_category_id)
        .where(Correction.user_id == user_id)
        .order_by(Correction.created_at.desc())
        .limit(limit)
    ).all()
    return [f'"{(t or u)[:80]}" ({domain_of(u)}) -> {c}' for t, u, c in rows]


def user_rules(db: Session, user_id: int) -> list[categorizer.UserRule]:
    rows = db.execute(
        select(CategoryRule.type, CategoryRule.pattern, Category.name)
        .join(Category, Category.id == CategoryRule.category_id)
        .where(CategoryRule.user_id == user_id)
    ).all()
    return [categorizer.UserRule(t, p, c) for t, p, c in rows]


def _merge_into_existing(db: Session, bm: Bookmark, canonical: str) -> bool:
    """If resolving a short link revealed a duplicate, bump the original and drop this one."""
    existing = db.scalar(select(Bookmark).where(
        Bookmark.user_id == bm.user_id, Bookmark.canonical_url == canonical, Bookmark.id != bm.id,
    ))
    if not existing:
        return False
    existing.created_at = utcnow()
    existing.position = time.time()
    existing.deleted_at = None
    if bm.note and not existing.note:
        existing.note = bm.note
    bm.deleted_at = utcnow()
    bm.status = "ready"
    db.commit()
    return True


async def process_bookmark(bookmark_id: int) -> None:
    db = db_module.SessionLocal()
    try:
        bm = db.get(Bookmark, bookmark_id)
        if not bm or bm.deleted_at or bm.platform == "screenshot":
            return
        try:
            needs_audio = await _enrich(db, bm)
            if needs_audio:
                await _transcribe_audio(db, bm)
            # Landed in a recipe category without a recipe: look harder, as when it's moved there by hand.
            db.refresh(bm)
            if not bm.deleted_at and not bm.recipe and recipe_job.is_recipe_category(bm.category):
                await recipe_job.find_recipe(bm.id)
        except Exception:
            log.exception("processing bookmark %s failed", bookmark_id)
            db.rollback()
            bm = db.get(Bookmark, bookmark_id)
            if bm and bm.status == "ready":
                if bm.transcript_status == "processing":
                    bm.transcript_status = "failed"
                    db.commit()
            elif bm:
                bm.status = "failed"
                bm.title = bm.title or domain_of(bm.url)
                if bm.category_id is None:
                    other = db.scalar(select(Category).where(
                        Category.user_id == bm.user_id, Category.name == categorizer.OTHER))
                    bm.category_id = other.id if other else None
                db.commit()
    finally:
        db.close()


async def _enrich(db: Session, bm: Bookmark) -> bool:
    """Fill in metadata and category. Returns True when the video still needs speech-to-text."""
    s = get_settings()
    url = bm.url

    if is_short_link(url):
        url = await extractor.resolve_redirects(url)
        canonical = normalize(url)
        if canonical != bm.canonical_url:
            if _merge_into_existing(db, bm, canonical):
                return False
            bm.canonical_url = canonical
            bm.platform = detect_platform(canonical)

    meta = await extractor.extract(url, bm.platform)

    cats = user_categories(db, bm.user_id)
    by_name = {c.name: c for c in cats}
    result = await categorizer.categorize(
        {
            "url": bm.canonical_url, "platform": bm.platform, "title": meta.title,
            "description": meta.description, "raw_text": meta.raw_text, "site_name": meta.site_name,
            "author": meta.author, "hashtags": meta.hashtags, "transcript": meta.transcript,
        },
        [c.name for c in cats] or [categorizer.OTHER],
        correction_examples(db, bm.user_id),
        user_rules(db, bm.user_id),
    )

    bm.title = meta.title
    bm.thumbnail_url = meta.thumbnail_url
    bm.author = meta.author
    bm.site_name = meta.site_name
    bm.raw_text = meta.raw_text
    bm.transcript = meta.transcript
    bm.transcript_source = meta.transcript_source
    needs_audio = bool(meta.is_video and not meta.transcript and transcriber.can_transcribe_audio(meta.duration))
    if meta.transcript:
        bm.transcript_status = "ready"
    elif needs_audio:
        bm.transcript_status = "processing"
    else:
        bm.transcript_status = "none" if meta.is_video else None
    bm.description = result.description or _first_sentence(meta.description or meta.raw_text)
    bm.tags = result.tags or meta.hashtags[:4]
    _set_summary(bm, result.summary, meta.transcript, meta.raw_text)
    _set_recipe(bm, meta.recipe, result, meta.raw_text)
    # Only a recipe read from a recipe website or written out by Claude is trusted to settle the category;
    # the caption reader is a guess.
    if (bm.recipe and bm.recipe_source in ("site", "ai") and RECIPES in by_name
            and result.category == categorizer.OTHER and result.source != "rules"):
        result.category, result.confidence = RECIPES, max(result.confidence, 0.8)  # a parsed recipe settles it
    bm.confidence = result.confidence
    # Don't override a category the user already picked while we were processing.
    if bm.category_id is None:
        cat = by_name.get(result.category) or by_name.get(categorizer.OTHER)
        bm.category_id = cat.id if cat else None
    if result.suggested_new_category and result.suggested_new_category not in by_name:
        bm.suggested_category = result.suggested_new_category
    bm.needs_review = bool(
        result.source != "rules" and (result.confidence < s.llm_min_confidence or bm.suggested_category)
    )
    bm.status = "ready"
    db.commit()
    return needs_audio


async def _transcribe_audio(db: Session, bm: Bookmark) -> None:
    """Slow path: no captions were published, so listen to the audio."""
    text = await asyncio.to_thread(transcriber.whisper_transcribe, bm.url)
    db.refresh(bm)
    if bm.deleted_at:
        return
    bm.transcript = text
    bm.transcript_source = "speech-to-text" if text else None
    bm.transcript_status = "ready" if text else "none"
    if text:
        cats = [c.name for c in user_categories(db, bm.user_id)] or [categorizer.OTHER]
        result = await categorizer.llm_categorize(
            {"url": bm.canonical_url, "platform": bm.platform, "title": bm.title, "raw_text": bm.raw_text,
             "site_name": bm.site_name, "author": bm.author, "transcript": text}, cats, [])
        _set_summary(bm, result.summary if result else None, text)
        if result and result.recipe and bm.recipe_source != "site":
            bm.recipe, bm.recipe_source = result.recipe, "ai"
    db.commit()


def _set_recipe(bm: Bookmark, site_recipe: dict | None, result: categorizer.CategoryResult,
                text: str | None) -> None:
    if site_recipe:
        bm.recipe, bm.recipe_source = site_recipe, "site"
    elif result.recipe:
        bm.recipe, bm.recipe_source = result.recipe, "ai"
    elif not result.llm_used:
        # No Claude: try reading an "Ingredients: ... Method: ..." style caption.
        quick = recipe_from_text(text)
        bm.recipe, bm.recipe_source = quick, ("quick" if quick else None)
    else:
        bm.recipe = bm.recipe_source = None


def _set_summary(bm: Bookmark, ai_summary: str | None, *texts: str | None) -> None:
    if ai_summary:
        bm.summary, bm.summary_source = ai_summary, "ai"
        return
    if bm.summary_source == "ai":
        return
    # A quick summary copies sentences, it can't translate: only use text already in the summary language.
    english = get_settings().summary_language_code == "en"
    quick = None
    for text in texts:
        if text and (not english or mostly_latin(text)):
            quick = extractive_summary(text)
            if quick:
                break
    bm.summary, bm.summary_source = quick, ("quick" if quick else None)


def _first_sentence(text: str | None, limit: int = 160) -> str | None:
    if not text:
        return None
    text = " ".join(text.split())
    for sep in (". ", "! ", "? ", "\n"):
        i = text.find(sep)
        if 0 < i < limit:
            return text[: i + 1].strip()
    return text[:limit].rstrip() + ("…" if len(text) > limit else "")

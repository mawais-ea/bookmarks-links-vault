"""Get the full recipe for a bookmark the person filed under recipes.

Triggered when a bookmark is moved into a recipe category (or "Try again" is tapped), and at
server start for recipe-category bookmarks that never got one. While it runs the bookmark's
recipe_source is "processing"; afterwards it's site | ai | quick, or "none" if nothing usable
was found.
"""

import logging

import httpx
from sqlalchemy import or_, select

from .. import db as db_module
from ..models import Bookmark, Category
from . import categorizer, extractor
from .recipes import parse_jsonld_recipe, recipe_from_text

log = logging.getLogger(__name__)


def is_recipe_category(cat: Category | None) -> bool:
    if cat is None:
        return False
    name = (cat.name or "").lower()
    return "recipe" in name or cat.icon in ("restaurant", "restaurant_menu")


async def find_recipe(bookmark_id: int) -> None:
    db = db_module.SessionLocal()
    try:
        bm = db.get(Bookmark, bookmark_id)
        if not bm or bm.deleted_at or bm.platform == "screenshot":
            return
        try:
            recipe, source = await _find(bm)
        except Exception:
            log.exception("finding recipe for %s failed", bookmark_id)
            recipe, source = None, None
        db.refresh(bm)
        if recipe:
            bm.recipe, bm.recipe_source = recipe, source
        else:
            bm.recipe, bm.recipe_source = None, "none"
        db.commit()
    finally:
        db.close()


async def _find(bm: Bookmark) -> tuple[dict | None, str | None]:
    url = bm.url

    # 1. The page itself may publish the exact recipe (recipe websites).
    if bm.platform == "web":
        try:
            async with extractor._client() as c:
                page_html, _ = await extractor._fetch_html(c, url)
            if page_html and (recipe := parse_jsonld_recipe(page_html)):
                return recipe, "site"
        except httpx.HTTPError as e:
            log.info("recipe page fetch failed for %s: %s", url, e)

    # Gather everything the post says; videos may also have captions we never stored.
    meta = {"url": bm.canonical_url, "title": bm.title, "author": bm.author, "raw_text": bm.raw_text,
            "description": bm.description, "transcript": bm.transcript}
    if bm.platform in extractor.VIDEO_PLATFORMS and not bm.transcript:
        fresh = await extractor.extract(url, bm.platform)
        meta["transcript"] = fresh.transcript
        meta["raw_text"] = meta["raw_text"] or fresh.raw_text

    # 2. Claude writes it out from the caption and captions.
    if recipe := await categorizer.llm_recipe(meta):
        return recipe, "ai"

    # 3. Without Claude: read it from the caption, trusting that it is a recipe.
    text = "\n".join(filter(None, [meta.get("raw_text"), meta.get("description")]))
    if recipe := recipe_from_text(text, assume_recipe=True):
        return recipe, "quick"
    return None, None


async def backfill_recipes() -> None:
    """At start-up: recipe-category bookmarks that never got a recipe get one now."""
    db = db_module.SessionLocal()
    try:
        # An empty JSON recipe may be stored as NULL or as JSON null, so check it in Python.
        ids = [b.id for b in db.scalars(
            select(Bookmark).join(Category, Category.id == Bookmark.category_id).where(
                Bookmark.deleted_at.is_(None), Bookmark.platform != "screenshot",
                or_(Bookmark.recipe_source.is_(None), Bookmark.recipe_source == "processing"),
                or_(Category.name.ilike("%recipe%"), Category.icon.in_(["restaurant", "restaurant_menu"])),
            )).unique() if not b.recipe]
    finally:
        db.close()
    for bookmark_id in ids:
        await find_recipe(bookmark_id)
    if ids:
        log.info("looked for recipes in %d bookmarks filed under recipes", len(ids))

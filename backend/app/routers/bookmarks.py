import time
from datetime import datetime, timezone

import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models import Bookmark, Category, CategoryRule, Correction, User, utcnow
from ..schemas import BookmarkIn, BookmarkList, BookmarkOut, BookmarkPatch
from ..services.pipeline import process_bookmark
from ..services.recipe_job import find_recipe, is_recipe_category
from ..services.screenshots import process_screenshot
from ..services.url_utils import detect_platform, domain_of, extract_url, normalize

router = APIRouter(prefix="/bookmarks", tags=["bookmarks"])

AUTO_RULE_THRESHOLD = 3


def to_out(bm: Bookmark, with_transcript: bool = False) -> BookmarkOut:
    out = BookmarkOut.model_validate(bm)
    if not with_transcript:
        out.transcript = None
    out.category_name = bm.category.name if bm.category else None
    out.tags = list(bm.tags or [])
    return out


def _get_owned(db: Session, user: User, bookmark_id: int) -> Bookmark:
    bm = db.get(Bookmark, bookmark_id)
    if not bm or bm.user_id != user.id or bm.deleted_at:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bookmark not found")
    return bm


@router.post("", response_model=BookmarkOut, status_code=status.HTTP_201_CREATED)
def create_bookmark(body: BookmarkIn, tasks: BackgroundTasks, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    url = extract_url(body.url)
    if not url:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No link found in the shared text")
    canonical = normalize(url)

    existing = db.scalar(select(Bookmark).where(Bookmark.user_id == user.id, Bookmark.canonical_url == canonical))
    if existing:
        # Re-sharing just bumps it to the top (and restores it if it was deleted).
        existing.created_at = utcnow()
        existing.position = time.time()
        existing.deleted_at = None
        existing.is_archived = False
        if body.note:
            existing.note = body.note
        if existing.status == "failed":
            existing.status = "processing"
            tasks.add_task(process_bookmark, existing.id)
        db.commit()
        return to_out(existing)

    bm = Bookmark(
        user_id=user.id, url=url, canonical_url=canonical, platform=detect_platform(canonical),
        title=None, note=body.note, status="processing", tags=[],
    )
    db.add(bm)
    db.commit()
    tasks.add_task(process_bookmark, bm.id)
    return to_out(bm)


def _user_by_key(db: Session, key: str) -> User:
    user = db.scalar(select(User).where(User.api_key == key)) if key else None
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong API key")
    return user


def _save_link_response(bm: BookmarkOut) -> PlainTextResponse:
    return PlainTextResponse(f"Saved ✓ {bm.title or bm.url}")


# Shortcut-friendly saving: everything is in the address, so an iPhone Shortcut (or anything that can
# open a URL) needs no method, headers or body. Answers with plain text for the notification.
save_router = APIRouter(tags=["bookmarks"])


@save_router.get("/s/{key}/{link:path}", response_class=PlainTextResponse)
def save_from_path(key: str, link: str, request: Request, tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """GET /s/<api key>/<the shared link, pasted as-is>. Its own ?query arrives as ours, so put it back."""
    user = _user_by_key(db, key)
    if request.url.query:
        link += "?" + request.url.query
    link = re.sub(r"^(https?):/+(?=[^/])", r"\1://", link)  # proxies may squash "//" in paths
    return _save_link_response(create_bookmark(BookmarkIn(url=link), tasks, user, db))


@save_router.get("/save", response_class=PlainTextResponse)
def save_from_query(url: str, key: str, tasks: BackgroundTasks, note: str | None = None,
                    db: Session = Depends(get_db)):
    """GET /save?key=<api key>&url=<URL-encoded link>."""
    return _save_link_response(create_bookmark(BookmarkIn(url=url, note=note), tasks, _user_by_key(db, key), db))


@router.get("", response_model=BookmarkList)
def list_bookmarks(
    category: str | None = Query(None, description="Category id or name"),
    q: str | None = None,
    since: datetime | None = Query(None, description="Return everything changed after this time, including deletions"),
    platform: str | None = None,
    favorite: bool | None = None,
    archived: bool = False,
    review: bool | None = None,
    include_transcript: bool = False,
    limit: int = Query(200, le=1000),
    offset: int = 0,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    server_time = datetime.now(timezone.utc)
    stmt = select(Bookmark).where(Bookmark.user_id == user.id)

    if since is not None:
        if since.tzinfo is not None:
            since = since.astimezone(timezone.utc)
        stmt = stmt.where(Bookmark.updated_at > since).order_by(Bookmark.updated_at)
    else:
        stmt = stmt.where(Bookmark.deleted_at.is_(None), Bookmark.is_archived.is_(archived))
        stmt = stmt.order_by(Bookmark.position.desc(), Bookmark.created_at.desc())

    if category:
        if category.isdigit():
            stmt = stmt.where(Bookmark.category_id == int(category))
        else:
            stmt = stmt.join(Category, Category.id == Bookmark.category_id).where(Category.name == category)
    if platform:
        stmt = stmt.where(Bookmark.platform == platform)
    if favorite is not None:
        stmt = stmt.where(Bookmark.is_favorite.is_(favorite))
    if review is not None:
        stmt = stmt.where(Bookmark.needs_review.is_(review))
    if q:
        for term in q.split()[:8]:
            like = f"%{term}%"
            stmt = stmt.where(or_(
                Bookmark.title.ilike(like), Bookmark.description.ilike(like), Bookmark.raw_text.ilike(like),
                Bookmark.note.ilike(like), Bookmark.canonical_url.ilike(like), Bookmark.author.ilike(like),
                Bookmark.transcript.ilike(like), Bookmark.summary.ilike(like),
                cast(Bookmark.tags, Text).ilike(like),
            ))

    items = db.scalars(stmt.limit(limit).offset(offset)).unique().all()
    return BookmarkList(items=[to_out(b, include_transcript) for b in items], server_time=server_time)


@router.get("/{bookmark_id}", response_model=BookmarkOut)
def get_bookmark(bookmark_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return to_out(_get_owned(db, user, bookmark_id), with_transcript=True)


@router.patch("/{bookmark_id}", response_model=BookmarkOut)
def update_bookmark(bookmark_id: int, body: BookmarkPatch, tasks: BackgroundTasks,
                    user: User = Depends(current_user), db: Session = Depends(get_db)):
    bm = _get_owned(db, user, bookmark_id)
    data = body.model_dump(exclude_unset=True)

    new_cat: Category | None = None
    if (name := data.pop("category", None)) is not None:
        new_cat = db.scalar(select(Category).where(Category.user_id == user.id, Category.name == name))
        if not new_cat:
            # Accepting a suggested category ("Create category 'Gardening'?") creates it.
            max_order = db.scalar(select(func.max(Category.sort_order)).where(Category.user_id == user.id)) or 0
            new_cat = Category(user_id=user.id, name=name.strip()[:80], sort_order=max_order + 1)
            db.add(new_cat)
            db.flush()
    elif (cid := data.pop("category_id", None)) is not None:
        new_cat = db.get(Category, cid)
        if not new_cat or new_cat.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    data.pop("category_id", None)

    if new_cat and new_cat.id != bm.category_id:
        _record_correction(db, user, bm, new_cat)
        bm.category_id = new_cat.id
        bm.needs_review = False
        if bm.suggested_category == new_cat.name:
            bm.suggested_category = None
        # Filed under recipes by hand: go and get the full recipe, as for links sorted there automatically.
        if is_recipe_category(new_cat) and not bm.recipe and bm.platform != "screenshot":
            bm.recipe_source = "processing"
            tasks.add_task(find_recipe, bm.id)
    if "title" in data:  # rename
        data["title"] = " ".join((data["title"] or "").split())
        if not data["title"]:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The name can't be empty")
    for key, value in data.items():
        setattr(bm, key, value)
    if data.get("needs_review") is False:
        bm.suggested_category = None
    db.commit()
    db.refresh(bm)
    return to_out(bm)


def _record_correction(db: Session, user: User, bm: Bookmark, to: Category) -> None:
    if bm.status == "processing" and bm.category_id is None:
        return  # user picked a category before enrichment finished: not a correction
    db.add(Correction(user_id=user.id, bookmark_id=bm.id, from_category_id=bm.category_id, to_category_id=to.id))
    db.flush()

    # Learn: moving the same domain to the same category N times creates a domain rule.
    domain = domain_of(bm.canonical_url)
    if bm.platform != "web":
        return  # instagram.com etc. span every topic; a domain rule would be wrong
    moved = db.scalars(
        select(Bookmark.canonical_url)
        .join(Correction, Correction.bookmark_id == Bookmark.id)
        .where(Correction.user_id == user.id, Correction.to_category_id == to.id)
    ).all()
    if sum(domain_of(u) == domain for u in set(moved)) >= AUTO_RULE_THRESHOLD:
        exists = db.scalar(select(CategoryRule).where(
            CategoryRule.user_id == user.id, CategoryRule.type == "domain", CategoryRule.pattern == domain))
        if exists:
            exists.category_id = to.id
        else:
            db.add(CategoryRule(user_id=user.id, type="domain", pattern=domain, category_id=to.id))


@router.delete("/{bookmark_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_bookmark(bookmark_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    bm = _get_owned(db, user, bookmark_id)
    bm.deleted_at = utcnow()
    db.commit()


@router.post("/{bookmark_id}/recipe", response_model=BookmarkOut)
def get_recipe(bookmark_id: int, tasks: BackgroundTasks, user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    """Look for the recipe again ("Try again" / "Get recipe" in the app)."""
    bm = _get_owned(db, user, bookmark_id)
    if bm.platform == "screenshot":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Screenshots don't have recipes")
    bm.recipe_source = "processing"
    db.commit()
    tasks.add_task(find_recipe, bm.id)
    return to_out(bm)


@router.post("/{bookmark_id}/refresh", response_model=BookmarkOut)
def refresh_bookmark(bookmark_id: int, tasks: BackgroundTasks, user: User = Depends(current_user),
                     db: Session = Depends(get_db)):
    bm = _get_owned(db, user, bookmark_id)
    bm.status = "processing"
    if bm.platform == "screenshot":  # re-read the image; it stays in Screenshots
        db.commit()
        tasks.add_task(process_screenshot, bm.id)
        return to_out(bm)
    bm.category_id = None
    bm.suggested_category = None
    bm.transcript = bm.transcript_status = bm.transcript_source = None
    bm.summary = bm.summary_source = None
    bm.recipe = bm.recipe_source = None
    db.commit()
    tasks.add_task(process_bookmark, bm.id)
    return to_out(bm)

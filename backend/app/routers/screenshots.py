import time
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..config import get_settings
from ..db import get_db
from ..models import Bookmark, User, utcnow
from ..schemas import BookmarkOut
from ..services import screenshots as shots
from .bookmarks import to_out

router = APIRouter(tags=["screenshots"])


def _save_image(user: User, data: bytes, db: Session, tasks: BackgroundTasks,
                note: str | None = None, taken_at: datetime | None = None) -> Bookmark:
    limit = get_settings().max_upload_mb * 1024 * 1024
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No image received")
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"Image is larger than {get_settings().max_upload_mb} MB")
    try:
        sha, name = shots.store_image(user.id, data)
    except shots.BadImage as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e

    canonical = f"screenshot:{sha}"
    existing = db.scalar(select(Bookmark).where(Bookmark.user_id == user.id, Bookmark.canonical_url == canonical))
    if existing:  # same image saved twice (e.g. app and tray app both noticed it)
        if existing.deleted_at:
            existing.deleted_at = None
            existing.created_at = utcnow()
            existing.position = time.time()
        db.commit()
        return existing

    if taken_at and taken_at.tzinfo is None:
        taken_at = taken_at.replace(tzinfo=timezone.utc)
    url = shots.signed_url(user.id, name)
    bm = Bookmark(
        user_id=user.id, url=url, canonical_url=canonical, platform="screenshot", file_name=name,
        title=shots.default_title(taken_at), thumbnail_url=url, note=note, status="processing", tags=[],
        category_id=shots.screenshots_category(db, user.id).id, created_at=taken_at or utcnow(),
    )
    db.add(bm)
    db.commit()
    tasks.add_task(shots.process_screenshot, bm.id)
    return bm


@router.post("/screenshots", response_model=BookmarkOut, status_code=status.HTTP_201_CREATED)
async def upload_screenshot(
    tasks: BackgroundTasks,
    file: UploadFile = File(...),
    note: str | None = Form(None),
    taken_at: datetime | None = Form(None, description="When the screenshot was taken (file time)"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    data = await file.read(get_settings().max_upload_mb * 1024 * 1024 + 1)
    return to_out(_save_image(user, data, db, tasks, note=note, taken_at=taken_at))


@router.post("/u/{key}", response_class=PlainTextResponse)
async def upload_with_key(key: str, request: Request, tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """iPhone Shortcut upload: POST the image as the request body (Request Body: File) to /u/<api key>.

    No headers or form fields needed. A multipart form with a file field works too.
    """
    user = db.scalar(select(User).where(User.api_key == key)) if key else None
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong API key")
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        form = await request.form()
        files = [v for v in form.values() if hasattr(v, "read")]
        data = await files[0].read() if files else b""
    else:
        data = await request.body()
    bm = _save_image(user, data, db, tasks)
    return PlainTextResponse(f"Saved ✓ {bm.title}")


@router.get("/files/{user_id}/{name}", include_in_schema=False)
def get_file(user_id: int, name: str, sig: str = Query("")):
    if "/" in name or "\\" in name or ".." in name or not shots.verify(user_id, name, sig):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    path = shots.file_path(user_id, name)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=31536000, immutable"})

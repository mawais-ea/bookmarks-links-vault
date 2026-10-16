import csv
import html
import io
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..auth import create_token, current_user, hash_password, new_api_key, verify_password
from ..config import get_settings
from ..db import get_db
from ..models import Bookmark, User, utcnow
from ..schemas import LoginIn, RegisterIn, TokenOut
from ..services.pipeline import seed_default_categories

router = APIRouter(tags=["account"])


@router.post("/auth/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    if not get_settings().allow_registration:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Registration is closed")
    email = body.email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = User(email=email, password_hash=hash_password(body.password), api_key=new_api_key())
    db.add(user)
    db.flush()
    seed_default_categories(db, user)
    db.commit()
    return TokenOut(access_token=create_token(user.id), api_key=user.api_key)


@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
    return TokenOut(access_token=create_token(user.id), api_key=user.api_key)


class MeOut(BaseModel):
    id: int
    email: str
    api_key: str


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(current_user)):
    return MeOut(id=user.id, email=user.email, api_key=user.api_key)


@router.post("/me/rotate-key", response_model=MeOut)
def rotate_key(user: User = Depends(current_user), db: Session = Depends(get_db)):
    user.api_key = new_api_key()
    db.commit()
    return MeOut(id=user.id, email=user.email, api_key=user.api_key)


@router.delete("/me/bookmarks", status_code=status.HTTP_204_NO_CONTENT)
def delete_all_bookmarks(user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.execute(update(Bookmark).where(Bookmark.user_id == user.id, Bookmark.deleted_at.is_(None))
               .values(deleted_at=utcnow()))
    db.commit()


@router.get("/export")
def export(fmt: str = Query("json", alias="format", pattern="^(json|csv|html)$"),
           user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Bookmark).where(Bookmark.user_id == user.id, Bookmark.deleted_at.is_(None))
                      .order_by(Bookmark.position.desc(), Bookmark.created_at.desc())).unique().all()
    records = [{
        "url": b.url, "title": b.title, "description": b.description, "category": b.category.name if b.category else None,
        "platform": b.platform, "tags": b.tags or [], "note": b.note, "is_favorite": b.is_favorite,
        "summary": b.summary,
        "recipe": b.recipe,
        "transcript": b.transcript,
        "created_at": b.created_at.isoformat(),
    } for b in rows]

    if fmt == "json":
        return Response(json.dumps(records, ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="linkvault.json"'})
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(records[0].keys()) if records else ["url"])
        w.writeheader()
        for r in records:
            w.writerow({**r, "tags": ", ".join(r["tags"])})
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="linkvault.csv"'})

    # Netscape bookmark file: importable by every browser.
    by_cat: dict[str, list[dict]] = {}
    for r in records:
        by_cat.setdefault(r["category"] or "Other", []).append(r)
    lines = ["<!DOCTYPE NETSCAPE-Bookmark-file-1>", '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
             "<TITLE>LinkVault</TITLE>", "<H1>LinkVault</H1>", "<DL><p>"]
    for cat, items in by_cat.items():
        lines.append(f"<DT><H3>{html.escape(cat)}</H3>")
        lines.append("<DL><p>")
        for r in items:
            lines.append(f'<DT><A HREF="{html.escape(r["url"], quote=True)}">{html.escape(r["title"] or r["url"])}</A>')
            if r["description"]:
                lines.append(f"<DD>{html.escape(r['description'])}")
        lines.append("</DL><p>")
    lines.append("</DL><p>")
    return Response("\n".join(lines), media_type="text/html",
                    headers={"Content-Disposition": 'attachment; filename="linkvault-bookmarks.html"'})

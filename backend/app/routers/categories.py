from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models import Bookmark, Category, CategoryRule, User, utcnow
from ..schemas import CategoryIn, CategoryOut, CategoryPatch
from ..services.categorizer import OTHER

router = APIRouter(tags=["categories"])


def _owned(db: Session, user: User, category_id: int) -> Category:
    cat = db.get(Category, category_id)
    if not cat or cat.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    return cat


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(user: User = Depends(current_user), db: Session = Depends(get_db)):
    counts = dict(db.execute(
        select(Bookmark.category_id, func.count())
        .where(Bookmark.user_id == user.id, Bookmark.deleted_at.is_(None), Bookmark.is_archived.is_(False))
        .group_by(Bookmark.category_id)
    ).all())
    cats = db.scalars(select(Category).where(Category.user_id == user.id).order_by(Category.sort_order, Category.id))
    out = []
    for c in cats:
        item = CategoryOut.model_validate(c)
        item.count = counts.get(c.id, 0)
        out.append(item)
    return out


@router.post("/categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
def create_category(body: CategoryIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if db.scalar(select(Category).where(Category.user_id == user.id, Category.name == body.name)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A category with that name already exists")
    cat = Category(user_id=user.id, **body.model_dump())
    db.add(cat)
    db.commit()
    return CategoryOut.model_validate(cat)


@router.patch("/categories/{category_id}", response_model=CategoryOut)
def update_category(category_id: int, body: CategoryPatch, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    cat = _owned(db, user, category_id)
    data = body.model_dump(exclude_unset=True)
    if cat.name == OTHER and data.get("name", OTHER) != OTHER:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f'"{OTHER}" cannot be renamed')
    for k, v in data.items():
        setattr(cat, k, v)
    # Bump bookmarks so syncing devices pick up the new category name.
    db.execute(update(Bookmark).where(Bookmark.category_id == cat.id).values(updated_at=utcnow()))
    db.commit()
    return CategoryOut.model_validate(cat)


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(category_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    cat = _owned(db, user, category_id)
    if cat.name == OTHER:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f'"{OTHER}" cannot be deleted')
    other = db.scalar(select(Category).where(Category.user_id == user.id, Category.name == OTHER))
    db.execute(update(Bookmark).where(Bookmark.category_id == cat.id)
               .values(category_id=other.id if other else None, updated_at=utcnow()))
    db.delete(cat)
    db.commit()


class ReorderIn(BaseModel):
    ids: list[int] = Field(min_length=1)


@router.post("/categories/reorder", response_model=list[CategoryOut])
def reorder_categories(body: ReorderIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Set the category order in one go: `ids` from first to last (categories left out keep their place after)."""
    cats = {c.id: c for c in db.scalars(select(Category).where(Category.user_id == user.id))}
    if any(i not in cats for i in body.ids):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    rest = sorted((c for i, c in cats.items() if i not in set(body.ids)), key=lambda c: c.sort_order)
    for order, cat in enumerate([cats[i] for i in body.ids] + rest):
        cat.sort_order = order
    db.commit()
    return list_categories(user, db)


# ---------------------------------------------------------------- rules


class RuleIn(BaseModel):
    type: str = Field(pattern="^(domain|keyword)$")
    pattern: str = Field(min_length=2, max_length=255)
    category_id: int


class RuleOut(RuleIn):
    id: int
    category_name: str


@router.get("/rules", response_model=list[RuleOut])
def list_rules(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(CategoryRule, Category.name).join(Category, Category.id == CategoryRule.category_id)
        .where(CategoryRule.user_id == user.id).order_by(CategoryRule.id)
    ).all()
    return [RuleOut(id=r.id, type=r.type, pattern=r.pattern, category_id=r.category_id, category_name=n)
            for r, n in rows]


@router.post("/rules", response_model=RuleOut, status_code=status.HTTP_201_CREATED)
def create_rule(body: RuleIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    cat = _owned(db, user, body.category_id)
    rule = CategoryRule(user_id=user.id, type=body.type, pattern=body.pattern.strip().lower(), category_id=cat.id)
    db.add(rule)
    db.commit()
    return RuleOut(id=rule.id, type=rule.type, pattern=rule.pattern, category_id=cat.id, category_name=cat.name)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rule = db.get(CategoryRule, rule_id)
    if not rule or rule.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rule not found")
    db.delete(rule)
    db.commit()

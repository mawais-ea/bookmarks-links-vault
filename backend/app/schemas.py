from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RegisterIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=200)


class LoginIn(RegisterIn):
    pass


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    api_key: str


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    icon: str = "folder"
    color: str = "#607D8B"
    sort_order: int = 0


class CategoryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    icon: str | None = None
    color: str | None = None
    sort_order: int | None = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    icon: str
    color: str
    sort_order: int
    is_default: bool
    count: int = 0


class BookmarkIn(BaseModel):
    url: str = Field(min_length=4, max_length=4096)
    note: str | None = None


class BookmarkPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    note: str | None = None
    category_id: int | None = None
    category: str | None = None  # move by name (e.g. accepting a suggested new category)
    tags: list[str] | None = None
    is_favorite: bool | None = None
    is_archived: bool | None = None
    needs_review: bool | None = None
    position: float | None = None


class BookmarkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    canonical_url: str
    platform: str
    title: str | None
    description: str | None
    thumbnail_url: str | None
    author: str | None
    site_name: str | None
    note: str | None
    category_id: int | None
    category_name: str | None = None
    suggested_category: str | None
    tags: list[str] = []
    confidence: float | None
    needs_review: bool
    status: str
    summary: str | None = None
    summary_source: str | None = None
    recipe: dict | None = None
    recipe_source: str | None = None
    transcript_status: str | None = None
    transcript_source: str | None = None
    transcript: str | None = None  # full text only from GET /bookmarks/{id} (or ?include_transcript=true)
    is_favorite: bool
    is_archived: bool
    position: float | None = None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None

    # SQLite drops timezone info; everything is stored as UTC, so restore it.
    @field_validator("created_at", "updated_at", "deleted_at")
    @classmethod
    def _as_utc(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class BookmarkList(BaseModel):
    items: list[BookmarkOut]
    server_time: datetime

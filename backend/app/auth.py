"""Password hashing, JWT login tokens, and per-user API keys.

Requests authenticate with either `Authorization: Bearer <jwt>` (the app)
or `X-API-Key: <key>` (Apple Shortcut, browser extension, tray app).
"""

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User

_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, digest_b64 = stored.split("$")
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(digest_b64)
    except ValueError:
        return False
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return hmac.compare_digest(digest, expected)


def new_api_key() -> str:
    return "lv_" + secrets.token_urlsafe(32)


def create_token(user_id: int) -> str:
    s = get_settings()
    payload = {"sub": str(user_id), "exp": datetime.now(timezone.utc) + timedelta(days=s.token_expire_days)}
    return jwt.encode(payload, s.secret_key, algorithm="HS256")


def _unauthorized() -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})


def current_user(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if x_api_key:
        user = db.scalar(select(User).where(User.api_key == x_api_key))
        if user:
            return user
        raise _unauthorized()
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        try:
            payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
            user = db.get(User, int(payload["sub"]))
        except (jwt.PyJWTError, KeyError, ValueError):
            user = None
        if user:
            return user
    raise _unauthorized()

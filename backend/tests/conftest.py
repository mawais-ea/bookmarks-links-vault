import os
import tempfile

# Point the app at a throwaway database and keep tests offline before anything imports it.
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["LLM_ENABLED"] = "false"
os.environ["ENABLE_YTDLP"] = "false"
os.environ["ENABLE_WHISPER"] = "false"
os.environ["FILES_DIR"] = f"{_tmp}/uploads"
os.environ["ALLOW_REGISTRATION"] = "true"  # tests create their own users
os.environ["SECRET_KEY"] = "test-secret-key-that-is-at-least-32-bytes"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services import extractor  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def fake_extract(monkeypatch):
    """Replace network extraction with canned metadata keyed by URL substring."""
    pages: dict[str, extractor.Metadata] = {}

    async def _extract(url, platform):
        for key, meta in pages.items():
            if key in url:
                return meta
        return extractor.Metadata(title="example.com")

    monkeypatch.setattr(extractor, "extract", _extract)
    return pages


@pytest.fixture
def client():
    with TestClient(app) as c:
        r = c.post("/auth/register", json={"email": "me@example.com", "password": "correct-horse"})
        assert r.status_code == 201, r.text
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        c.api_key = r.json()["api_key"]
        yield c

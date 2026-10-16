import asyncio
import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import init_db
from .routers import account, bookmarks, categories, screenshots
from .services.recipe_job import backfill_recipes
from .services.screenshots import ensure_category_for_all_users

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class _HideApiKeys(logging.Filter):
    """The Shortcut save links carry the API key in the address; keep it out of the access log."""
    _pattern = re.compile(r"(/[su]/|[?&]key=)lv_[A-Za-z0-9_\-]+")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(self._pattern.sub(r"\1lv_***", a) if isinstance(a, str) else a for a in record.args)
        return True


logging.getLogger("uvicorn.access").addFilter(_HideApiKeys())


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    ensure_category_for_all_users()
    backfill = asyncio.create_task(backfill_recipes())  # recipe links that never got a recipe
    yield
    backfill.cancel()


app = FastAPI(title="LinkVault API", version="0.1.0", lifespan=lifespan)

# The browser extension calls the API from chrome-extension:// origins.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(account.router)
app.include_router(bookmarks.router)
app.include_router(bookmarks.save_router)
app.include_router(categories.router)
app.include_router(screenshots.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}

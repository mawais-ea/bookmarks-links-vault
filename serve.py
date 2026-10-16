"""Run the LinkVault server with the vault web page at /app.

    .venv\\Scripts\\python -m uvicorn serve:app --host 127.0.0.1 --port 8000

Everything is on one address, so one Cloudflare tunnel covers it all:
  /            the API (iPhone shortcuts, browser extension, tray app, PC app)
  /app         your vault as a web page: open it in Safari and "Add to Home Screen"

The page runs the same Flet app as the PC; each browser signs in separately.
"""

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "app"))      # the Flet app: main.py, views/...
sys.path.insert(0, str(ROOT / "backend"))  # the API: package "app"
os.chdir(ROOT / "backend")                 # .env, linkvault.db and uploads/ live here

import flet_web.fastapi as flet_fastapi  # noqa: E402

from app.main import app  # noqa: E402  (the API)
import main as vault  # noqa: E402  (the Flet app; "main" here is app/main.py)

web = flet_fastapi.app(
    vault.web_main,
    assets_dir=str(ROOT / "app" / "assets"),
    app_name="LinkVault",
    app_short_name="LinkVault",
    app_description="Your saved links, recipes and screenshots",
)
app.mount("/app", web)

# Mounted apps don't get startup/shutdown events, so start Flet's session manager with the API's.
_api_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _lifespan(a):
    async with _api_lifespan(a):
        await flet_fastapi.app_manager.start()
        try:
            yield
        finally:
            await flet_fastapi.app_manager.shutdown()


app.router.lifespan_context = _lifespan

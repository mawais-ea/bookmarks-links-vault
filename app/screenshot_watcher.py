"""Finds new screenshots on Windows.

Windows 11 saves screenshots to Pictures\\Screenshots (Win+PrtScn, and the Snipping Tool /
Win+Shift+S with its default "automatically save" setting). With OneDrive backup on, that
folder lives under OneDrive\\Pictures\\Screenshots. We watch every one of these that exists.

Only screenshots taken after the feature was switched on are uploaded: the cursor starts at
"now", so years of old screenshots are never sent.
"""

import os
import time
from pathlib import Path

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
SETTLE_SECONDS = 1.5  # a file this fresh may still be being written


def screenshot_folders() -> list[Path]:
    home = Path.home()
    candidates = [home / "Pictures" / "Screenshots", home / "OneDrive" / "Pictures" / "Screenshots"]
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if os.environ.get(var):
            candidates.append(Path(os.environ[var]) / "Pictures" / "Screenshots")
    seen, out = set(), []
    for c in candidates:
        try:
            key = c.resolve()
        except OSError:
            continue
        if key not in seen and c.is_dir():
            seen.add(key)
            out.append(c)
    return out


def new_screenshots(folders: list[Path], since: float, now: float | None = None) -> list[Path]:
    """Images modified after `since` that have finished being written, oldest first."""
    now = now or time.time()
    found = []
    for folder in folders:
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for e in entries:
            if not e.is_file() or Path(e.name).suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            try:
                mtime = e.stat().st_mtime
            except OSError:
                continue
            if since < mtime <= now - SETTLE_SECONDS:
                found.append((mtime, Path(e.path)))
    return [p for _, p in sorted(found)]

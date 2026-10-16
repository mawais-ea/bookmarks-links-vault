"""LinkVault tray app for Windows.

Press Ctrl+Shift+S anywhere to save the link on the clipboard, or use the tray menu.
New screenshots (Pictures/Screenshots, including the OneDrive one) are uploaded to the
Screenshots category automatically, even when the LinkVault app is closed.

Setup:
    pip install pystray pillow pyperclip keyboard httpx
    python windows_tray.py --server https://your-server --api-key lv_xxx   # first run saves config
    python windows_tray.py                                                 # later runs

Start with Windows: press Win+R, type `shell:startup`, and put a shortcut to
`pythonw windows_tray.py` in that folder.
"""

import argparse
import json
import os
import re
import threading
import time
from pathlib import Path

import httpx
import keyboard
import pyperclip
import pystray
from PIL import Image, ImageDraw

CONFIG = Path.home() / ".linkvault" / "tray.json"
URL_RE = re.compile(r"https?://\S+")
HOTKEY = "ctrl+shift+s"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def screenshot_folders() -> list[Path]:
    home = Path.home()
    candidates = [home / "Pictures" / "Screenshots", home / "OneDrive" / "Pictures" / "Screenshots"]
    candidates += [Path(os.environ[v]) / "Pictures" / "Screenshots"
                   for v in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial") if os.environ.get(v)]
    out = []
    for c in candidates:
        if c.is_dir() and all(c.resolve() != o.resolve() for o in out):
            out.append(c)
    return out


def load_config(args) -> dict:
    cfg = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    if args.server:
        cfg["server"] = args.server.rstrip("/")
    if args.api_key:
        cfg["api_key"] = args.api_key
    if args.server or args.api_key:
        save_config(cfg)
    if not cfg.get("server") or not cfg.get("api_key"):
        raise SystemExit("Run once with --server and --api-key (copy the key from LinkVault > Settings).")
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2))


def icon_image() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((6, 4, 58, 60), radius=10, fill=(63, 81, 181))
    d.polygon([(20, 4), (44, 4), (44, 40), (32, 30), (20, 40)], fill=(255, 255, 255))
    return img


class Tray:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.icon = pystray.Icon("LinkVault", icon_image(), "LinkVault (Ctrl+Shift+S)", menu=pystray.Menu(
            pystray.MenuItem("Save link from clipboard", lambda: self.save_clipboard(), default=True),
            pystray.MenuItem("Quit", lambda: self.icon.stop()),
        ))

    def notify(self, msg: str) -> None:
        try:
            self.icon.notify(msg, "LinkVault")
        except Exception:
            print(msg)

    def save_clipboard(self) -> None:
        threading.Thread(target=self._save, daemon=True).start()

    def _save(self) -> None:
        text = (pyperclip.paste() or "").strip()
        m = URL_RE.search(text)
        if not m:
            self.notify("No link on the clipboard")
            return
        try:
            r = httpx.post(f"{self.cfg['server']}/bookmarks", json={"url": m.group(0)},
                           headers={"X-API-Key": self.cfg["api_key"]}, timeout=15)
            r.raise_for_status()
        except httpx.HTTPError as e:
            self.notify(f"Couldn't save: {e}")
            return
        self.notify(f"Saved ✓ {m.group(0)[:60]}")

    def watch_screenshots(self) -> None:
        """Upload screenshots taken after the tray app was first started (never older ones)."""
        cursor = float(self.cfg.setdefault("screenshot_cursor", time.time()))
        save_config(self.cfg)
        while True:
            now = time.time()
            new = sorted(
                (p.stat().st_mtime, p) for folder in screenshot_folders() for p in folder.iterdir()
                if p.suffix.lower() in IMAGE_EXTENSIONS and cursor < p.stat().st_mtime <= now - 1.5
            )
            for mtime, path in new:
                try:
                    r = httpx.post(f"{self.cfg['server']}/screenshots", headers={"X-API-Key": self.cfg["api_key"]},
                                   files={"file": (path.name, path.read_bytes())},
                                   data={"taken_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mtime))},
                                   timeout=60)
                except (httpx.HTTPError, OSError):
                    break  # offline or file busy: retry next round
                if r.status_code < 300:
                    self.notify(f"Screenshot saved ✓ {path.name}")
                cursor = mtime
                self.cfg["screenshot_cursor"] = cursor
                save_config(self.cfg)
            time.sleep(3)

    def run(self) -> None:
        keyboard.add_hotkey(HOTKEY, self.save_clipboard)
        threading.Thread(target=self.watch_screenshots, daemon=True).start()
        self.icon.run()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--server")
    p.add_argument("--api-key")
    Tray(load_config(p.parse_args())).run()

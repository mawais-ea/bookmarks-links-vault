"""LinkVault app (Windows, Android, iOS) built with Flet."""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import flet as ft

from api_client import ApiClient, ApiError, OfflineError
from local_db import LocalDB
import screenshot_watcher
from share_handler import ClipboardWatcher, looks_like_url, url_from_route
from sync import SyncService
from views import common, detail, home, login, review, search, settings

log = logging.getLogger("linkvault")

TABS = ["/", "/search", "/review", "/settings"]


def data_dir() -> Path:
    # `flet build` sets FLET_APP_STORAGE_DATA to the app's private writable folder on every platform.
    d = os.getenv("FLET_APP_STORAGE_DATA")
    return Path(d) if d else Path.home() / ".linkvault"


class LinkVaultApp:
    def __init__(self, page: ft.Page, web_api: str | None = None):
        """`web_api` set = running as a web page served by the server itself (phones use it via Safari).

        A web session keeps nothing on the server between visits: its cache lives in memory and
        the login is remembered in the visitor's own browser, so each phone signs in for itself.
        """
        self.page = page
        self.web = web_api is not None
        self.db = LocalDB(":memory:" if self.web else data_dir() / "linkvault.db")
        if self.web:
            self.db.set("server_url", web_api)
            self.prefs = ft.SharedPreferences()  # the browser's localStorage
        self.api = ApiClient(self.db.get("server_url"), self.db.get("token"))
        # Images are loaded by the device showing the app, so in a browser they need the public address.
        common.SERVER = public_origin(page.url) if self.web else self.db.get("server_url")
        if self.web:
            log.debug("web session: page.url=%r -> image base %r", page.url, common.SERVER)
        self.sync = SyncService(self.api, self.db)
        self.history: list[str] = []
        self.search_state: dict = {}
        self._pending_after_render: list = []
        self._view_cache: dict[str, tuple] = {}
        self._poll_task: asyncio.Task | None = None
        self._syncing = False
        # Services must be created inside main() so they register with this page.
        self.clipboard = ft.Clipboard()
        self.launcher = ft.UrlLauncher()
        self.sharer = ft.Share()
        self.storage = ft.StoragePaths()
        self.file_picker = ft.FilePicker()
        self._watch_task: asyncio.Task | None = None
        self.clip_watch = ClipboardWatcher(self.db.get, self.db.set)

    # ------------------------------------------------------------ helpers

    @property
    def logged_in(self) -> bool:
        return bool(self.db.get("token"))

    def run(self, coro) -> None:
        """Fire-and-forget a coroutine from a sync callback (button clicks etc.)."""
        async def runner():
            await coro
        self.page.run_task(runner)

    def after_render(self, coro_fn) -> None:
        self._pending_after_render.append(coro_fn)

    def toast(self, message: str, action: str | None = None, on_action=None) -> None:
        self.page.show_dialog(ft.SnackBar(ft.Text(message), action=action, on_action=on_action,
                                          duration=ft.Duration(seconds=4)))

    def default_server(self) -> str:
        # The Android emulator reaches the host machine at 10.0.2.2.
        return "http://10.0.2.2:8000" if self.page.platform == ft.PagePlatform.ANDROID else "http://127.0.0.1:8000"

    def apply_theme(self) -> None:
        mode = self.db.get("theme", "system")
        self.page.theme_mode = {"light": ft.ThemeMode.LIGHT, "dark": ft.ThemeMode.DARK}.get(mode, ft.ThemeMode.SYSTEM)
        self.page.update()

    def nav_bar(self, index: int) -> ft.NavigationBar:
        review_count = len(self.db.bookmarks(review=True, limit=99))
        return ft.NavigationBar(
            selected_index=index,
            on_change=lambda e: self.go(TABS[e.control.selected_index]),
            destinations=[
                ft.NavigationBarDestination(icon=ft.Icons.HOME_OUTLINED, selected_icon=ft.Icons.HOME, label="Home"),
                ft.NavigationBarDestination(icon=ft.Icons.SEARCH, label="Search"),
                ft.NavigationBarDestination(
                    icon=ft.Icon(ft.Icons.INBOX_OUTLINED, badge=str(review_count) if review_count else None),
                    selected_icon=ft.Icons.INBOX, label="Review"),
                ft.NavigationBarDestination(icon=ft.Icons.SETTINGS_OUTLINED, selected_icon=ft.Icons.SETTINGS,
                                            label="Settings"),
            ],
        )

    def sync_button(self) -> ft.Control:
        if self._syncing:
            return ft.Container(ft.ProgressRing(width=18, height=18, stroke_width=2), padding=14)
        return ft.IconButton(ft.Icons.SYNC, tooltip="Sync", on_click=lambda: self.run(self.do_sync()))

    # ------------------------------------------------------------ navigation

    def _build(self, route: str) -> ft.View:
        if route == "/login":
            return login.build(self)
        if route == "/search":
            return search.build(self)
        if route == "/review":
            return review.build(self)
        if route == "/settings":
            return settings.build(self)
        if route.startswith("/bookmark/"):
            return detail.build(self, route)
        if route.startswith("/category/") or route in ("/favorites", "/archive"):
            return home.build_list(self, route)
        return home.build(self)

    def _build_cached(self, route: str) -> ft.View:
        """Detail screens are rebuilt only when their bookmark changed, so a background
        sync doesn't wipe what you're typing there."""
        if not route.startswith("/bookmark/"):
            return self._build(route)
        bm = self.db.bookmark(int(route.rsplit("/", 1)[-1])) or {}
        fingerprint = (bm.get("updated_at"), bm.get("status"), bm.get("transcript_status"),
                       bm.get("summary"), bm.get("category_name"), bool(bm.get("recipe")))
        cached = self._view_cache.get(route)
        if cached and cached[0] == fingerprint:
            return cached[1]
        view = self._build(route)
        self._view_cache[route] = (fingerprint, view)
        return view

    def render(self) -> None:
        if not self.history:
            self.history = ["/" if self.logged_in else "/login"]
        self.page.views.clear()
        self.page.views.extend(self._build_cached(r) for r in self.history)
        self.page.update()
        pending, self._pending_after_render = self._pending_after_render, []
        for fn in pending:
            self.run(fn())

    def go(self, route: str) -> None:
        if route in TABS or route == "/login":
            self.history = [route]
        elif not self.history or self.history[-1] != route:
            self.history.append(route)
            self._view_cache.pop(route, None)
        self.render()

    def back(self) -> None:
        if len(self.history) > 1:
            self.history.pop()
        self.render()

    def refresh(self) -> None:
        """Rebuild the current screens from the local cache."""
        self.history = [r for r in self.history if not r.startswith("/bookmark/")
                        or self.db.bookmark(int(r.rsplit("/", 1)[-1]))] or ["/"]
        self.render()

    async def on_view_pop(self, e) -> None:
        self.back()

    async def on_route_change(self, e) -> None:
        # Deep links (linkvault://save?url=...) arrive here from the share intent or Shortcuts.
        shared = url_from_route(self.page.route)
        if shared:
            await self.save_link(shared)

    # ------------------------------------------------------------ account

    async def restore_web_login(self) -> None:
        """Web: pick up the login this browser saved last time."""
        token = await self.prefs.get("lv_token")
        if token:
            self.db.set("token", token)
            self.db.set("email", await self.prefs.get("lv_email") or "")
            self.db.set("api_key", await self.prefs.get("lv_api_key") or "")
            self.api.configure(token=token)

    async def on_logged_in(self, server: str, email: str, res: dict) -> None:
        if self.web:
            server = self.db.get("server_url")  # fixed: the page and the API live on the same server
        self.db.clear_account()
        self.db.set("server_url", server)
        self.db.set("email", email)
        self.db.set("token", res["access_token"])
        self.db.set("api_key", res["api_key"])
        self.api.configure(base_url=server, token=res["access_token"])
        if self.web:
            await self.prefs.set("lv_token", res["access_token"])
            await self.prefs.set("lv_email", email)
            await self.prefs.set("lv_api_key", res["api_key"])
        else:
            common.SERVER = server
        self.go("/")
        await self.do_sync()
        self.start_screenshot_watcher()

    def logout(self) -> None:
        server = self.db.get("server_url")
        self.db.clear_account()
        self.api.configure(token="")
        if self.web:
            self.db.set("server_url", server)

            async def forget():
                for k in ("lv_token", "lv_email", "lv_api_key"):
                    await self.prefs.remove(k)
            self.run(forget())
        self.go("/login")

    # ------------------------------------------------------------ data actions

    async def do_sync(self, full: bool = False) -> None:
        if not self.logged_in or self._syncing:
            return
        self._syncing = True
        self.refresh()
        still_processing = False
        try:
            if full:
                await self.sync.full_resync()
            else:
                still_processing = await self.sync.sync()
        except OfflineError:
            self.toast("Offline: showing saved copy")
        except ApiError as e:
            if e.status == 401:
                self.toast("Session expired, please log in again")
                self.logout()
                return
            self.toast(f"Sync failed: {e.detail}")
        finally:
            self._syncing = False
        self.refresh()
        if still_processing:
            self._schedule_poll()

    def _schedule_poll(self) -> None:
        """While links are still being read on the server, check back a few times."""
        if self._poll_task and not self._poll_task.done():
            return

        async def poll():
            # Usually ready within seconds; speech-to-text (if enabled) can take minutes.
            for delay in (3, 5, 8, 13, 20, 30, 30, 45, 60, 60, 90, 120, 120):
                await asyncio.sleep(delay)
                try:
                    if not await self.sync.sync():
                        break
                except (OfflineError, ApiError):
                    break
                finally:
                    self.refresh()
            self.refresh()

        self._poll_task = asyncio.create_task(poll())

    async def save_link(self, text: str) -> None:
        if not self.logged_in:
            self.toast("Log in first, then share the link again")
            return
        try:
            bm, online = await self.sync.save(text)
        except ApiError as e:
            self.toast(e.detail)
            return
        self.toast("Saved ✓" if online else "Saved ✓ (will upload when online)")
        self.refresh()
        if online:
            self._schedule_poll()

    async def save_from_clipboard(self) -> None:
        clip = await self.clipboard.get()
        if looks_like_url(clip):
            await self.save_link(clip.strip())
            return
        image = await self.clipboard.get_image()  # e.g. Win+Shift+S puts the snip on the clipboard
        if image:
            await self.upload_image(image, "clipboard.png")
        else:
            self.toast("No link or image on the clipboard")

    # ------------------------------------------------------------ screenshots

    @property
    def is_windows(self) -> bool:
        """The Windows desktop app (not a browser on Windows): the only place that watches folders."""
        return self.page.platform == ft.PagePlatform.WINDOWS and not self.web

    async def upload_image(self, data: bytes, filename: str, taken_at: float | None = None,
                           quiet: bool = False) -> bool:
        if not self.logged_in:
            return False
        when = datetime.fromtimestamp(taken_at, timezone.utc).isoformat() if taken_at else None
        try:
            bm = await self.api.upload_screenshot(data, filename, when)
        except OfflineError:
            if not quiet:
                self.toast("You're offline; screenshots need a connection to upload")
            raise
        except ApiError as e:
            self.toast(f"Couldn't save {filename}: {e.detail}")
            return False
        self.db.upsert_bookmarks([bm])
        self.db.replace_categories(await self.api.categories())
        self.toast("Screenshot saved ✓")
        self.refresh()
        self._schedule_poll()
        return True

    async def pick_screenshots(self) -> None:
        files = await self.file_picker.pick_files(
            dialog_title="Add screenshots", file_type=ft.FilePickerFileType.IMAGE, allow_multiple=True,
            with_data=not self.is_windows)  # phones hand over bytes; desktop gives a path
        for f in files or []:
            try:
                data = f.bytes if f.bytes else Path(f.path).read_bytes()
                await self.upload_image(data, f.name)
            except OfflineError:
                return
            except OSError as e:
                self.toast(f"Couldn't read {f.name}: {e}")

    def auto_screenshots_enabled(self) -> bool:
        return self.is_windows and self.db.get("auto_screenshots", "1") == "1"

    def set_auto_screenshots(self, on: bool) -> None:
        self.db.set("auto_screenshots", "1" if on else "0")
        if on:
            self.db.set("screenshot_cursor", str(time.time()))  # only screenshots from now on
            self.start_screenshot_watcher()
        elif self._watch_task:
            self._watch_task.cancel()

    def start_screenshot_watcher(self) -> None:
        """Upload new screenshots from the Windows Screenshots folder while the app is open."""
        if not self.auto_screenshots_enabled() or (self._watch_task and not self._watch_task.done()):
            return
        if not self.db.get("screenshot_cursor"):
            self.db.set("screenshot_cursor", str(time.time()))  # never upload old screenshots

        async def watch():
            while self.auto_screenshots_enabled() and self.logged_in:
                cursor = float(self.db.get("screenshot_cursor") or time.time())
                for path in screenshot_watcher.new_screenshots(screenshot_watcher.screenshot_folders(), cursor):
                    try:
                        mtime = path.stat().st_mtime
                        await self.upload_image(path.read_bytes(), path.name, taken_at=mtime, quiet=True)
                    except OfflineError:
                        break  # try again next round
                    except OSError:
                        mtime = cursor  # vanished or locked; skip it
                    self.db.set("screenshot_cursor", str(max(mtime, cursor)))
                await asyncio.sleep(3)

        self._watch_task = asyncio.create_task(watch())

    async def check_clipboard(self) -> None:
        if self.web:
            return  # browsers ask permission for every clipboard read; don't nag
        """Windows-style 'share': offer to save a freshly copied link when the app gains focus."""
        if not self.logged_in:
            return
        try:
            clip = await self.clipboard.get()
        except Exception:
            return
        url = self.clip_watch.new_url(clip)
        if url and not self.db.bookmarks(q=url, limit=1):
            short = url if len(url) < 50 else url[:47] + "…"
            self.toast(f"Save this link? {short}", action="Save", on_action=lambda: self.run(self.save_link(url)))

    async def update_bookmark(self, bookmark_id: int, **changes) -> None:
        try:
            bm = await self.api.update_bookmark(bookmark_id, **changes)
        except OfflineError:
            self.toast("You're offline; changes need a connection")
            return
        except ApiError as e:
            self.toast(e.detail)
            return
        self.db.upsert_bookmarks([bm])
        if "category" in changes:
            self.db.replace_categories(await self.api.categories())
        self.refresh()
        if bm.get("recipe_source") == "processing":  # moved into recipes: the server is fetching it
            self.toast("Getting the recipe…")
            self._schedule_poll()

    # ------------------------------------------------------------ drag to reorder

    @staticmethod
    def position_between(above: float | None, below: float | None) -> float | None:
        """Positions sort high to low, so a card dropped between two others gets the midpoint."""
        if above is None and below is None:
            return None
        if above is None:
            return below + 1.0
        if below is None:
            return above - 1.0
        return (above + below) / 2

    def move_bookmark(self, items: list[dict], old_index: int, new_index: int) -> None:
        """Called when a card is dropped in a new place in a list (Recent, a category...)."""
        if old_index == new_index or not (0 <= old_index < len(items)):
            return
        items = list(items)
        moved = items.pop(old_index)
        new_index = max(0, min(new_index, len(items)))
        items.insert(new_index, moved)
        if moved["id"] < 0:
            self.toast("This link is still uploading; move it once it's saved")
            self.refresh()
            return
        above = items[new_index - 1].get("position") if new_index > 0 else None
        below = items[new_index + 1].get("position") if new_index + 1 < len(items) else None
        position = self.position_between(above, below)
        if position is None:
            return
        self.db.set_position(moved["id"], position)  # show the new order right away
        self.refresh()
        self.run(self._save_position(moved["id"], position))

    async def _save_position(self, bookmark_id: int, position: float) -> None:
        try:
            self.db.upsert_bookmarks([await self.api.update_bookmark(bookmark_id, position=position)])
        except OfflineError:
            self.toast("You're offline; the new order will be lost on the next sync")
        except ApiError as e:
            self.toast(f"Couldn't save the new order: {e.detail}")
            await self.do_sync(full=True)

    def move_category(self, source_id: int, target_id: int) -> None:
        """Category tile dropped onto another tile: it takes that tile's place."""
        ids = [c["id"] for c in self.db.categories()]
        if source_id == target_id or source_id not in ids or target_id not in ids:
            return
        target_index = ids.index(target_id)
        ids.remove(source_id)
        ids.insert(target_index, source_id)  # takes the target's spot; the others shift over
        self.db.set_category_order(ids)
        self.refresh()

        async def save():
            try:
                self.db.replace_categories(await self.api.reorder_categories(ids))
            except (OfflineError, ApiError) as e:
                self.toast(f"Couldn't save the category order: {e}")
        self.run(save())

    async def delete_bookmark(self, bookmark_id: int, go_back: bool = False) -> None:
        bm = self.db.bookmark(bookmark_id)
        try:
            await self.api.delete_bookmark(bookmark_id)
        except OfflineError:
            self.toast("You're offline; deleting needs a connection")
            return
        self.db.delete_bookmark(bookmark_id)
        if go_back and self.history and self.history[-1] == f"/bookmark/{bookmark_id}":
            self.history.pop()
        self.refresh()
        if bm:
            self.toast("Deleted", action="Undo", on_action=lambda: self.run(self.save_link(bm["url"])))

    async def find_recipe(self, bookmark_id: int) -> None:
        try:
            self.db.upsert_bookmarks([await self.api.find_recipe(bookmark_id)])
        except (OfflineError, ApiError) as e:
            self.toast(f"Couldn't look for the recipe: {e}")
            return
        self.refresh()
        self._schedule_poll()

    async def refresh_bookmark(self, bookmark_id: int) -> None:
        try:
            self.db.upsert_bookmarks([await self.api.refresh_bookmark(bookmark_id)])
        except (OfflineError, ApiError) as e:
            self.toast(str(e))
            return
        self.refresh()
        self._schedule_poll()

    def open_url(self, url: str) -> None:
        # External mode hands Instagram/TikTok/YouTube links to their own apps on phones.
        self.run(self.launcher.launch_url(url, mode=ft.LaunchMode.EXTERNAL_APPLICATION))

    async def copy(self, text: str, message: str = "Link copied") -> None:
        await self.clipboard.set(text)
        self.db.set("last_clipboard_offer", text)  # don't offer to save what we just copied
        self.toast(message)

    async def share(self, bm: dict) -> None:
        await self.sharer.share_text(bm["url"], subject=bm.get("title") or None)

    async def export(self, fmt: str) -> None:
        if self.web:
            self.toast("Export from the LinkVault app on your PC")
            return
        try:
            content = await self.api.export(fmt)
        except (OfflineError, ApiError) as e:
            self.toast(f"Export failed: {e}")
            return
        if not isinstance(content, str):
            import json
            content = json.dumps(content, ensure_ascii=False, indent=2)
        try:
            folder = await self.storage.get_downloads_directory()
        except Exception:
            folder = None
        folder = Path(folder) if folder else data_dir()
        name = {"json": "linkvault.json", "csv": "linkvault.csv", "html": "linkvault-bookmarks.html"}[fmt]
        path = folder / name
        path.write_text(content, encoding="utf-8")
        self.toast(f"Exported to {path}")


def public_origin(url: str | None) -> str:
    """Web address of the site the browser is on, from the session's connection URL.

    Flet reports the live connection (ws://host/app). Behind a tunnel or a real domain the
    browser is on https even though the tunnel talks plain ws to us, so only local addresses
    stay http.
    """
    from urllib.parse import urlparse

    p = urlparse(url or "")
    if not p.netloc:
        return ""
    host = p.hostname or ""
    local = host in ("localhost", "127.0.0.1", "::1") or host.startswith(("192.168.", "10."))
    secure = p.scheme in ("https", "wss") or not local
    return f"{'https' if secure else 'http'}://{p.netloc}"


async def main(page: ft.Page, web_api: str | None = None):
    logging.basicConfig(level=logging.INFO)
    page.title = "LinkVault"
    page.theme = ft.Theme(color_scheme_seed=ft.Colors.INDIGO)
    page.dark_theme = ft.Theme(color_scheme_seed=ft.Colors.INDIGO)

    app = LinkVaultApp(page, web_api)
    if app.web:
        await app.restore_web_login()
    app.apply_theme()
    page.on_view_pop = app.on_view_pop
    page.on_route_change = app.on_route_change

    async def on_lifecycle(e: ft.AppLifecycleStateChangeEvent):
        if e.state == ft.AppLifecycleState.RESUME:
            await app.check_clipboard()
            await app.do_sync()

    page.on_app_lifecycle_state_change = on_lifecycle

    app.render()
    # The app may have been opened by a share (deep link) rather than the launcher.
    shared = url_from_route(page.route)
    if shared:
        await app.save_link(shared)
    await app.do_sync()
    if app.is_windows:
        app.start_screenshot_watcher()
        await app.check_clipboard()


async def web_main(page: ft.Page):
    """Entry point when the server serves the app as a web page (see serve.py)."""
    await main(page, web_api=os.getenv("LINKVAULT_API", "http://127.0.0.1:8000"))


if __name__ == "__main__":
    ft.run(main)

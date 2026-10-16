"""Keeps the local cache in step with the server: upload the outbox, then pull changes."""

import logging

from api_client import ApiClient, ApiError, OfflineError
from local_db import LocalDB

log = logging.getLogger(__name__)


class SyncService:
    def __init__(self, api: ApiClient, db: LocalDB):
        self.api = api
        self.db = db

    async def save(self, url: str, note: str | None = None) -> tuple[dict | None, bool]:
        """Save a link. Returns (bookmark, online). Offline saves go to the outbox."""
        try:
            bm = await self.api.save_bookmark(url, note)
        except OfflineError:
            self.db.queue(url, note)
            return None, False
        self.db.upsert_bookmarks([bm])
        return bm, True

    async def flush_outbox(self) -> int:
        sent = 0
        for item in self.db.outbox():
            try:
                bm = await self.api.save_bookmark(item["url"], item["note"])
            except OfflineError:
                break
            except ApiError as e:
                if e.status in (401, 403):
                    break
                log.warning("dropping outbox item %s: %s", item["url"], e.detail)  # e.g. not a link
                self.db.dequeue(item["id"])
                continue
            self.db.dequeue(item["id"])
            self.db.upsert_bookmarks([bm])
            sent += 1
        return sent

    async def sync(self) -> bool:
        """Returns True when anything is still processing on the server (caller can poll again)."""
        await self.flush_outbox()
        self.db.replace_categories(await self.api.categories())
        last = self.db.get("last_sync") or None
        res = await self.api.list_bookmarks(since=last)
        self.db.upsert_bookmarks(res["items"])
        if last is None:
            # First sync: archived items aren't in the default listing, fetch them too.
            self.db.upsert_bookmarks((await self.api.list_bookmarks(archived="true"))["items"])
        self.db.set("last_sync", res["server_time"])
        return bool(self.db.conn.execute(
            "SELECT 1 FROM bookmarks WHERE status = 'processing' OR transcript_status = 'processing'"
            " OR recipe_source = 'processing' LIMIT 1"
        ).fetchone())

    async def full_resync(self) -> None:
        self.db.set("last_sync", "")
        self.db.conn.execute("DELETE FROM bookmarks WHERE id > 0")
        self.db.conn.commit()
        await self.sync()

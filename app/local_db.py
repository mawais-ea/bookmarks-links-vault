"""On-device SQLite cache: bookmarks, categories, settings and the offline outbox."""

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY, name TEXT, icon TEXT, color TEXT, sort_order INTEGER, is_default INTEGER, count INTEGER
);
CREATE TABLE IF NOT EXISTS bookmarks (
    id INTEGER PRIMARY KEY,           -- negative ids are local placeholders waiting in the outbox
    url TEXT, canonical_url TEXT, platform TEXT, title TEXT, description TEXT, thumbnail_url TEXT,
    author TEXT, site_name TEXT, note TEXT, category_id INTEGER, category_name TEXT, suggested_category TEXT,
    tags TEXT, confidence REAL, needs_review INTEGER, status TEXT, is_favorite INTEGER, is_archived INTEGER,
    created_at TEXT, updated_at TEXT, transcript TEXT, transcript_status TEXT, transcript_source TEXT,
    summary TEXT, summary_source TEXT, recipe TEXT, recipe_source TEXT, raw_text TEXT, position REAL
);
CREATE INDEX IF NOT EXISTS ix_bm_created ON bookmarks(created_at DESC);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT, note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

BOOKMARK_COLS = [
    "id", "url", "canonical_url", "platform", "title", "description", "thumbnail_url", "author", "site_name",
    "note", "category_id", "category_name", "suggested_category", "tags", "confidence", "needs_review", "status",
    "is_favorite", "is_archived", "created_at", "updated_at", "transcript", "transcript_status", "transcript_source",
    "summary", "summary_source", "recipe", "recipe_source", "raw_text", "position",
]


class LocalDB:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        # Older installs: add columns introduced later.
        have = {r[1] for r in self.conn.execute("PRAGMA table_info(bookmarks)")}
        for col in ("transcript", "transcript_status", "transcript_source", "summary", "summary_source",
                    "recipe", "recipe_source", "raw_text", "position"):
            if col not in have:
                kind = "REAL" if col == "position" else "TEXT"
                self.conn.execute(f"ALTER TABLE bookmarks ADD COLUMN {col} {kind}")
        self.conn.commit()

    # ------------------------------------------------------------ settings

    def get(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))
        self.conn.commit()

    def clear_account(self) -> None:
        for table in ("bookmarks", "categories", "outbox"):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.execute("DELETE FROM meta WHERE key IN ('token', 'api_key', 'email', 'last_sync')")
        self.conn.commit()

    # ------------------------------------------------------------ categories

    def replace_categories(self, cats: list[dict]) -> None:
        self.conn.execute("DELETE FROM categories")
        self.conn.executemany(
            "INSERT INTO categories VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(c["id"], c["name"], c["icon"], c["color"], c["sort_order"], int(c["is_default"]), c.get("count", 0))
             for c in cats],
        )
        self.conn.commit()

    def categories(self) -> list[dict]:
        rows = self.conn.execute("""
            SELECT c.*, (SELECT COUNT(*) FROM bookmarks b WHERE b.category_id = c.id AND b.is_archived = 0) AS n
            FROM categories c ORDER BY sort_order, id
        """).fetchall()
        return [{**dict(r), "count": r["n"]} for r in rows]

    def category(self, category_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM categories WHERE id=?", (category_id,)).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------ bookmarks

    def upsert_bookmarks(self, items: list[dict]) -> None:
        for b in items:
            if b.get("deleted_at"):
                self.conn.execute("DELETE FROM bookmarks WHERE id=?", (b["id"],))
                continue
            row = {**b, "tags": json.dumps(b.get("tags") or []),
                   "recipe": json.dumps(b["recipe"]) if b.get("recipe") else None}
            for flag in ("needs_review", "is_favorite", "is_archived"):
                row[flag] = int(bool(row.get(flag)))
            # List syncs leave the transcript out; keep the copy we already have.
            updates = ", ".join(
                f"{c} = COALESCE(excluded.{c}, bookmarks.{c})" if c == "transcript" else f"{c} = excluded.{c}"
                for c in BOOKMARK_COLS if c != "id")
            self.conn.execute(
                f"INSERT INTO bookmarks ({', '.join(BOOKMARK_COLS)}) "
                f"VALUES ({', '.join('?' for _ in BOOKMARK_COLS)}) ON CONFLICT(id) DO UPDATE SET {updates}",
                [row.get(c) for c in BOOKMARK_COLS],
            )
        self.conn.commit()

    def set_position(self, bookmark_id: int, position: float) -> None:
        self.conn.execute("UPDATE bookmarks SET position=? WHERE id=?", (position, bookmark_id))
        self.conn.commit()

    def set_category_order(self, ids: list[int]) -> None:
        for order, cat_id in enumerate(ids):
            self.conn.execute("UPDATE categories SET sort_order=? WHERE id=?", (order, cat_id))
        self.conn.commit()

    def delete_bookmark(self, bookmark_id: int) -> None:
        self.conn.execute("DELETE FROM bookmarks WHERE id=?", (bookmark_id,))
        self.conn.commit()

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["tags"] = json.loads(d.get("tags") or "[]")
        d["recipe"] = json.loads(d["recipe"]) if d.get("recipe") else None
        for flag in ("needs_review", "is_favorite", "is_archived"):
            d[flag] = bool(d.get(flag))
        return d

    def bookmark(self, bookmark_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM bookmarks WHERE id=?", (bookmark_id,)).fetchone()
        return self._row(row) if row else None

    def bookmarks(self, category_id: int | None = None, q: str = "", platform: str | None = None,
                  favorite: bool | None = None, review: bool | None = None, archived: bool = False,
                  limit: int = 500) -> list[dict]:
        sql = "SELECT * FROM bookmarks WHERE is_archived = ?"
        args: list = [int(archived)]
        if category_id is not None:
            sql += " AND category_id = ?"
            args.append(category_id)
        if platform:
            sql += " AND platform = ?"
            args.append(platform)
        if favorite is not None:
            sql += " AND is_favorite = ?"
            args.append(int(favorite))
        if review is not None:
            sql += " AND needs_review = ? AND id > 0"
            args.append(int(review))
        for term in q.split()[:8]:
            sql += (" AND (title LIKE ? OR description LIKE ? OR note LIKE ? OR tags LIKE ? OR url LIKE ?"
                    " OR author LIKE ? OR category_name LIKE ? OR transcript LIKE ? OR summary LIKE ? OR recipe LIKE ? OR raw_text LIKE ?)")
            args.extend([f"%{term}%"] * 11)
        sql += " ORDER BY COALESCE(position, 9e18) DESC, created_at DESC LIMIT ?"
        args.append(limit)
        return [self._row(r) for r in self.conn.execute(sql, args).fetchall()]

    def platforms(self) -> list[str]:
        return [r[0] for r in self.conn.execute(
            "SELECT platform FROM bookmarks GROUP BY platform ORDER BY COUNT(*) DESC").fetchall()]

    # ------------------------------------------------------------ outbox

    def queue(self, url: str, note: str | None = None) -> int:
        cur = self.conn.execute("INSERT INTO outbox(url, note) VALUES (?, ?)", (url, note))
        outbox_id = cur.lastrowid
        # Show a placeholder card immediately; it's replaced by the real one after upload.
        self.conn.execute(
            "INSERT INTO bookmarks (id, url, canonical_url, platform, title, status, needs_review, is_favorite,"
            " is_archived, tags, created_at, updated_at)"
            " VALUES (?, ?, ?, 'web', ?, 'pending', 0, 0, 0, '[]', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))",
            (-outbox_id, url, url, url),
        )
        self.conn.commit()
        return outbox_id

    def outbox(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM outbox ORDER BY id").fetchall()]

    def dequeue(self, outbox_id: int) -> None:
        self.conn.execute("DELETE FROM outbox WHERE id=?", (outbox_id,))
        self.conn.execute("DELETE FROM bookmarks WHERE id=?", (-outbox_id,))
        self.conn.commit()

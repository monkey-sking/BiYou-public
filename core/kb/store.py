import json
import os
import sqlite3
from datetime import datetime

from core.publishing.records import get_bilibili_publish_ids
from core.utils.config import PROJECT_ROOT, load_config


def get_default_db_path() -> str:
    config = load_config()
    kb_config = config.get("kb") or {}
    configured = kb_config.get("resource_links_db") or config.get("resource_links_db")
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    return os.path.join(PROJECT_ROOT, "data", "resource_kb.db")


class ResourceLinkStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or get_default_db_path()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_schema()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS resource_links (
                    bvid TEXT PRIMARY KEY,
                    video_title TEXT,
                    channel TEXT,
                    links_json TEXT,
                    created_at TIMESTAMP
                )
                """
            )

    def upsert(self, bvid: str, video_title: str, channel: str, links: list[dict]) -> bool:
        if not bvid or not links:
            return False
        links_json = json.dumps(links, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO resource_links (bvid, video_title, channel, links_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(bvid) DO UPDATE SET
                    video_title = excluded.video_title,
                    channel = excluded.channel,
                    links_json = excluded.links_json
                """,
                (bvid, video_title or "", channel or "", links_json, datetime.now().isoformat(timespec="seconds")),
            )
        return True

    def get(self, bvid: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM resource_links WHERE bvid = ?", (bvid,)).fetchone()
        return dict(row) if row else None

    def search(self, query: str, limit: int = 5) -> list[dict]:
        term = f"%{(query or '').strip()}%"
        if term == "%%":
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM resource_links
                WHERE video_title LIKE ? OR channel LIKE ? OR links_json LIKE ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (term, term, term, int(limit)),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            try:
                item["links"] = json.loads(item.get("links_json") or "[]")
            except json.JSONDecodeError:
                item["links"] = []
            results.append(item)
        return results

    def store_from_metadata(self, metadata: dict) -> bool:
        if not isinstance(metadata, dict):
            return False
        links = metadata.get("resource_links") or []
        if not links:
            return False
        bvid, _aid = get_bilibili_publish_ids(metadata)
        if not bvid:
            return False
        return self.upsert(
            bvid=bvid,
            video_title=metadata.get("title") or metadata.get("original_title") or "",
            channel=metadata.get("channel") or metadata.get("uploader") or metadata.get("uploader_id") or "",
            links=links,
        )


def store_resource_links_from_metadata(metadata: dict) -> bool:
    return ResourceLinkStore().store_from_metadata(metadata)


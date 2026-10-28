"""Legacy DM monitor (backward-compatible with core/kb/ path).

Prefer ``core.bili_dm.monitor.ResourceDMMonitor`` for new code.
"""

import re
import time

from core.kb.dm_client import BiliDMClient
from core.kb.database import search_links as _db_search_links
from core.services.bili_api import get_bilibili_credentials
from core.utils.logger import log

# Keywords that trigger resource link search + auto-reply
RESOURCE_WORD_RE = re.compile(
    r"(资源|链接|asset|discord|下载|素材|项目文件|project\s*files?|source\s*code)",
    re.IGNORECASE,
)


def extract_query(content: str) -> str:
    text = (content or "").strip()
    if not RESOURCE_WORD_RE.search(text):
        return ""
    text = RESOURCE_WORD_RE.sub(" ", text)
    text = re.sub(r"[【】\[\]（）()：:，,。.!！?？]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def format_reply(match: dict) -> str:
    title = match.get("video_title") or "相关视频"
    links = [
        item
        for item in (match.get("links") or [])
        if item.get("classification") == "video_required"
    ] or (match.get("links") or [])
    lines = [f"你要的《{title}》资源链接已整理："]
    for item in links[:8]:
        label = item.get("label") or "资源"
        lines.append(f"{label}: {item.get('url')}")
    lines.append("如果不是这条视频，请回复“资源 + 视频标题关键词”。")
    return "\n".join(lines)


class ResourceDMMonitor:
    def __init__(self, client=None, store=None, sleep_seconds: float = 300.0):
        self.client = client
        self._store = store  # Optional: injectable store for tests
        self.sleep_seconds = sleep_seconds

    def _search(self, query: str, limit: int = 1):
        """Search KB, using injected store or default database module."""
        if self._store:
            return self._store.search(query, limit=limit)
        return _db_search_links(query, limit=limit)

    @classmethod
    def from_bilibili_cookies(cls, cookies=None, store=None, sleep_seconds: float = 300.0):
        cookies = cookies or get_bilibili_credentials()
        if not cookies:
            raise RuntimeError("Bilibili cookies are required for DM monitoring")
        return cls(BiliDMClient(cookies), store=store, sleep_seconds=sleep_seconds)

    def poll_once(self) -> int:
        sent = 0
        for msg in self.client.fetch_new_messages():
            query = extract_query(msg.get("content", ""))
            if not query:
                continue
            matches = self._search(query, limit=1)
            if not matches:
                continue
            self.client.send_text(msg["uid"], format_reply(matches[0]))
            sent += 1
        return sent

    def run_forever(self):
        while True:
            try:
                self.poll_once()
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                log.warning(f"⚠️ Bilibili resource DM monitor error: {exc}")
            time.sleep(self.sleep_seconds)

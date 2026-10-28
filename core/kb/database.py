"""
B站视频资源链接知识库 (RAG KB)

SQLite 存储：bvid → 视频资源链接映射
支持关键词检索，零 LLM token 消耗。
"""
import json
import os
import sqlite3
from datetime import datetime
from typing import Optional

def _resolve_db_path() -> str:
    """资源知识库真源：配置 kb.resource_links_db > <项目根>/data/resource_kb.db。

    与 core/kb/store.py 的解析保持一致。2026-09-18 修复：此前固定指向
    core/data/resource_kb.db（7 月初后未再更新，仅 14 条旧数据），
    导致私信资源匹配大面积漏命中。
    """
    try:
        from core.utils.config import PROJECT_ROOT, load_config

        cfg = load_config() or {}
        configured = (cfg.get("kb") or {}).get("resource_links_db") or cfg.get(
            "resource_links_db"
        )
        if configured:
            return os.path.abspath(os.path.expanduser(str(configured)))
        return os.path.join(PROJECT_ROOT, "data", "resource_kb.db")
    except Exception:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(root, "data", "resource_kb.db")


DB_PATH = _resolve_db_path()


def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """初始化数据库表结构"""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS resource_links (
                bvid TEXT PRIMARY KEY,
                video_title TEXT NOT NULL DEFAULT '',
                channel TEXT NOT NULL DEFAULT '',
                links_json TEXT NOT NULL DEFAULT '[]',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rl_title
            ON resource_links(video_title)
        """)


def save_video_links(bvid: str, video_title: str, channel: str, links: list) -> bool:
    """
    保存视频资源链接到知识库。

    Args:
        bvid: B站 BV号
        video_title: 视频标题
        channel: 频道/UP主名称
        links: [{"type": "assetstore"|"discord"|"github"|"other",
                 "url": "...", "label": "..."}, ...]

    Returns:
        True 保存成功
    """
    init_db()
    links_json = json.dumps(links, ensure_ascii=False)
    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO resource_links (bvid, video_title, channel, links_json, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(bvid) DO UPDATE SET
                video_title = excluded.video_title,
                channel = excluded.channel,
                links_json = excluded.links_json,
                updated_at = CURRENT_TIMESTAMP
        """, (bvid, video_title, channel, links_json))
    return True


def get_video_links(bvid: str) -> Optional[dict]:
    """按 BV号 查询视频资源链接"""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM resource_links WHERE bvid = ?", (bvid,)
        ).fetchone()
    if not row:
        return None
    return {
        "bvid": row["bvid"],
        "video_title": row["video_title"],
        "channel": row["channel"],
        "links": json.loads(row["links_json"]),
        "created_at": row["created_at"],
    }


def _lcs_len(a: str, b: str, cap: int = 400) -> int:
    """两段文本的最长公共子串长度（只在短文本上用，成本可控）。"""
    a, b = a[:cap], b[:cap]
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def search_links(keyword: str, limit: int = 10) -> list:
    """
    全文关键词检索视频资源链接。

    匹配字段：视频标题、频道名、链接标签、URL。

    Args:
        keyword: 搜索关键词（如 "asset store", "discord", 视频名）
        limit: 最大返回条数

    Returns:
        [{"bvid": ..., "video_title": ..., "channel": ...,
          "links": [...], "relevance": "title"|"channel"|"link"}, ...]
    """
    init_db()
    results = []
    keyword_lower = keyword.strip().lower()
    if not keyword_lower:
        return results

    with _get_conn() as conn:
        # 2026-09-18 修复：原来只取最近 limit*3 条（约 15 条）来打分，
        # 老视频永远匹配不到。KB 总量很小（几百条），直接全量扫描。
        rows = conn.execute(
            "SELECT * FROM resource_links ORDER BY created_at DESC LIMIT 5000"
        ).fetchall()

    scored = []
    keywords = [kw for kw in keyword_lower.split() if len(kw) > 2]
    for row in rows:
        score = 0
        reason = ""

        title_lower = (row["video_title"] or "").lower()
        channel_lower = (row["channel"] or "").lower()
        links_json = row["links_json"] or "[]"

        # Title match: full query (highest)
        if keyword_lower in title_lower:
            score += 10
            reason = "title"
        else:
            # Individual keyword matching for punctuation-stripped queries
            for kw in keywords:
                if kw in title_lower:
                    score += 5
                    if not reason:
                        reason = "title_kw"

        # Channel match: full query
        if keyword_lower in channel_lower:
            score += 5
            if not reason:
                reason = "channel"
        else:
            for kw in keywords:
                if kw in channel_lower:
                    score += 3
                    if not reason:
                        reason = "channel_kw"

        # 链接标签/URL匹配
        try:
            links = json.loads(links_json)
            for link in links:
                label_lower = (link.get("label", "") or "").lower()
                url_lower = (link.get("url", "") or "").lower()
                if keyword_lower in label_lower or keyword_lower in url_lower:
                    score += 3
                    if not reason:
                        reason = "link"
        except json.JSONDecodeError:
            pass

        # 2026-09-18：粉丝很少给完整标题（"波浪曲线的hip在哪里获取"），
        # 关键词不命中时用最长公共子串兜底（>=4 个连续汉字/字符才算）。
        if score == 0 and len(keyword_lower) >= 4:
            hit = _lcs_len(keyword_lower, title_lower)
            if hit >= 4:
                score = 3 + hit
                reason = "fuzzy"

        if score > 0:
            scored.append((score, {
                "bvid": row["bvid"],
                "video_title": row["video_title"],
                "channel": row["channel"],
                "links": json.loads(links_json),
                "relevance": reason,
            }))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored[:limit]]


def list_recent(limit: int = 20) -> list:
    """列出最近添加的视频链接"""
    init_db()
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM resource_links ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [{
        "bvid": r["bvid"],
        "video_title": r["video_title"],
        "channel": r["channel"],
        "links": json.loads(r["links_json"]),
        "created_at": r["created_at"],
    } for r in rows]

import json
import os
from typing import Any

from core.utils.config import PROJECT_ROOT


DEFAULT_STATE_PATH = os.path.join(PROJECT_ROOT, "history", "channel_daily_publish.json")


def default_state(
    channel_url: str = "",
    publish_time: str = "19:05",
    timezone: str = "Asia/Shanghai",
) -> dict[str, Any]:
    return {
        "channel_url": channel_url,
        "publish_time": publish_time,
        "timezone": timezone,
        "items": {},
    }


def load_channel_backlog(
    state_path: str = DEFAULT_STATE_PATH,
    channel_url: str = "",
    publish_time: str = "19:05",
    timezone: str = "Asia/Shanghai",
) -> dict[str, Any]:
    if os.path.exists(state_path):
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
    else:
        state = default_state(channel_url=channel_url, publish_time=publish_time, timezone=timezone)

    state.setdefault("channel_url", channel_url)
    state.setdefault("publish_time", publish_time)
    state.setdefault("timezone", timezone)
    state.setdefault("items", {})
    return state


def save_channel_backlog(state: dict[str, Any], state_path: str = DEFAULT_STATE_PATH) -> None:
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def upsert_discovered_item(state: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    items = state.setdefault("items", {})
    youtube_id = incoming["youtube_id"]
    existing = items.get(youtube_id, {})

    merged = {
        "youtube_id": youtube_id,
        "source_url": incoming.get("source_url", existing.get("source_url", "")),
        "title": incoming.get("title", existing.get("title", "")),
        "discovered_at": existing.get("discovered_at", incoming.get("discovered_at", "")),
        "prepared_at": existing.get("prepared_at", ""),
        "status": existing.get("status", "discovered"),
        "is_new": existing.get("is_new", True),
        "video_path": existing.get("video_path", ""),
        "metadata_path": existing.get("metadata_path", ""),
        "scheduled_for": existing.get("scheduled_for", ""),
        "uploaded_bvid": existing.get("uploaded_bvid", ""),
        "last_error": existing.get("last_error", ""),
    }

    items[youtube_id] = merged
    return merged

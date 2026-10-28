import argparse
import concurrent.futures
import json
import subprocess
import sys
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from core.services.channel_backlog import (
    DEFAULT_STATE_PATH,
    load_channel_backlog,
    save_channel_backlog,
    upsert_discovered_item,
)
from core.utils.config import load_config


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def choose_publish_candidate(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    prepared = [item for item in items if item.get("status") == "prepared"]
    if not prepared:
        return None

    new_items = [item for item in prepared if item.get("is_new")]
    target = new_items if new_items else prepared
    return max(target, key=lambda item: item.get("discovered_at", ""))


def has_scheduled_item_for_day(items: list[dict[str, Any]], day_str: str) -> bool:
    for item in items:
        scheduled_for = item.get("scheduled_for", "")
        if scheduled_for and scheduled_for[:10] == day_str and item.get("status") in {"scheduled", "published"}:
            return True
    return False


def compute_target_publish_datetime(
    current: datetime,
    publish_time: str,
    timezone: str = "Asia/Shanghai",
    min_lead_hours: int = 4,
) -> datetime:
    tz = ZoneInfo(timezone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=tz)
    else:
        current = current.astimezone(tz)

    hour = int(publish_time[:2])
    minute = int(publish_time[3:5])
    target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target - current < timedelta(hours=min_lead_hours):
        target = target + timedelta(days=1)
    return target


def _yt_dlp_command() -> list[str]:
    return [sys.executable, "-m", "yt_dlp"]


def discover_channel_items(channel_url: str, limit: int = 20) -> list[dict[str, Any]]:
    cmd = [
        *_yt_dlp_command(),
        "--flat-playlist",
        "--playlist-end",
        str(limit),
        "--print-json",
        channel_url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    items = []
    discovered_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        video_id = payload.get("id")
        if not video_id:
            continue
        items.append(
            {
                "youtube_id": video_id,
                "title": payload.get("title", ""),
                "source_url": f"https://www.youtube.com/watch?v={video_id}",
                "discovered_at": discovered_at,
            }
        )
    return items


class ChannelDailyPublishWorkflow:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or load_config()
        cfg = self.config.get("channel_daily_publish", {})
        monitor_channels = (self.config.get("monitor", {}) or {}).get("channels", [])
        fallback_channel_url = ""
        if monitor_channels:
            fallback_channel_url = next((item.get("url", "") for item in monitor_channels if item.get("url")), "")

        self.channel_url = cfg.get("channel_url") or fallback_channel_url
        self.publish_time = cfg.get("publish_time", "19:05")
        self.timezone = cfg.get("timezone", "Asia/Shanghai")
        self.sync_window = int(cfg.get("sync_window", 20) or 20)
        self.state_path = cfg.get("state_file") or DEFAULT_STATE_PATH

    def load_state(self) -> dict[str, Any]:
        state = load_channel_backlog(
            state_path=self.state_path,
            channel_url=self.channel_url,
            publish_time=self.publish_time,
            timezone=self.timezone,
        )
        state["channel_url"] = self.channel_url
        state["publish_time"] = self.publish_time
        state["timezone"] = self.timezone
        return state

    def save_state(self, state: dict[str, Any]) -> None:
        save_channel_backlog(state, state_path=self.state_path)

    def now_local(self) -> datetime:
        return datetime.now(ZoneInfo(self.timezone))

    def now_iso(self) -> str:
        return self.now_local().isoformat()

    def discover_items(self) -> list[dict[str, Any]]:
        if not self.channel_url:
            return []
        return discover_channel_items(self.channel_url, limit=self.sync_window)

    def build_main_args(
        self,
        url: str | None,
        *,
        skip_upload: bool,
        skip_fetch: bool = False,
        skip_cover: bool = False,
    ) -> argparse.Namespace:
        return argparse.Namespace(
            url=url,
            skip_fetch=skip_fetch,
            skip_cover=skip_cover,
            skip_upload=skip_upload,
            dry_run=False,
            yes=True,
            post_sync_subtitles=False,
            season=None,
            auto_season=True,
        )

    def prepare_item(self, item: dict[str, Any]) -> dict[str, Any]:
        import main as main_workflow
        from core.modules.fetcher import load_metadata, save_metadata

        args = self.build_main_args(item["source_url"], skip_upload=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            download_future, video_path_target = main_workflow.step_download_video(args, executor)
            if args.url and not video_path_target:
                raise RuntimeError("Video prepare failed before metadata")

            metadata_path = main_workflow.step_fetch_metadata(args, target_path=video_path_target)
            if not metadata_path:
                raise RuntimeError("Metadata preparation failed")

            if not main_workflow.step_process_cover(args, metadata_path, download_future):
                raise RuntimeError("Cover preparation failed")

            video_path = video_path_target
            if download_future:
                resolved_video_path = main_workflow.wait_for_download(download_future)
                if not resolved_video_path:
                    raise RuntimeError("Video download did not complete")
                video_path = resolved_video_path

                metadata = load_metadata(path=metadata_path)
                if metadata and metadata.get("video_path") != video_path:
                    metadata["video_path"] = video_path
                    save_metadata(metadata, path=metadata_path)

        return {"video_path": video_path, "metadata_path": metadata_path}

    def sync(self) -> dict[str, Any]:
        state = self.load_state()

        for incoming in self.discover_items():
            upsert_discovered_item(state, incoming)

        items = list(state.get("items", {}).values())
        items.sort(key=lambda item: item.get("discovered_at", ""), reverse=True)

        for item in items:
            if item.get("status") in {"prepared", "scheduled", "published"}:
                continue

            item["status"] = "preparing"
            try:
                prepared = self.prepare_item(item)
                item["video_path"] = prepared.get("video_path", "")
                item["metadata_path"] = prepared.get("metadata_path", "")
                item["prepared_at"] = self.now_iso()
                item["status"] = "prepared"
                item["is_new"] = True
                item["last_error"] = ""
            except Exception as exc:
                item["status"] = "failed_prepare"
                item["last_error"] = str(exc)

        self.save_state(state)
        return state

    def compute_publish_datetime(self) -> datetime:
        return compute_target_publish_datetime(
            self.now_local(),
            self.publish_time,
            timezone=self.timezone,
        )

    def schedule_upload(self, item: dict[str, Any], schedule_ts: int) -> dict[str, Any]:
        import main as main_workflow
        from core.modules.fetcher import load_metadata

        metadata_path = item["metadata_path"]
        args = self.build_main_args(None, skip_upload=False, skip_fetch=True, skip_cover=True)
        ok = main_workflow.step_upload(args, schedule_ts, metadata_path)
        if not ok:
            raise RuntimeError("Scheduled upload failed")

        uploaded_bvid = ""
        if metadata_path:
            metadata = load_metadata(path=metadata_path)
            uploaded_bvid = metadata.get("uploaded_bvid", "")

        main_workflow.step_cleanup(args, metadata_path)
        return {"success": True, "uploaded_bvid": uploaded_bvid}

    def publish(self) -> dict[str, Any] | None:
        state = self.load_state()
        items = list(state.get("items", {}).values())
        publish_dt = self.compute_publish_datetime()
        publish_day = publish_dt.date().isoformat()

        if has_scheduled_item_for_day(items, publish_day):
            return None

        candidate = choose_publish_candidate(items)
        if not candidate:
            return None

        try:
            result = self.schedule_upload(candidate, int(publish_dt.timestamp()))
            candidate["status"] = "scheduled"
            candidate["scheduled_for"] = publish_dt.isoformat()
            candidate["uploaded_bvid"] = result.get("uploaded_bvid", "")
            candidate["is_new"] = False
            candidate["last_error"] = ""
        except Exception as exc:
            candidate["status"] = "failed_publish"
            candidate["last_error"] = str(exc)
            self.save_state(state)
            return None

        self.save_state(state)
        return candidate

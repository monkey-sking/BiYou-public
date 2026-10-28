from datetime import datetime

from core.modules.uploader import get_bilibili_credentials, upload_video
from core.publishing.adapters.base import BasePublishingAdapter
from core.publishing.models import AdapterCapabilities, PublishJob


class BilibiliAdapter(BasePublishingAdapter):
    platform_name = "bilibili"
    stability_tier = "stable"
    execution_mode = "api"
    capabilities = AdapterCapabilities(True, True, True, True)

    def validate(self, job: PublishJob) -> None:
        missing = []
        if not job.video_path:
            missing.append("video_path")
        if not job.cover_path:
            missing.append("cover_path")
        if missing:
            raise ValueError(f"Missing required fields: {', '.join(missing)}")

    def publish(self, job: PublishJob) -> dict:
        metadata = self._build_metadata(job)
        credentials = get_bilibili_credentials()
        raw_result = upload_video(
            metadata,
            credentials,
            dry_run=False,
            interactive=False,
        )
        return self._build_result(raw_result, scheduled=False, publish_at="")

    def schedule(self, job: PublishJob, publish_at: str) -> dict:
        metadata = self._build_metadata(job)
        metadata["dtime"] = self._publish_at_to_timestamp(publish_at)
        credentials = get_bilibili_credentials()
        raw_result = upload_video(
            metadata,
            credentials,
            dry_run=False,
            interactive=False,
        )
        return self._build_result(raw_result, scheduled=True, publish_at=publish_at)

    def _build_metadata(self, job: PublishJob) -> dict:
        payload = job.platform_payloads.get("bilibili", {})
        title = payload.get("title", job.title)
        description = payload.get("description", job.description)
        tags = payload.get("tags", job.tags)
        cover_path = payload.get("cover_path", job.cover_path)
        metadata = {
            "video_path": job.video_path,
            "title": title,
            "description": description,
            "tags": tags,
            "cover_path_16_9": cover_path,
            "cover_path_4_3": payload.get("cover_path_4_3", cover_path),
        }
        metadata.update(payload)
        return metadata

    def _build_result(self, raw_result, scheduled: bool, publish_at: str) -> dict:
        success = bool(isinstance(raw_result, dict) and raw_result.get("success"))
        bvid = ""
        aid = None
        if success:
            bvid = str(raw_result.get("bvid") or "")
            aid = raw_result.get("aid")
        platform_url = f"https://www.bilibili.com/video/{bvid}" if bvid else ""
        status = "scheduled" if scheduled and success else "published" if success else "failed_retryable"
        scheduled_by = "platform" if scheduled else "biyou"
        scheduled_for = publish_at if scheduled else ""
        extra = {}
        if aid is not None:
            extra["aid"] = aid
        return {
            "platform": self.platform_name,
            "status": status,
            "platform_content_id": bvid,
            "platform_url": platform_url,
            "native_schedule": scheduled,
            "scheduled_by": scheduled_by,
            "scheduled_for": scheduled_for,
            "published_at": "",
            "last_error": "" if success else "upload failed",
            "extra": extra,
        }

    @staticmethod
    def _publish_at_to_timestamp(publish_at: str) -> int:
        normalized = publish_at.replace("Z", "+00:00")
        return int(datetime.fromisoformat(normalized).timestamp())

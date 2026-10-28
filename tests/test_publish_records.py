import unittest

from core.publishing.models import PublishResult
from core.publishing.records import (
    get_bilibili_publish_ids,
    get_publish_record,
    update_publish_record,
)


class TestPublishRecords(unittest.TestCase):
    def test_update_publish_record_creates_publish_records_tree(self):
        metadata = {}
        result = PublishResult(
            platform="bilibili",
            status="published",
            platform_content_id="BV1xx",
            platform_url="https://www.bilibili.com/video/BV1xx",
            native_schedule=True,
            scheduled_by="platform",
            scheduled_for="",
            published_at="2026-04-08T19:05:00+08:00",
            last_error="",
            extra={"aid": 123456},
        )

        update_publish_record(metadata, result)

        self.assertEqual(metadata["publish_records"]["bilibili"]["platform_content_id"], "BV1xx")
        self.assertEqual(metadata["uploaded_bvid"], "BV1xx")
        self.assertEqual(metadata["uploaded_aid"], 123456)

    def test_update_publish_record_mirrors_ids_for_scheduled_bilibili_results(self):
        metadata = {"uploaded_bvid": "BV1legacy", "uploaded_aid": 42}
        result = PublishResult(
            platform="bilibili",
            status="scheduled",
            platform_content_id="BV1scheduled",
            platform_url="https://www.bilibili.com/video/BV1scheduled",
            native_schedule=True,
            scheduled_by="platform",
            scheduled_for="2026-04-08T19:05:00+08:00",
            published_at="",
            last_error="",
            extra={"aid": 987654},
        )

        update_publish_record(metadata, result)

        self.assertEqual(metadata["publish_records"]["bilibili"]["platform_content_id"], "BV1scheduled")
        self.assertEqual(metadata["uploaded_bvid"], "BV1scheduled")
        self.assertEqual(metadata["uploaded_aid"], 987654)

    def test_get_bilibili_publish_ids_falls_back_to_legacy_fields(self):
        metadata = {"uploaded_bvid": "BV1legacy", "uploaded_aid": 42}

        bvid, aid = get_bilibili_publish_ids(metadata)

        self.assertEqual(bvid, "BV1legacy")
        self.assertEqual(aid, 42)

    def test_get_bilibili_publish_ids_ignores_failed_publish_record_ids(self):
        metadata = {
            "uploaded_bvid": "BV1legacy",
            "uploaded_aid": 42,
            "publish_records": {
                "bilibili": {
                    "status": "failed_manual",
                    "platform_content_id": "BV1failed",
                    "aid": 999,
                }
            },
        }

        bvid, aid = get_bilibili_publish_ids(metadata)

        self.assertEqual(bvid, "BV1legacy")
        self.assertEqual(aid, 42)

    def test_update_publish_record_preserves_legacy_bilibili_ids_on_failed_empty_result(self):
        metadata = {"uploaded_bvid": "BV1legacy", "uploaded_aid": 42}
        result = PublishResult(
            platform="bilibili",
            status="failed_manual",
            platform_content_id="",
            platform_url="",
            native_schedule=False,
            scheduled_by="platform",
            scheduled_for="",
            published_at="",
            last_error="upload failed",
            extra={"aid": None},
        )

        update_publish_record(metadata, result)

        self.assertEqual(metadata["uploaded_bvid"], "BV1legacy")
        self.assertEqual(metadata["uploaded_aid"], 42)

    def test_update_publish_record_preserves_legacy_bilibili_ids_on_failed_result_with_ids(self):
        metadata = {"uploaded_bvid": "BV1legacy", "uploaded_aid": 42}
        result = PublishResult(
            platform="bilibili",
            status="failed_retryable",
            platform_content_id="BV1failed",
            platform_url="",
            native_schedule=False,
            scheduled_by="platform",
            scheduled_for="",
            published_at="",
            last_error="upload failed",
            extra={"aid": 999},
        )

        update_publish_record(metadata, result)

        self.assertEqual(metadata["uploaded_bvid"], "BV1legacy")
        self.assertEqual(metadata["uploaded_aid"], 42)

    def test_get_publish_record_returns_empty_dict_when_missing(self):
        self.assertEqual(get_publish_record({}, "douyin"), {})

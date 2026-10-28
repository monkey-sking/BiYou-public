import unittest
from datetime import datetime
from unittest.mock import patch

from core.publishing.models import PublishJob, ScheduleIntent


class TestBilibiliAdapter(unittest.TestCase):
    def _make_job(self, **overrides):
        data = {
            "platform_targets": ["bilibili"],
            "video_path": "/tmp/video.mp4",
            "title": "Test title",
            "description": "Test description",
            "cover_path": "/tmp/cover.jpg",
            "tags": ["tag1", "tag2"],
            "metadata": {},
            "platform_payloads": {},
        }
        data.update(overrides)
        return PublishJob(**data)

    def test_validate_requires_video_and_cover_paths(self):
        from core.publishing.adapters.bilibili import BilibiliAdapter

        adapter = BilibiliAdapter()

        with self.assertRaises(ValueError):
            adapter.validate(self._make_job(video_path="", cover_path="/tmp/cover.jpg"))

        with self.assertRaises(ValueError):
            adapter.validate(self._make_job(video_path="/tmp/video.mp4", cover_path=""))

    @patch("core.publishing.adapters.bilibili.upload_video")
    @patch("core.publishing.adapters.bilibili.get_bilibili_credentials")
    def test_publish_wraps_uploader_result(self, mock_get_credentials, mock_upload_video):
        from core.publishing.adapters.bilibili import BilibiliAdapter

        mock_get_credentials.return_value = {"cookie": "value"}
        mock_upload_video.return_value = {"success": True, "bvid": "BV1abc", "aid": 123456}

        adapter = BilibiliAdapter()
        job = self._make_job(
            platform_payloads={
                "bilibili": {
                    "cover_path_4_3": "/tmp/cover-4x3.jpg",
                    "copyright": 2,
                }
            }
        )

        result = adapter.publish(job)

        self.assertEqual(result["platform"], "bilibili")
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["platform_content_id"], "BV1abc")
        self.assertEqual(result["platform_url"], "https://www.bilibili.com/video/BV1abc")
        self.assertEqual(result["extra"]["aid"], 123456)
        mock_get_credentials.assert_called_once_with()
        mock_upload_video.assert_called_once()
        metadata, credentials = mock_upload_video.call_args.args[:2]
        self.assertEqual(metadata["video_path"], "/tmp/video.mp4")
        self.assertEqual(metadata["title"], "Test title")
        self.assertEqual(metadata["description"], "Test description")
        self.assertEqual(metadata["tags"], ["tag1", "tag2"])
        self.assertEqual(metadata["cover_path_16_9"], "/tmp/cover.jpg")
        self.assertEqual(metadata["cover_path_4_3"], "/tmp/cover-4x3.jpg")
        self.assertEqual(metadata["copyright"], 2)
        self.assertEqual(credentials, {"cookie": "value"})
        self.assertFalse(mock_upload_video.call_args.kwargs["dry_run"])
        self.assertFalse(mock_upload_video.call_args.kwargs["interactive"])

    @patch("core.publishing.adapters.bilibili.upload_video")
    @patch("core.publishing.adapters.bilibili.get_bilibili_credentials")
    def test_schedule_sets_native_timestamp_and_platform_schedule_fields(
        self, mock_get_credentials, mock_upload_video
    ):
        from core.publishing.adapters.bilibili import BilibiliAdapter

        mock_get_credentials.return_value = {"cookie": "value"}
        mock_upload_video.return_value = {"success": True, "bvid": "BV1sched", "aid": 7}

        adapter = BilibiliAdapter()
        publish_at = "2026-04-08T19:05:00+08:00"
        expected_ts = int(datetime.fromisoformat(publish_at).timestamp())
        job = self._make_job(
            platform_payloads={"bilibili": {"cover_path_4_3": "/tmp/cover-4x3.jpg"}}
        )

        result = adapter.schedule(job, publish_at)

        self.assertEqual(result["status"], "scheduled")
        self.assertEqual(result["scheduled_by"], "platform")
        self.assertEqual(result["scheduled_for"], publish_at)
        self.assertTrue(result["native_schedule"])
        self.assertEqual(result["platform_content_id"], "BV1sched")
        self.assertEqual(result["platform_url"], "https://www.bilibili.com/video/BV1sched")
        self.assertEqual(result["extra"]["aid"], 7)
        metadata = mock_upload_video.call_args.args[0]
        self.assertEqual(metadata["dtime"], expected_ts)
        self.assertFalse(mock_upload_video.call_args.kwargs["dry_run"])
        self.assertFalse(mock_upload_video.call_args.kwargs["interactive"])


if __name__ == "__main__":
    unittest.main()

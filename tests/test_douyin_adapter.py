import unittest
from unittest.mock import Mock

from core.publishing.adapters.douyin import DouyinAdapter
from core.publishing.models import PublishJob


class TestDouyinAdapter(unittest.TestCase):
    def _make_job(self, **overrides):
        data = {
            "platform_targets": ["douyin"],
            "video_path": "/tmp/video.mp4",
            "title": "Morning Workout",
            "description": "A short session",
            "cover_path": "/tmp/cover.jpg",
            "tags": ["fitness", "cardio"],
            "metadata": {},
            "platform_payloads": {},
        }
        data.update(overrides)
        return PublishJob(**data)

    def test_publish_maps_tags_into_text_and_returns_item_id(self):
        client = Mock()
        client.upload_video.return_value = {"video_id": "video_1"}
        client.create_video.return_value = {"item_id": "item_1"}

        adapter = DouyinAdapter(client=client)
        job = self._make_job()

        result = adapter.publish(job)

        self.assertEqual(result["platform_content_id"], "item_1")
        self.assertEqual(result["scheduled_by"], "biyou")
        self.assertIn("#fitness", client.create_video.call_args.kwargs["text"])

    def test_schedule_raises_phase_one_not_implemented_error(self):
        adapter = DouyinAdapter(client=Mock())
        job = self._make_job()

        with self.assertRaisesRegex(
            NotImplementedError,
            "Douyin uses BiYou-managed scheduling in phase 1",
        ):
            adapter.schedule(job, "2026-04-08T10:00:00+08:00")


if __name__ == "__main__":
    unittest.main()

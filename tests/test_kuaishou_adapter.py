import unittest
from unittest.mock import Mock

from core.publishing.adapters.kuaishou import KuaishouAdapter
from core.publishing.models import PublishJob


class TestKuaishouAdapter(unittest.TestCase):
    def _make_job(self, **overrides):
        data = {
            "platform_targets": ["kuaishou"],
            "video_path": "/tmp/video.mp4",
            "title": "Morning Workout",
            "description": "A short session",
            "cover_path": "/tmp/cover.jpg",
            "tags": ["fitness", "cardio", "home", "warmup", "strength", "extra"],
            "metadata": {},
            "platform_payloads": {},
        }
        data.update(overrides)
        return PublishJob(**data)

    def test_publish_uses_start_upload_then_publish(self):
        client = Mock()
        client.start_upload.return_value = {
            "upload_token": "up_1",
            "endpoint": "uploader.test.gifshow.com",
        }
        client.publish_photo.return_value = {"photo_id": "photo_1"}

        adapter = KuaishouAdapter(client=client)
        job = self._make_job()

        result = adapter.publish(job)

        self.assertEqual(result["platform_content_id"], "photo_1")
        self.assertEqual(
            client.publish_photo.call_args.kwargs["caption"],
            "Morning Workout\nA short session\n#fitness #cardio #home #warmup",
        )
        client.publish_photo.assert_called_once()
        self.assertEqual(result["native_schedule"], False)
        self.assertEqual(result["scheduled_by"], "biyou")
        with self.assertRaisesRegex(
            NotImplementedError,
            "Kuaishou uses BiYou-managed scheduling in phase 1",
        ):
            adapter.schedule(job, "2026-04-08T19:05:00+08:00")

    def test_publish_prefers_platform_payload_overrides(self):
        client = Mock()
        client.start_upload.return_value = {
            "upload_token": "up_1",
            "endpoint": "uploader.test.gifshow.com",
        }
        client.publish_photo.return_value = {"photo_id": "photo_1"}

        adapter = KuaishouAdapter(client=client)
        job = self._make_job(
            platform_payloads={
                "kuaishou": {
                    "title": "快手标题",
                    "description": "快手简介",
                    "tags": ["暴汗", "居家"],
                    "video_path": "/tmp/kuaishou.mp4",
                    "cover_path": "/tmp/kuaishou.jpg",
                }
            }
        )

        adapter.publish(job)

        self.assertEqual(client.publish_photo.call_args.kwargs["video_path"], "/tmp/kuaishou.mp4")
        self.assertEqual(client.publish_photo.call_args.kwargs["cover_path"], "/tmp/kuaishou.jpg")
        self.assertIn("快手标题", client.publish_photo.call_args.kwargs["caption"])
        self.assertIn("#暴汗", client.publish_photo.call_args.kwargs["caption"])

    def test_publish_returns_failed_manual_when_video_exceeds_duration_limit(self):
        client = Mock()
        adapter = KuaishouAdapter(client=client)
        job = self._make_job(metadata={"duration": 6262})

        result = adapter.publish(job)

        self.assertEqual(result["status"], "failed_manual")
        self.assertIn("up to 60:00", result["last_error"])
        client.start_upload.assert_not_called()
        client.publish_photo.assert_not_called()


if __name__ == "__main__":
    unittest.main()

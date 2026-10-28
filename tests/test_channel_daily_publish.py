import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from core.services.channel_backlog import upsert_discovered_item
from core.workflows.channel_daily_publish import (
    ChannelDailyPublishWorkflow,
    choose_publish_candidate,
    compute_target_publish_datetime,
    has_scheduled_item_for_day,
)


class TestChannelDailyPublish(unittest.TestCase):
    def test_prefers_new_prepared_item_before_backlog(self):
        items = [
            {
                "youtube_id": "old1",
                "status": "prepared",
                "is_new": False,
                "discovered_at": "2026-04-06T10:00:00+08:00",
            },
            {
                "youtube_id": "new1",
                "status": "prepared",
                "is_new": True,
                "discovered_at": "2026-04-07T09:00:00+08:00",
            },
        ]

        chosen = choose_publish_candidate(items)

        self.assertEqual(chosen["youtube_id"], "new1")

    def test_falls_back_to_newest_unpublished_backlog_item(self):
        items = [
            {
                "youtube_id": "old1",
                "status": "prepared",
                "is_new": False,
                "discovered_at": "2026-04-05T09:00:00+08:00",
            },
            {
                "youtube_id": "old2",
                "status": "prepared",
                "is_new": False,
                "discovered_at": "2026-04-06T09:00:00+08:00",
            },
        ]

        chosen = choose_publish_candidate(items)

        self.assertEqual(chosen["youtube_id"], "old2")

    def test_blocks_second_schedule_for_same_day(self):
        items = [
            {
                "youtube_id": "a1",
                "status": "scheduled",
                "scheduled_for": "2026-04-07T19:05:00+08:00",
            }
        ]

        self.assertTrue(has_scheduled_item_for_day(items, "2026-04-07"))

    def test_compute_publish_slot_rolls_to_next_day_when_today_is_too_late(self):
        current = datetime.fromisoformat("2026-04-07T16:00:00+08:00")

        target = compute_target_publish_datetime(current, "19:05")

        self.assertEqual(target.isoformat(), "2026-04-08T19:05:00+08:00")

    def test_upsert_discovered_item_preserves_prepared_state(self):
        state = {
            "items": {
                "abc": {
                    "youtube_id": "abc",
                    "status": "prepared",
                    "is_new": True,
                    "metadata_path": "/tmp/video.json",
                }
            }
        }

        item = upsert_discovered_item(
            state,
            {
                "youtube_id": "abc",
                "title": "Updated Title",
                "source_url": "https://www.youtube.com/watch?v=abc",
                "discovered_at": "2026-04-07T10:00:00+08:00",
            },
        )

        self.assertEqual(item["status"], "prepared")
        self.assertEqual(item["title"], "Updated Title")
        self.assertEqual(item["metadata_path"], "/tmp/video.json")

    def test_sync_marks_new_item_prepared(self):
        workflow = ChannelDailyPublishWorkflow(
            config={
                "channel_daily_publish": {
                    "channel_url": "https://www.youtube.com/@RosarioCooking/videos",
                    "publish_time": "19:05",
                }
            }
        )

        in_memory_state = {"items": {}}
        workflow.load_state = lambda: in_memory_state
        workflow.save_state = lambda state: in_memory_state.update(state)
        workflow.discover_items = lambda: [
            {
                "youtube_id": "abc123xyz00",
                "title": "Video",
                "source_url": "https://www.youtube.com/watch?v=abc123xyz00",
                "discovered_at": "2026-04-07T09:00:00+08:00",
            }
        ]
        workflow.prepare_item = lambda item: {
            "video_path": "/tmp/video.mp4",
            "metadata_path": "/tmp/video.json",
        }
        workflow.now_iso = lambda: "2026-04-07T09:10:00+08:00"

        state = workflow.sync()

        self.assertEqual(state["items"]["abc123xyz00"]["status"], "prepared")
        self.assertEqual(state["items"]["abc123xyz00"]["metadata_path"], "/tmp/video.json")

    def test_publish_marks_item_scheduled(self):
        workflow = ChannelDailyPublishWorkflow(
            config={
                "channel_daily_publish": {
                    "channel_url": "https://www.youtube.com/@RosarioCooking/videos",
                    "publish_time": "19:05",
                }
            }
        )

        in_memory_state = {
            "items": {
                "abc": {
                    "youtube_id": "abc",
                    "source_url": "https://www.youtube.com/watch?v=abc",
                    "status": "prepared",
                    "is_new": True,
                    "discovered_at": "2026-04-07T09:00:00+08:00",
                    "metadata_path": "/tmp/video.json",
                }
            }
        }
        workflow.load_state = lambda: in_memory_state
        workflow.save_state = lambda state: in_memory_state.update(state)
        workflow.now_local = lambda: datetime.fromisoformat("2026-04-07T10:00:00+08:00")
        workflow.schedule_upload = lambda item, schedule_ts: {
            "success": True,
            "uploaded_bvid": "BV1xx",
        }

        result = workflow.publish()

        self.assertEqual(result["youtube_id"], "abc")
        self.assertEqual(in_memory_state["items"]["abc"]["status"], "scheduled")
        self.assertEqual(in_memory_state["items"]["abc"]["uploaded_bvid"], "BV1xx")
        self.assertFalse(in_memory_state["items"]["abc"]["is_new"])

    def test_prepare_item_reuses_main_pipeline_stages(self):
        workflow = ChannelDailyPublishWorkflow(
            config={"channel_daily_publish": {"channel_url": "https://www.youtube.com/@RosarioCooking/videos"}}
        )

        fake_main = SimpleNamespace(
            step_download_video=lambda args, executor: (None, "/tmp/video.mp4"),
            step_fetch_metadata=lambda args, target_path=None: "/tmp/video.json",
            step_process_cover=lambda args, metadata_path, download_future: True,
            wait_for_download=lambda future: "/tmp/video.mp4",
        )

        with patch.dict("sys.modules", {"main": fake_main}), \
             patch("core.modules.fetcher.load_metadata", return_value={"video_path": "/tmp/video.mp4"}), \
             patch("core.modules.fetcher.save_metadata") as save_metadata:
            result = workflow.prepare_item(
                {"source_url": "https://www.youtube.com/watch?v=abc123xyz00"}
            )

        self.assertEqual(result["video_path"], "/tmp/video.mp4")
        self.assertEqual(result["metadata_path"], "/tmp/video.json")
        save_metadata.assert_not_called()

    def test_schedule_upload_reuses_main_upload_and_cleanup(self):
        workflow = ChannelDailyPublishWorkflow(
            config={"channel_daily_publish": {"channel_url": "https://www.youtube.com/@RosarioCooking/videos"}}
        )

        fake_main = SimpleNamespace(
            step_upload=lambda args, schedule_ts, metadata_path: True,
            step_cleanup=lambda args, metadata_path: None,
        )

        with patch.dict("sys.modules", {"main": fake_main}), \
             patch("core.modules.fetcher.load_metadata", return_value={"uploaded_bvid": "BV9xx"}):
            result = workflow.schedule_upload({"metadata_path": "/tmp/video.json"}, 1234567890)

        self.assertTrue(result["success"])
        self.assertEqual(result["uploaded_bvid"], "BV9xx")

    def test_publish_marks_item_failed_when_schedule_upload_raises(self):
        workflow = ChannelDailyPublishWorkflow(
            config={
                "channel_daily_publish": {
                    "channel_url": "https://www.youtube.com/@RosarioCooking/videos",
                    "publish_time": "19:05",
                }
            }
        )

        in_memory_state = {
            "items": {
                "abc": {
                    "youtube_id": "abc",
                    "source_url": "https://www.youtube.com/watch?v=abc",
                    "status": "prepared",
                    "is_new": True,
                    "discovered_at": "2026-04-07T09:00:00+08:00",
                    "metadata_path": "/tmp/video.json",
                }
            }
        }
        workflow.load_state = lambda: in_memory_state
        workflow.save_state = lambda state: in_memory_state.update(state)
        workflow.now_local = lambda: datetime.fromisoformat("2026-04-07T10:00:00+08:00")

        def raise_upload_error(item, schedule_ts):
            raise RuntimeError("upload boom")

        workflow.schedule_upload = raise_upload_error

        result = workflow.publish()

        self.assertIsNone(result)
        self.assertEqual(in_memory_state["items"]["abc"]["status"], "failed_publish")
        self.assertIn("upload boom", in_memory_state["items"]["abc"]["last_error"])


if __name__ == "__main__":
    unittest.main()

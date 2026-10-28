import json
import os
import tempfile
import unittest

from core.publishing.manager import PublishManager
from core.publishing.models import AdapterCapabilities, PublishJob, ScheduleIntent
from core.publishing.registry import build_registry


class NativeScheduleAdapter:
    platform_name = "bilibili"
    capabilities = AdapterCapabilities(
        supports_native_schedule=True,
        supports_cover=True,
        supports_tags=True,
        supports_description=True,
    )

    def validate(self, job):
        return None

    def publish(self, job):
        raise AssertionError("publish should not be used")

    def schedule(self, job, publish_at):
        return {
            "platform": "bilibili",
            "status": "scheduled",
            "platform_content_id": "BV1xx",
            "platform_url": "https://example.com/BV1xx",
            "native_schedule": True,
            "scheduled_by": "platform",
            "scheduled_for": publish_at,
            "published_at": "",
            "last_error": "",
            "extra": {"aid": 1},
        }


class ManagedScheduleAdapter:
    platform_name = "kuaishou"
    capabilities = AdapterCapabilities(
        supports_native_schedule=False,
        supports_cover=True,
        supports_tags=True,
        supports_description=True,
    )

    def validate(self, job):
        return None

    def publish(self, job):
        return {
            "platform": "kuaishou",
            "status": "published",
            "platform_content_id": "photo_1",
            "platform_url": "https://example.com/photo_1",
            "native_schedule": False,
            "scheduled_by": "biyou",
            "scheduled_for": "",
            "published_at": "2026-04-08T19:05:00+08:00",
            "last_error": "",
            "extra": {},
        }

    def schedule(self, job, publish_at):
        raise AssertionError("schedule should not be used")


class RecordingPublishAdapter:
    platform_name = "bilibili"
    capabilities = AdapterCapabilities(
        supports_native_schedule=False,
        supports_cover=True,
        supports_tags=True,
        supports_description=True,
    )

    def __init__(self):
        self.publish_calls = 0

    def validate(self, job):
        return None

    def publish(self, job):
        self.publish_calls += 1
        return {
            "platform": "bilibili",
            "status": "published",
            "platform_content_id": "BV1xx",
            "platform_url": "https://example.com/BV1xx",
            "native_schedule": False,
            "scheduled_by": "biyou",
            "scheduled_for": "",
            "published_at": "2026-04-08T19:05:00+08:00",
            "last_error": "",
            "extra": {},
        }

    def schedule(self, job, publish_at):
        raise AssertionError("schedule should not be used")


class FailingValidationAdapter:
    platform_name = "douyin"
    capabilities = AdapterCapabilities(
        supports_native_schedule=False,
        supports_cover=True,
        supports_tags=True,
        supports_description=True,
    )

    def validate(self, job):
        raise ValueError("invalid target")

    def publish(self, job):
        raise AssertionError("publish should not be used")

    def schedule(self, job, publish_at):
        raise AssertionError("schedule should not be used")


class FailingPublishAdapter:
    platform_name = "douyin"
    capabilities = AdapterCapabilities(
        supports_native_schedule=False,
        supports_cover=True,
        supports_tags=True,
        supports_description=True,
    )

    def validate(self, job):
        return None

    def publish(self, job):
        raise RuntimeError("upload widget not found")

    def schedule(self, job, publish_at):
        raise AssertionError("schedule should not be used")


class RecordingKuaishouAdapter:
    platform_name = "kuaishou"
    capabilities = AdapterCapabilities(
        supports_native_schedule=False,
        supports_cover=True,
        supports_tags=True,
        supports_description=True,
    )

    def __init__(self):
        self.publish_calls = 0

    def validate(self, job):
        return None

    def publish(self, job):
        self.publish_calls += 1
        return {
            "platform": "kuaishou",
            "status": "published",
            "platform_content_id": "ks_1",
            "platform_url": "https://example.com/ks_1",
            "native_schedule": False,
            "scheduled_by": "biyou",
            "scheduled_for": "",
            "published_at": "2026-04-08T19:06:00+08:00",
            "last_error": "",
            "extra": {},
        }

    def schedule(self, job, publish_at):
        raise AssertionError("schedule should not be used")


class TestPublishingManager(unittest.TestCase):
    def test_build_registry_uses_platform_name_keys(self):
        registry = build_registry(NativeScheduleAdapter(), ManagedScheduleAdapter())

        self.assertEqual(set(registry), {"bilibili", "kuaishou"})
        self.assertIsInstance(registry["bilibili"], NativeScheduleAdapter)
        self.assertIsInstance(registry["kuaishou"], ManagedScheduleAdapter)

    def test_native_schedule_uses_adapter_schedule(self):
        manager = PublishManager(
            adapters={"bilibili": NativeScheduleAdapter()},
            scheduler_state={"jobs": []},
        )
        metadata = {}
        job = PublishJob(
            platform_targets=["bilibili"],
            video_path="/tmp/video.mp4",
            title="T",
            description="D",
            cover_path="/tmp/cover.jpg",
            tags=["tag"],
            metadata=metadata,
            metadata_path="/tmp/video.json",
        )
        job.schedule_intent = ScheduleIntent(
            mode="publish_at",
            publish_at="2026-04-08T19:05:00+08:00",
        )

        result = manager.execute(job)

        self.assertIs(result, metadata)
        self.assertEqual(metadata["publish_records"]["bilibili"]["scheduled_by"], "platform")
        self.assertEqual(metadata["publish_records"]["bilibili"]["native_schedule"], True)
        self.assertEqual(manager.scheduler_state["jobs"], [])

    def test_non_native_schedule_enqueues_biyou_job(self):
        manager = PublishManager(
            adapters={"kuaishou": ManagedScheduleAdapter()},
            scheduler_state={"jobs": []},
        )
        metadata = {}
        job = PublishJob(
            platform_targets=["kuaishou"],
            video_path="/tmp/video.mp4",
            title="T",
            description="D",
            cover_path="/tmp/cover.jpg",
            tags=["tag"],
            metadata=metadata,
            metadata_path="/tmp/video.json",
        )
        job.schedule_intent = ScheduleIntent(
            mode="publish_at",
            publish_at="2026-04-08T19:05:00+08:00",
        )

        result = manager.execute(job)

        self.assertIs(result, metadata)
        self.assertEqual(metadata["publish_records"]["kuaishou"]["scheduled_by"], "biyou")
        self.assertEqual(metadata["publish_records"]["kuaishou"]["native_schedule"], False)
        self.assertEqual(len(manager.scheduler_state["jobs"]), 1)

    def test_execute_prefights_all_targets_before_side_effects(self):
        first_adapter = RecordingPublishAdapter()
        manager = PublishManager(
            adapters={"bilibili": first_adapter, "douyin": FailingValidationAdapter()},
            scheduler_state={"jobs": []},
        )
        metadata = {}
        job = PublishJob(
            platform_targets=["bilibili", "douyin"],
            video_path="/tmp/video.mp4",
            title="T",
            description="D",
            cover_path="/tmp/cover.jpg",
            tags=["tag"],
            metadata=metadata,
            metadata_path="/tmp/video.json",
        )
        job.schedule_intent = ScheduleIntent(mode="publish_now", publish_at="")

        with self.assertRaises(ValueError):
            manager.execute(job)

        self.assertEqual(first_adapter.publish_calls, 0)
        self.assertEqual(metadata, {})
        self.assertEqual(manager.scheduler_state["jobs"], [])

    def test_execute_records_failure_and_continues_after_later_publish_error(self):
        first_adapter = RecordingPublishAdapter()
        last_adapter = RecordingKuaishouAdapter()
        manager = PublishManager(
            adapters={
                "bilibili": first_adapter,
                "douyin": FailingPublishAdapter(),
                "kuaishou": last_adapter,
            },
            scheduler_state={"jobs": []},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_path = os.path.join(tmpdir, "video.json")
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump({}, f)

            metadata = {}
            job = PublishJob(
                platform_targets=["bilibili", "douyin", "kuaishou"],
                video_path="/tmp/video.mp4",
                title="T",
                description="D",
                cover_path="/tmp/cover.jpg",
                tags=["tag"],
                metadata=metadata,
                metadata_path=metadata_path,
            )
            job.schedule_intent = ScheduleIntent(mode="publish_now", publish_at="")

            result = manager.execute(job)

            with open(metadata_path, "r", encoding="utf-8") as f:
                persisted = json.load(f)

        self.assertIs(result, metadata)
        self.assertEqual(first_adapter.publish_calls, 1)
        self.assertEqual(last_adapter.publish_calls, 1)
        self.assertEqual(
            persisted["publish_records"]["bilibili"]["platform_content_id"], "BV1xx"
        )
        self.assertEqual(
            persisted["publish_records"]["douyin"]["status"], "failed_manual"
        )
        self.assertIn(
            "upload widget not found",
            persisted["publish_records"]["douyin"]["last_error"],
        )
        self.assertEqual(
            persisted["publish_records"]["kuaishou"]["platform_content_id"], "ks_1"
        )
        self.assertEqual(persisted["uploaded_bvid"], "BV1xx")


if __name__ == "__main__":
    unittest.main()

import unittest
from dataclasses import FrozenInstanceError

from core.publishing.models import (
    AdapterCapabilities,
    PublishJob,
    PublishResult,
    ScheduleIntent,
)


class TestPublishingModels(unittest.TestCase):
    def test_publish_job_defaults_to_publish_now(self):
        job = PublishJob(
            platform_targets=["bilibili"],
            video_path="/tmp/video.mp4",
            title="Morning Workout",
            description="A simple test upload",
            cover_path="/tmp/cover.jpg",
            tags=["fitness", "cardio"],
        )

        self.assertEqual(job.schedule_intent.mode, "publish_now")
        self.assertEqual(job.platform_payloads, {})

    def test_publish_result_keeps_schedule_metadata(self):
        result = PublishResult(
            platform="douyin",
            status="scheduled",
            platform_content_id="item_123",
            platform_url="https://example.com/item_123",
            native_schedule=False,
            scheduled_by="biyou",
            scheduled_for="2026-04-08T19:05:00+08:00",
            published_at="",
            last_error="",
        )

        self.assertFalse(result.native_schedule)
        self.assertEqual(result.scheduled_by, "biyou")

    def test_adapter_capabilities_expose_native_flags(self):
        caps = AdapterCapabilities(
            supports_native_schedule=False,
            supports_cover=True,
            supports_tags=False,
            supports_description=True,
        )

        self.assertFalse(caps.supports_native_schedule)
        self.assertTrue(caps.supports_cover)

    def test_adapter_capabilities_are_immutable(self):
        caps = AdapterCapabilities(
            supports_native_schedule=False,
            supports_cover=True,
            supports_tags=False,
            supports_description=True,
        )

        with self.assertRaises(FrozenInstanceError):
            caps.supports_cover = False

    def test_schedule_intent_accepts_publish_at_mode(self):
        intent = ScheduleIntent(mode="publish_at", publish_at="2026-04-08T19:05:00+08:00")

        self.assertEqual(intent.mode, "publish_at")
        self.assertEqual(intent.publish_at, "2026-04-08T19:05:00+08:00")

    def test_schedule_intent_rejects_empty_publish_at_for_publish_at_mode(self):
        with self.assertRaises(ValueError):
            ScheduleIntent(mode="publish_at", publish_at="")

    def test_schedule_intent_rejects_whitespace_only_publish_at_for_publish_at_mode(self):
        with self.assertRaises(ValueError):
            ScheduleIntent(mode="publish_at", publish_at="   ")

    def test_schedule_intent_rejects_non_string_publish_at_for_publish_at_mode(self):
        with self.assertRaises(ValueError):
            ScheduleIntent(mode="publish_at", publish_at=123)

    def test_schedule_intent_rejects_publish_at_value_for_publish_now_mode(self):
        with self.assertRaises(ValueError):
            ScheduleIntent(mode="publish_now", publish_at="2026-04-08T19:05:00+08:00")

    def test_publish_result_accepts_approved_status(self):
        result = PublishResult(
            platform="douyin",
            status="failed_retryable",
            platform_content_id="item_123",
            platform_url="https://example.com/item_123",
            native_schedule=False,
            scheduled_by="biyou",
            scheduled_for="2026-04-08T19:05:00+08:00",
            published_at="",
            last_error="temporary failure",
        )

        self.assertEqual(result.status, "failed_retryable")

    def test_base_adapter_accepts_approved_metadata(self):
        from core.publishing.adapters.base import BasePublishingAdapter

        class ApprovedPublishingAdapter(BasePublishingAdapter):
            platform_name = "demo"
            stability_tier = "beta"
            execution_mode = "automation"

            def validate(self, job):
                return None

            def publish(self, job):
                return None

            def schedule(self, job, publish_at):
                return None

        self.assertEqual(ApprovedPublishingAdapter.stability_tier, "beta")
        self.assertEqual(ApprovedPublishingAdapter.execution_mode, "automation")

    def test_base_adapter_rejects_empty_platform_name_for_concrete_subclass(self):
        from core.publishing.adapters.base import BasePublishingAdapter

        with self.assertRaises(ValueError):
            class EmptyNamePublishingAdapter(BasePublishingAdapter):
                platform_name = ""
                stability_tier = "stable"
                execution_mode = "api"

                def validate(self, job):
                    return None

                def publish(self, job):
                    return None

                def schedule(self, job, publish_at):
                    return None

    def test_base_adapter_rejects_blank_platform_name_for_concrete_subclass(self):
        from core.publishing.adapters.base import BasePublishingAdapter

        with self.assertRaises(ValueError):
            class BlankNamePublishingAdapter(BasePublishingAdapter):
                platform_name = "   "
                stability_tier = "stable"
                execution_mode = "api"

                def validate(self, job):
                    return None

                def publish(self, job):
                    return None

                def schedule(self, job, publish_at):
                    return None

    def test_base_adapter_rejects_malformed_callable_contract(self):
        from core.publishing.adapters.base import BasePublishingAdapter

        with self.assertRaises(ValueError):
            class BadSignaturePublishingAdapter(BasePublishingAdapter):
                platform_name = "demo"
                stability_tier = "stable"
                execution_mode = "api"

                def validate(self, job, extra):
                    return None

                def publish(self, job):
                    return None

                def schedule(self, job, publish_at):
                    return None

    def test_base_adapter_rejects_non_capabilities_object(self):
        from core.publishing.adapters.base import BasePublishingAdapter

        with self.assertRaises(ValueError):
            class BadCapabilitiesPublishingAdapter(BasePublishingAdapter):
                platform_name = "demo"
                stability_tier = "stable"
                execution_mode = "api"
                capabilities = {"supports_cover": True}

                def validate(self, job):
                    return None

                def publish(self, job):
                    return None

                def schedule(self, job, publish_at):
                    return None

    def test_base_adapter_rejects_non_callable_override(self):
        from core.publishing.adapters.base import BasePublishingAdapter

        with self.assertRaises(ValueError):
            class NonCallablePublishingAdapter(BasePublishingAdapter):
                platform_name = "demo"
                stability_tier = "stable"
                execution_mode = "api"
                validate = None

                def publish(self, job):
                    return None

                def schedule(self, job, publish_at):
                    return None

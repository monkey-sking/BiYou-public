import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, call

from core.publishing.adapters.kuaishou import (
    KuaishouAutomationAdapter,
    normalize_kuaishou_publish_payload,
)
from core.publishing.models import PublishJob


class TestKuaishouAutomationAdapter(unittest.TestCase):
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

    def _build_adapter(self, selectors=None):
        return KuaishouAutomationAdapter(
            config={},
            selectors=selectors or {},
        )

    def test_normalize_publish_payload_keeps_core_fields(self):
        job = self._make_job()

        payload = normalize_kuaishou_publish_payload(job)

        self.assertEqual(payload["title"], "Morning Workout")
        self.assertEqual(payload["video_path"], "/tmp/video.mp4")
        self.assertEqual(payload["cover_path"], "/tmp/cover.jpg")
        self.assertIn("#fitness", payload["description"])
        self.assertIn("#warmup", payload["description"])
        self.assertNotIn("#strength", payload["description"])
        self.assertNotIn("#extra", payload["description"])

    def test_normalize_publish_payload_truncates_caption_to_kuaishou_limit(self):
        job = self._make_job(
            title="Morning Workout",
            description="A" * 600,
            tags=[],
        )

        payload = normalize_kuaishou_publish_payload(job)

        self.assertEqual(len(payload["caption"]), 460)
        self.assertTrue(payload["caption"].startswith("Morning Workout"))

    def test_normalize_publish_payload_strips_link_markup_and_duplicate_hashtag_tail(self):
        job = self._make_job(
            title="Morning Workout",
            description=(
                "Follow the plan here: [Trybe](https://trybe.do/example)\n\n"
                "Keep moving.\n\n"
                "#fitness #cardio #home"
            ),
            tags=["fitness", "cardio", "home", "warmup"],
        )

        payload = normalize_kuaishou_publish_payload(job)

        self.assertNotIn("https://", payload["caption"])
        self.assertNotIn("[Trybe](", payload["caption"])
        self.assertEqual(payload["caption"].count("#fitness"), 1)
        self.assertEqual(payload["caption"].count("#cardio"), 1)

    def test_normalize_publish_payload_preserves_extra_platform_fields(self):
        job = self._make_job(
            platform_payloads={
                "kuaishou": {
                    "visibility": "private",
                    "allow_comment": False,
                    "topic": "居家训练",
                }
            }
        )

        payload = normalize_kuaishou_publish_payload(job)

        self.assertEqual(payload["visibility"], "private")
        self.assertFalse(payload["allow_comment"])
        self.assertEqual(payload["topic"], "居家训练")

    def test_capabilities_and_validate_requirements_match_beta_automation(self):
        adapter = self._build_adapter()

        self.assertEqual(adapter.stability_tier, "beta")
        self.assertEqual(adapter.execution_mode, "automation")
        self.assertFalse(adapter.capabilities.supports_native_schedule)
        self.assertTrue(adapter.capabilities.supports_cover)
        self.assertFalse(adapter.capabilities.supports_tags)
        self.assertTrue(adapter.capabilities.supports_description)

        adapter.validate(self._make_job())

        with self.assertRaisesRegex(ValueError, "video_path, cover_path, and title"):
            adapter.validate(self._make_job(video_path=""))

        with self.assertRaisesRegex(ValueError, "video_path, cover_path, and title"):
            adapter.validate(self._make_job(cover_path=""))

        with self.assertRaisesRegex(ValueError, "video_path, cover_path, and title"):
            adapter.validate(self._make_job(title=""))

    def test_publish_runs_async_flow_and_maps_standard_result(self):
        adapter = self._build_adapter(
            selectors={
                "publish_url": "https://cp.kuaishou.com",
                "locators": {
                    "title": ["input"],
                    "description": ["textarea"],
                    "video_upload": ["input[type='file']"],
                    "cover_upload": ["input[type='file']"],
                    "publish_button": ["button"],
                },
            }
        )
        job = self._make_job()
        payload = normalize_kuaishou_publish_payload(job)
        adapter._publish_async = AsyncMock(
            return_value={
                "platform_content_id": "kuaishou_123",
                "platform_url": "https://www.kuaishou.com/short-video/kuaishou_123",
                "published_at": "2026-04-09T12:00:00+08:00",
                "last_error": "",
            }
        )

        result = adapter.publish(job)

        self.assertEqual(result["platform"], "kuaishou")
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["platform_content_id"], "kuaishou_123")
        self.assertEqual(result["platform_url"], "https://www.kuaishou.com/short-video/kuaishou_123")
        self.assertEqual(result["scheduled_by"], "biyou")
        self.assertEqual(result["extra"]["cover_path"], "/tmp/cover.jpg")
        self.assertIn("#fitness", result["extra"]["description"])
        adapter._publish_async.assert_awaited_once()

    def test_publish_async_waits_for_form_fields_before_cover_upload(self):
        adapter = self._build_adapter(
            selectors={
                "publish_url": "https://cp.kuaishou.com",
                "locators": {
                    "title": ["input"],
                    "description": ["textarea"],
                    "video_upload": ["input[type='file']"],
                    "cover_upload": ["input[data-cover='1']"],
                    "publish_button": ["button"],
                },
            }
        )
        payload = normalize_kuaishou_publish_payload(self._make_job())
        playwright = AsyncMock()
        context = AsyncMock()
        page = AsyncMock()
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        events = []

        async def record_set_file(target, selectors, path):
            events.append(("set_file", tuple(selectors), path))
            return None

        async def record_wait_for_form(target, selectors, **kwargs):
            events.append(("wait_form", tuple(selectors)))
            return (page, "input")

        adapter._set_file_first = AsyncMock(side_effect=record_set_file)
        adapter._wait_for_any_selector_across_targets = AsyncMock(
            side_effect=record_wait_for_form
        )
        adapter._wait_for_text_to_clear = AsyncMock(return_value="页面已准备好")
        adapter._fill_first_across_targets = AsyncMock(return_value=page)
        adapter._click_first_across_targets = AsyncMock(return_value=page)
        adapter._apply_option_controls_across_targets = AsyncMock(return_value=page)
        adapter._verify_publish_result = AsyncMock(
            return_value={
                "verified": True,
                "platform_content_id": "kuaishou_123",
                "platform_url": "https://cp.kuaishou.com/article/manage/video",
                "published_at": "",
                "last_error": "",
            }
        )

        result = adapter.run_async(adapter._publish_async(payload))

        self.assertEqual(result["platform_content_id"], "kuaishou_123")
        self.assertEqual(result["last_error"], "")
        self.assertEqual(
            events[0],
            ("set_file", ("input[type='file']",), payload["video_path"]),
        )
        self.assertEqual(events[1], ("wait_form", ("input", "textarea")))
        self.assertEqual(
            adapter._set_file_first.await_args_list,
            [
                call(page, ["input[type='file']"], payload["video_path"]),
                call(page, ["input[data-cover='1']"], payload["cover_path"]),
            ],
        )
        self.assertLess(
            events.index(("wait_form", ("input", "textarea"))),
            events.index(("set_file", ("input[data-cover='1']",), payload["cover_path"])),
        )
        adapter._wait_for_any_selector_across_targets.assert_awaited_once_with(
            page,
            ["input", "textarea"],
            timeout_ms=600000,
            poll_ms=1000,
        )
        self.assertEqual(
            adapter._fill_first_across_targets.await_args_list,
            [
                call(page, ["input"], payload["title"]),
                call(page, ["textarea"], payload["description"]),
            ],
        )
        adapter._apply_option_controls_across_targets.assert_awaited_once_with(
            page,
            payload,
            {},
            active_target=page,
        )
        adapter._wait_for_text_to_clear.assert_awaited_once_with(
            page,
            ["上传中", "处理中"],
            timeout_ms=600000,
            poll_ms=1000,
        )
        adapter._click_first_across_targets.assert_awaited_once_with(page, ["button"])
        adapter._verify_publish_result.assert_awaited_once()

    def test_publish_async_applies_configured_option_controls(self):
        adapter = self._build_adapter(
            selectors={
                "publish_url": "https://cp.kuaishou.com",
                "locators": {
                    "title": ["input"],
                    "description": ["textarea"],
                    "video_upload": ["input[type='file']"],
                    "cover_upload": ["input[data-cover='1']"],
                    "publish_button": ["button"],
                },
                "option_controls": {
                    "visibility": {
                        "kind": "choice",
                        "options": {"private": ["div:text-is('仅自己可见')"]},
                    }
                },
            }
        )
        payload = normalize_kuaishou_publish_payload(
            self._make_job(platform_payloads={"kuaishou": {"visibility": "private"}})
        )
        playwright = AsyncMock()
        context = AsyncMock()
        page = AsyncMock()
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        adapter._set_file_first = AsyncMock(return_value=None)
        adapter._wait_for_any_selector_across_targets = AsyncMock(return_value=(page, "input"))
        adapter._wait_for_text_to_clear = AsyncMock(return_value="页面已准备好")
        adapter._fill_first_across_targets = AsyncMock(return_value=page)
        adapter._click_first_across_targets = AsyncMock(return_value=page)
        adapter._apply_option_controls_across_targets = AsyncMock(return_value=page)
        adapter._verify_publish_result = AsyncMock(
            return_value={
                "verified": True,
                "platform_content_id": "kuaishou_123",
                "platform_url": "",
                "published_at": "",
                "last_error": "",
            }
        )

        adapter.run_async(adapter._publish_async(payload))

        adapter._apply_option_controls_across_targets.assert_awaited_once_with(
            page,
            payload,
            adapter.selectors["option_controls"],
            active_target=page,
        )

    def test_publish_async_keeps_active_frame_for_processing_checks_when_no_option_controls(self):
        adapter = self._build_adapter(
            selectors={
                "publish_url": "https://cp.kuaishou.com",
                "locators": {
                    "title": [],
                    "description": ["div[role='textbox']"],
                    "video_upload": ["input[type='file']"],
                    "cover_upload": [],
                    "publish_button": ["button:text-is('发布')"],
                },
            }
        )
        payload = normalize_kuaishou_publish_payload(self._make_job())
        playwright = AsyncMock()
        context = AsyncMock()
        page = AsyncMock()
        frame = AsyncMock()
        page.frames = [frame]
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        adapter._set_file_first = AsyncMock(return_value=None)
        adapter._wait_for_any_selector_across_targets = AsyncMock(
            return_value=(frame, "div[role='textbox']")
        )
        adapter._fill_first_across_targets = AsyncMock(return_value=frame)
        adapter._wait_for_text_to_clear = AsyncMock(return_value="页面已准备好")
        adapter._click_first_across_targets = AsyncMock(return_value=frame)
        adapter._verify_publish_result = AsyncMock(
            return_value={
                "verified": True,
                "platform_content_id": "kuaishou_123",
                "platform_url": "",
                "published_at": "",
                "last_error": "",
            }
        )

        adapter.run_async(adapter._publish_async(payload))

        adapter._wait_for_text_to_clear.assert_awaited_once_with(
            frame,
            ["上传中", "处理中"],
            timeout_ms=600000,
            poll_ms=1000,
        )

    def test_publish_async_supports_description_only_flow(self):
        adapter = self._build_adapter(
            selectors={
                "publish_url": "https://cp.kuaishou.com",
                "locators": {
                    "title": [],
                    "description": ["[contenteditable='true']"],
                    "video_upload": ["input[type='file']"],
                    "cover_upload": [],
                    "publish_button": ["button:text-is('发布')"],
                },
            }
        )
        payload = normalize_kuaishou_publish_payload(self._make_job())
        playwright = AsyncMock()
        context = AsyncMock()
        page = AsyncMock()
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        adapter._set_file_first = AsyncMock(return_value=None)
        adapter._wait_for_any_selector = AsyncMock(return_value="[contenteditable='true']")
        adapter._wait_for_text_to_clear = AsyncMock(return_value="页面已准备好")
        adapter._fill_first = AsyncMock(return_value=None)
        adapter._click_first = AsyncMock(return_value=None)
        adapter._verify_publish_result = AsyncMock(
            return_value={
                "verified": True,
                "platform_content_id": "kuaishou_123",
                "platform_url": "https://cp.kuaishou.com/article/manage/video",
                "published_at": "",
                "last_error": "",
            }
        )

        result = adapter.run_async(adapter._publish_async(payload))

        self.assertEqual(result["last_error"], "")
        adapter._set_file_first.assert_awaited_once_with(
            page,
            ["input[type='file']"],
            payload["video_path"],
        )
        adapter._wait_for_any_selector.assert_awaited_once_with(
            page,
            ["[contenteditable='true']"],
            timeout_ms=600000,
            poll_ms=1000,
        )
        adapter._fill_first.assert_awaited_once_with(
            page,
            ["[contenteditable='true']"],
            payload["caption"],
        )
        adapter._click_first.assert_awaited_once_with(page, ["button:text-is('发布')"])
        adapter._verify_publish_result.assert_awaited_once()

    def test_wait_for_any_selector_across_targets_falls_back_to_frame(self):
        adapter = self._build_adapter()
        page = AsyncMock()
        frame = AsyncMock()
        page.frames = [frame]
        adapter._wait_for_any_selector = AsyncMock(
            side_effect=[RuntimeError("page miss"), "div[role='textbox']"]
        )

        target, selector = adapter.run_async(
            adapter._wait_for_any_selector_across_targets(
                page,
                ["div[role='textbox']"],
                timeout_ms=0,
                poll_ms=1,
            )
        )

        self.assertIs(target, frame)
        self.assertEqual(selector, "div[role='textbox']")
        self.assertEqual(
            adapter._wait_for_any_selector.await_args_list,
            [
                call(page, ["div[role='textbox']"], timeout_ms=0, poll_ms=1),
                call(frame, ["div[role='textbox']"], timeout_ms=0, poll_ms=1),
            ],
        )

    def test_publish_async_stops_before_publish_when_video_is_still_processing(self):
        adapter = self._build_adapter(
            selectors={
                "publish_url": "https://cp.kuaishou.com",
                "locators": {
                    "title": [],
                    "description": ["[contenteditable='true']"],
                    "video_upload": ["input[type='file']"],
                    "cover_upload": [],
                    "publish_button": ["button:text-is('发布')"],
                },
            }
        )
        payload = normalize_kuaishou_publish_payload(self._make_job())
        playwright = AsyncMock()
        context = AsyncMock()
        page = AsyncMock()
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        adapter._set_file_first = AsyncMock(return_value=None)
        adapter._wait_for_any_selector = AsyncMock(return_value="[contenteditable='true']")
        adapter._wait_for_text_to_clear = AsyncMock(return_value="视频上传中 处理中")
        adapter._fill_first = AsyncMock(return_value=None)
        adapter._click_first = AsyncMock(return_value=None)
        adapter._verify_publish_result = AsyncMock(return_value={})

        result = adapter.run_async(adapter._publish_async(payload))

        self.assertFalse(result["verified"])
        self.assertIn("still processing", result["last_error"])
        adapter._click_first.assert_not_awaited()
        adapter._verify_publish_result.assert_not_awaited()

    def test_publish_async_stops_before_publish_when_page_has_validation_error(self):
        adapter = self._build_adapter(
            selectors={
                "publish_url": "https://cp.kuaishou.com",
                "locators": {
                    "title": [],
                    "description": ["[contenteditable='true']"],
                    "video_upload": ["input[type='file']"],
                    "cover_upload": [],
                    "publish_button": ["button:text-is('发布')"],
                },
            }
        )
        payload = normalize_kuaishou_publish_payload(self._make_job())
        playwright = AsyncMock()
        context = AsyncMock()
        page = AsyncMock()
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        adapter._set_file_first = AsyncMock(return_value=None)
        adapter._wait_for_any_selector = AsyncMock(return_value="[contenteditable='true']")
        adapter._wait_for_text_to_clear = AsyncMock(return_value="作品描述超过字数限制")
        adapter._fill_first = AsyncMock(return_value=None)
        adapter._click_first = AsyncMock(return_value=None)
        adapter._verify_publish_result = AsyncMock(return_value={})

        result = adapter.run_async(adapter._publish_async(payload))

        self.assertFalse(result["verified"])
        self.assertIn("validation", result["last_error"])
        adapter._click_first.assert_not_awaited()
        adapter._verify_publish_result.assert_not_awaited()

    def test_verify_publish_result_requires_manager_status_term(self):
        adapter = self._build_adapter()
        payload = normalize_kuaishou_publish_payload(self._make_job())
        verify_page = AsyncMock()
        verify_page.goto = AsyncMock()
        verify_page.close = AsyncMock()
        context = AsyncMock()
        context.new_page = AsyncMock(return_value=verify_page)
        adapter._wait_for_text = AsyncMock(return_value="Morning Workout\n审核中\n刚刚")

        result = adapter.run_async(adapter._verify_publish_result(context, payload))

        self.assertTrue(result["verified"])
        self.assertEqual(result["platform_url"], "https://cp.kuaishou.com/article/manage/video")
        self.assertEqual(result["last_error"], "")

    def test_verify_publish_result_rejects_title_only_match_without_manager_status(self):
        adapter = self._build_adapter()
        payload = normalize_kuaishou_publish_payload(self._make_job())
        verify_page = AsyncMock()
        verify_page.goto = AsyncMock()
        verify_page.close = AsyncMock()
        context = AsyncMock()
        context.new_page = AsyncMock(return_value=verify_page)
        adapter._wait_for_text = AsyncMock(return_value="Morning Workout")

        result = adapter.run_async(adapter._verify_publish_result(context, payload))

        self.assertFalse(result["verified"])
        self.assertIn("could not be verified", result["last_error"])

    def test_verify_publish_result_uses_manager_row_dom_when_body_text_is_empty(self):
        adapter = self._build_adapter()
        payload = normalize_kuaishou_publish_payload(self._make_job())
        verify_page = AsyncMock()
        verify_page.goto = AsyncMock()
        verify_page.close = AsyncMock()
        verify_page.evaluate = AsyncMock(
            return_value=[
                {
                    "title": "Morning Workout",
                    "status": "已发布",
                    "text": "Morning Workout\n已发布\n2026-04-10 10:46",
                    "published_at": "2026-04-10 10:46",
                    "platform_content_id": "3xkq7w9abcde",
                    "platform_url": "https://www.kuaishou.com/short-video/3xkq7w9abcde",
                }
            ]
        )
        context = AsyncMock()
        context.new_page = AsyncMock(return_value=verify_page)
        adapter._wait_for_text = AsyncMock(return_value="")

        result = adapter.run_async(adapter._verify_publish_result(context, payload))

        self.assertTrue(result["verified"])
        self.assertEqual(result["platform_content_id"], "3xkq7w9abcde")
        self.assertEqual(result["platform_url"], "https://www.kuaishou.com/short-video/3xkq7w9abcde")
        self.assertEqual(result["last_error"], "")

    def test_verify_publish_result_builds_public_url_from_bare_manager_row_content_id(self):
        adapter = self._build_adapter()
        payload = normalize_kuaishou_publish_payload(self._make_job())
        verify_page = AsyncMock()
        verify_page.goto = AsyncMock()
        verify_page.close = AsyncMock()
        verify_page.evaluate = AsyncMock(
            return_value=[
                {
                    "title": "Morning Workout",
                    "status": "审核中",
                    "text": "Morning Workout\n审核中\n2026-04-10 10:46",
                    "published_at": "2026-04-10 10:46",
                    "platform_content_id": "3xkq7w9abcde",
                    "platform_url": "",
                }
            ]
        )
        context = AsyncMock()
        context.new_page = AsyncMock(return_value=verify_page)
        adapter._wait_for_text = AsyncMock(return_value="")

        result = adapter.run_async(adapter._verify_publish_result(context, payload))

        self.assertTrue(result["verified"])
        self.assertEqual(result["platform_content_id"], "3xkq7w9abcde")
        self.assertEqual(result["platform_url"], "https://www.kuaishou.com/short-video/3xkq7w9abcde")
        self.assertEqual(result["last_error"], "")

    def test_publish_marks_result_retryable_when_verification_does_not_confirm_publish(self):
        adapter = self._build_adapter()
        job = self._make_job()
        adapter._publish_async = AsyncMock(
            return_value={
                "verified": False,
                "platform_content_id": "",
                "platform_url": "",
                "published_at": "",
                "last_error": "Kuaishou publish result could not be verified from video manager",
            }
        )

        result = adapter.publish(job)

        self.assertEqual(result["status"], "failed_retryable")
        self.assertIn("could not be verified", result["last_error"])

    def test_publish_returns_failed_manual_when_video_exceeds_duration_limit(self):
        adapter = self._build_adapter()
        job = self._make_job(metadata={"duration": 6262})
        adapter._publish_async = AsyncMock(return_value={})

        result = adapter.publish(job)

        self.assertEqual(result["status"], "failed_manual")
        self.assertIn("up to 60:00", result["last_error"])
        adapter._publish_async.assert_not_awaited()

    def test_publish_prefers_kuaishou_override_video_path_even_when_metadata_duration_is_too_long(self):
        adapter = self._build_adapter()
        adapter._publish_async = AsyncMock(return_value={})

        with tempfile.TemporaryDirectory() as tmpdir:
            base_video_path = os.path.join(tmpdir, "video.mp4")
            trim_video_path = os.path.join(tmpdir, "video_trim.mp4")
            trim_info_path = os.path.join(tmpdir, "video_trim.info.json")
            with open(base_video_path, "wb") as f:
                f.write(b"")
            with open(trim_video_path, "wb") as f:
                f.write(b"")
            with open(trim_info_path, "w", encoding="utf-8") as f:
                json.dump({"duration": 3599}, f)

            job = self._make_job(
                video_path=base_video_path,
                metadata={"duration": 6262},
                platform_payloads={"kuaishou": {"video_path": trim_video_path}},
            )
            result = adapter.publish(job)

        self.assertNotIn("60:00", result["last_error"])
        adapter._publish_async.assert_awaited_once()

    def test_schedule_returns_biyou_managed_result_with_normalized_payload(self):
        adapter = self._build_adapter()
        job = self._make_job()

        result = adapter.schedule(job, "2026-04-09T12:00:00+08:00")

        self.assertEqual(result["platform"], "kuaishou")
        self.assertEqual(result["status"], "scheduled")
        self.assertEqual(result["scheduled_by"], "biyou")
        self.assertEqual(result["scheduled_for"], "2026-04-09T12:00:00+08:00")
        self.assertEqual(result["extra"]["cover_path"], "/tmp/cover.jpg")
        self.assertIn("#fitness", result["extra"]["description"])


if __name__ == "__main__":
    unittest.main()

import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, call, patch

from core.publishing.adapters.douyin import (
    VERIFY_TIMEOUT_MS as DOUYIN_VERIFY_TIMEOUT_MS,
    DouyinAutomationAdapter,
    extract_work_list_record,
    normalize_douyin_publish_payload,
)
from core.publishing.models import PublishJob


class TestDouyinAutomationAdapter(unittest.TestCase):
    def _make_job(self, **overrides):
        data = {
            "platform_targets": ["douyin"],
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

    def test_normalize_publish_payload_keeps_core_fields(self):
        job = self._make_job()

        payload = normalize_douyin_publish_payload(job)

        self.assertEqual(payload["title"], "Morning Workout")
        self.assertEqual(payload["video_path"], "/tmp/video.mp4")
        self.assertEqual(payload["cover_path"], "/tmp/cover.jpg")
        self.assertIn("#fitness", payload["description"])
        self.assertIn("#strength", payload["description"])
        self.assertNotIn("#extra", payload["description"])

    def test_normalize_publish_payload_truncates_to_douyin_ui_limits(self):
        job = self._make_job(
            title="居家40分钟全身燃脂：无需器械，练就紧致线条再多一点再长一点继续补字",
            description="A" * 1200,
            tags=[],
        )

        payload = normalize_douyin_publish_payload(job)

        self.assertEqual(len(payload["title"]), 30)
        self.assertEqual(len(payload["description"]), 1000)

    def test_normalize_publish_payload_prefers_platform_overrides(self):
        job = self._make_job(
            platform_payloads={
                "douyin": {
                    "title": "抖音专用标题",
                    "description": "抖音专用简介",
                    "tags": ["暴汗", "燃脂"],
                    "video_path": "/tmp/douyin.mp4",
                }
            }
        )

        payload = normalize_douyin_publish_payload(job)

        self.assertEqual(payload["title"], "抖音专用标题")
        self.assertEqual(payload["video_path"], "/tmp/douyin.mp4")
        self.assertIn("#暴汗", payload["description"])
        self.assertIn("抖音专用简介", payload["description"])

    def test_normalize_publish_payload_preserves_extra_platform_fields(self):
        job = self._make_job(
            platform_payloads={
                "douyin": {
                    "visibility": "friends",
                    "allow_comment": False,
                    "allow_duet": False,
                }
            }
        )

        payload = normalize_douyin_publish_payload(job)

        self.assertEqual(payload["visibility"], "friends")
        self.assertFalse(payload["allow_comment"])
        self.assertFalse(payload["allow_duet"])

    def test_capabilities_and_validate_requirements_match_beta_automation(self):
        adapter = DouyinAutomationAdapter(config={}, selectors={})

        self.assertEqual(adapter.platform_name, "douyin")
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
            adapter.validate(self._make_job(title=""))

        with self.assertRaisesRegex(ValueError, "video_path, cover_path, and title"):
            adapter.validate(self._make_job(cover_path=""))

    def test_extract_work_list_record_returns_platform_identity_for_matching_title(self):
        record = extract_work_list_record(
            {
                "aweme_list": [
                    {
                        "aweme_id": "7626781229026708779",
                        "desc": "居家40分钟全身燃脂：无需器械，练就紧致线条",
                        "create_time": 1775748392,
                    }
                ]
            },
            "居家40分钟全身燃脂：无需器械，练就紧致线条",
        )

        self.assertEqual(record["platform_content_id"], "7626781229026708779")
        self.assertEqual(
            record["platform_url"],
            "https://www.douyin.com/video/7626781229026708779",
        )
        self.assertTrue(record["published_at"].startswith("2026-04-"))

    def test_extract_work_list_record_supports_nested_data_aweme_list(self):
        record = extract_work_list_record(
            {
                "data": {
                    "aweme_list": [
                        {
                            "aweme_id": "7626999999999999999",
                            "desc": "30分钟哑铃下肢塑形，打开就能跟练",
                            "create_time": 1775748392,
                        }
                    ]
                }
            },
            "30分钟哑铃下肢塑形",
        )

        self.assertEqual(record["platform_content_id"], "7626999999999999999")
        self.assertEqual(
            record["platform_url"],
            "https://www.douyin.com/video/7626999999999999999",
        )

    def test_publish_async_uses_automation_helpers_and_expected_locators(self):
        adapter = DouyinAutomationAdapter(
            config={},
            selectors={
                "publish_url": "https://creator.douyin.com",
                "locators": {
                    "title": ["input"],
                    "description": ["textarea"],
                    "video_upload": ["input[type='file']"],
                    "cover_upload": ["input[type='file'][accept*='image']"],
                    "publish_button": ["button"],
                },
            },
        )
        payload = normalize_douyin_publish_payload(self._make_job())
        playwright = AsyncMock()
        context = AsyncMock()
        page = AsyncMock()
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        adapter._fill_first = AsyncMock(return_value=None)
        adapter._set_file_first = AsyncMock(return_value=None)
        adapter._wait_for_any_selector = AsyncMock(return_value="input")
        adapter._apply_option_controls = AsyncMock(return_value=None)
        adapter._wait_for_text_to_clear = AsyncMock(return_value="页面已准备好")
        adapter._click_first = AsyncMock(return_value=None)
        adapter._verify_publish_result = AsyncMock(
            return_value={
                "verified": True,
                "platform_content_id": "douyin_123",
                "platform_url": "https://creator.douyin.com/creator-micro/content/manage",
                "published_at": "",
                "last_error": "",
            }
        )

        with patch("core.publishing.adapters.douyin.os.path.exists", return_value=True):
            result = adapter.run_async(adapter._publish_async(payload))

        self.assertEqual(result["platform_content_id"], "douyin_123")
        self.assertEqual(result["last_error"], "")
        adapter._open_page.assert_awaited_once_with("https://creator.douyin.com")
        self.assertEqual(
            adapter._set_file_first.await_args_list,
            [
                call(page, ["input[type='file']"], payload["video_path"]),
                call(
                    page,
                    ["input[type='file'][accept*='image']"],
                    payload["cover_path"],
                ),
            ],
        )
        adapter._wait_for_any_selector.assert_awaited_once_with(
            page,
            ["input", "textarea"],
            timeout_ms=600000,
            poll_ms=1000,
        )
        self.assertEqual(
            adapter._fill_first.await_args_list,
            [
                call(page, ["input"], payload["title"]),
                call(page, ["textarea"], payload["description"]),
            ],
        )
        adapter._apply_option_controls.assert_awaited_once_with(page, payload, {})
        adapter._wait_for_text_to_clear.assert_awaited_once_with(
            page,
            ["上传中", "处理中"],
            timeout_ms=600000,
            poll_ms=1000,
        )
        adapter._click_first.assert_awaited_once_with(page, ["button"])
        adapter._verify_publish_result.assert_awaited_once()

    def test_publish_async_applies_configured_option_controls(self):
        adapter = DouyinAutomationAdapter(
            config={},
            selectors={
                "publish_url": "https://creator.douyin.com",
                "locators": {
                    "title": ["input"],
                    "description": ["textarea"],
                    "video_upload": ["input[type='file']"],
                    "cover_upload": ["input[type='file'][accept*='image']"],
                    "publish_button": ["button"],
                },
                "option_controls": {
                    "visibility": {
                        "kind": "choice",
                        "options": {"friends": ["div:text-is('好友可见')"]},
                    }
                },
            },
        )
        payload = normalize_douyin_publish_payload(
            self._make_job(
                platform_payloads={"douyin": {"visibility": "friends"}}
            )
        )
        playwright = AsyncMock()
        context = AsyncMock()
        page = AsyncMock()
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        adapter._fill_first = AsyncMock(return_value=None)
        adapter._set_file_first = AsyncMock(return_value=None)
        adapter._wait_for_any_selector = AsyncMock(return_value="input")
        adapter._apply_option_controls = AsyncMock(return_value=None)
        adapter._wait_for_text_to_clear = AsyncMock(return_value="页面已准备好")
        adapter._click_first = AsyncMock(return_value=None)
        adapter._verify_publish_result = AsyncMock(
            return_value={
                "verified": True,
                "platform_content_id": "douyin_123",
                "platform_url": "",
                "published_at": "",
                "last_error": "",
            }
        )

        adapter.run_async(adapter._publish_async(payload))

        adapter._apply_option_controls.assert_awaited_once_with(
            page,
            payload,
            adapter.selectors["option_controls"],
        )

    def test_publish_async_stops_before_publish_when_video_is_still_processing(self):
        adapter = DouyinAutomationAdapter(
            config={},
            selectors={
                "publish_url": "https://creator.douyin.com",
                "locators": {
                    "title": ["input"],
                    "description": ["textarea"],
                    "video_upload": ["input[type='file']"],
                    "publish_button": ["button"],
                },
            },
        )
        payload = normalize_douyin_publish_payload(self._make_job())
        playwright = AsyncMock()
        context = AsyncMock()
        page = AsyncMock()
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        adapter._fill_first = AsyncMock(return_value=None)
        adapter._set_file_first = AsyncMock(return_value=None)
        adapter._wait_for_any_selector = AsyncMock(return_value="input")
        adapter._wait_for_text_to_clear = AsyncMock(return_value="上传中 处理中")
        adapter._click_first = AsyncMock(return_value=None)
        adapter._verify_publish_result = AsyncMock(return_value={})

        result = adapter.run_async(adapter._publish_async(payload))

        self.assertFalse(result["verified"])
        self.assertIn("still processing", result["last_error"])
        adapter._click_first.assert_not_awaited()
        adapter._verify_publish_result.assert_not_awaited()

    def test_publish_async_retries_publish_after_confirming_intercept_modal(self):
        adapter = DouyinAutomationAdapter(
            config={},
            selectors={
                "publish_url": "https://creator.douyin.com",
                "locators": {
                    "title": ["input"],
                    "description": ["textarea"],
                    "video_upload": ["input[type='file']"],
                    "publish_button": ["button:text-is('发布')"],
                    "publish_modal_confirm": ["button:text-is('继续发布')"],
                },
            },
        )
        payload = normalize_douyin_publish_payload(self._make_job())
        playwright = AsyncMock()
        context = AsyncMock()
        page = AsyncMock()
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        adapter._fill_first = AsyncMock(return_value=None)
        adapter._set_file_first = AsyncMock(return_value=None)
        adapter._wait_for_any_selector = AsyncMock(return_value="input")
        adapter._apply_option_controls = AsyncMock(return_value=None)
        adapter._wait_for_text_to_clear = AsyncMock(return_value="页面已准备好")
        adapter._click_first = AsyncMock(
            side_effect=[
                RuntimeError(
                    "Locator.click: Timeout 30000ms exceeded. "
                    "<div role='modal' class='semi-modal-wrap'> intercepts pointer events"
                ),
                None,
                None,
            ]
        )
        adapter._verify_publish_result = AsyncMock(
            return_value={
                "verified": True,
                "platform_content_id": "douyin_123",
                "platform_url": "",
                "published_at": "",
                "last_error": "",
            }
        )

        result = adapter.run_async(adapter._publish_async(payload))

        self.assertEqual(result["platform_content_id"], "douyin_123")
        self.assertEqual(
            adapter._click_first.await_args_list,
            [
                call(page, ["button:text-is('发布')"]),
                call(
                    page,
                    ["button:text-is('继续发布')"],
                    timeout_ms=5000,
                    poll_ms=250,
                ),
                call(page, ["button:text-is('发布')"]),
            ],
        )
        adapter._verify_publish_result.assert_awaited_once()

    def test_publish_runs_async_flow_and_maps_standard_result(self):
        adapter = DouyinAutomationAdapter(
            config={},
            selectors={
                "publish_url": "https://creator.douyin.com",
                "locators": {
                    "title": ["input"],
                    "description": ["textarea"],
                    "video_upload": ["input[type='file']"],
                    "publish_button": ["button"],
                },
            },
        )
        job = self._make_job()
        adapter._publish_async = AsyncMock(
            return_value={
                "platform_content_id": "douyin_123",
                "platform_url": "https://www.douyin.com/video/douyin_123",
                "published_at": "2026-04-09T12:00:00+08:00",
                "last_error": "",
            }
        )

        result = adapter.publish(job)

        self.assertEqual(result["platform"], "douyin")
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["platform_content_id"], "douyin_123")
        self.assertEqual(result["platform_url"], "https://www.douyin.com/video/douyin_123")
        self.assertEqual(result["scheduled_by"], "biyou")
        self.assertEqual(result["scheduled_for"], "")
        self.assertIn("#fitness", result["extra"]["description"])
        adapter._publish_async.assert_awaited_once()

    def test_publish_marks_result_retryable_when_verification_does_not_confirm_publish(self):
        adapter = DouyinAutomationAdapter(config={}, selectors={})
        job = self._make_job()
        adapter._publish_async = AsyncMock(
            return_value={
                "verified": False,
                "platform_content_id": "",
                "platform_url": "",
                "published_at": "",
                "last_error": "Douyin publish result could not be verified from creator center",
            }
        )

        result = adapter.publish(job)

        self.assertEqual(result["status"], "failed_retryable")
        self.assertIn("could not be verified", result["last_error"])

    def test_publish_rejects_video_longer_than_platform_limit(self):
        adapter = DouyinAutomationAdapter(config={}, selectors={})
        job = self._make_job(metadata={"duration": 3623})
        adapter._publish_async = AsyncMock(return_value={})

        result = adapter.publish(job)

        self.assertEqual(result["status"], "failed_manual")
        self.assertIn("60:00", result["last_error"])
        self.assertIn("3623s", result["last_error"])
        adapter._publish_async.assert_not_awaited()

    def test_publish_uses_sidecar_info_duration_when_metadata_duration_is_missing(self):
        adapter = DouyinAutomationAdapter(config={}, selectors={})
        adapter._publish_async = AsyncMock(return_value={})

        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, "video.mp4")
            info_path = os.path.join(tmpdir, "video.info.json")
            with open(video_path, "wb") as f:
                f.write(b"")
            with open(info_path, "w", encoding="utf-8") as f:
                json.dump({"duration": 3623}, f)

            job = self._make_job(video_path=video_path, metadata={})
            result = adapter.publish(job)

        self.assertEqual(result["status"], "failed_manual")
        self.assertIn("3623s", result["last_error"])
        adapter._publish_async.assert_not_awaited()

    def test_publish_prefers_douyin_override_video_path_for_duration_limit(self):
        adapter = DouyinAutomationAdapter(config={}, selectors={})
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
                metadata={},
                platform_payloads={"douyin": {"video_path": trim_video_path}},
            )
            result = adapter.publish(job)

        self.assertNotIn("60:00", result["last_error"])
        adapter._publish_async.assert_awaited_once()

    def test_publish_prefers_douyin_override_video_path_even_when_metadata_duration_is_too_long(self):
        adapter = DouyinAutomationAdapter(config={}, selectors={})
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
                platform_payloads={"douyin": {"video_path": trim_video_path}},
            )
            result = adapter.publish(job)

        self.assertNotIn("60:00", result["last_error"])
        adapter._publish_async.assert_awaited_once()

    def test_publish_requires_backend_content_id_for_success(self):
        adapter = DouyinAutomationAdapter(config={}, selectors={})
        job = self._make_job()
        adapter._publish_async = AsyncMock(
            return_value={
                "verified": True,
                "platform_content_id": "",
                "platform_url": "",
                "published_at": "",
                "last_error": "",
            }
        )

        result = adapter.publish(job)

        self.assertEqual(result["status"], "failed_retryable")
        self.assertIn("could not be verified", result["last_error"])

    def test_publish_accepts_creator_center_row_verification_without_content_id(self):
        adapter = DouyinAutomationAdapter(config={}, selectors={})
        job = self._make_job()
        adapter._publish_async = AsyncMock(
            return_value={
                "verified": True,
                "platform_content_id": "",
                "platform_url": "https://creator.douyin.com/creator-micro/content/manage",
                "published_at": "",
                "last_error": "",
                "verification_source": "creator_center_row",
            }
        )

        result = adapter.publish(job)

        self.assertEqual(result["status"], "published")
        self.assertEqual(
            result["platform_url"],
            "https://creator.douyin.com/creator-micro/content/manage",
        )
        self.assertEqual(result["last_error"], "")

    def test_publish_keeps_manual_required_status_for_sms_or_safety_block(self):
        adapter = DouyinAutomationAdapter(config={}, selectors={})
        job = self._make_job()
        adapter._publish_async = AsyncMock(
            return_value={
                "verified": False,
                "manual_required": True,
                "platform_content_id": "",
                "platform_url": "",
                "published_at": "",
                "last_error": "Douyin publish requires SMS verification in creator center",
            }
        )

        result = adapter.publish(job)

        self.assertEqual(result["status"], "failed_manual")
        self.assertIn("SMS verification", result["last_error"])

    def test_schedule_returns_biyou_managed_result_with_normalized_payload(self):
        adapter = DouyinAutomationAdapter(config={}, selectors={})
        job = self._make_job()

        result = adapter.schedule(job, "2026-04-09T12:00:00+08:00")

        self.assertEqual(result["platform"], "douyin")
        self.assertEqual(result["status"], "scheduled")
        self.assertEqual(result["scheduled_by"], "biyou")
        self.assertEqual(result["scheduled_for"], "2026-04-09T12:00:00+08:00")
        self.assertIn("#fitness", result["extra"]["description"])

    def test_verify_publish_result_requires_backend_record_not_title_only_match(self):
        adapter = DouyinAutomationAdapter(config={}, selectors={})
        payload = normalize_douyin_publish_payload(self._make_job())
        context = AsyncMock()
        verify_page = AsyncMock()
        context.new_page = AsyncMock(return_value=verify_page)
        adapter._extract_body_text = AsyncMock(return_value="Morning Workout 已发布")
        adapter._fetch_work_list_record = AsyncMock(return_value={})
        adapter._poll_work_list_record = AsyncMock(return_value={})
        adapter._wait_for_text = AsyncMock(return_value="Morning Workout 已发布")
        adapter._wait_for_backend_record = AsyncMock(return_value={})

        result = adapter.run_async(adapter._verify_publish_result(context, payload))

        self.assertFalse(result["verified"])
        self.assertEqual(result["platform_content_id"], "")
        self.assertIn("could not be verified", result["last_error"])
        adapter._wait_for_backend_record.assert_awaited_once_with(
            verify_page,
            payload,
            timeout_ms=DOUYIN_VERIFY_TIMEOUT_MS,
            poll_ms=5000,
        )

    def test_verify_publish_result_returns_platform_identity_from_backend_record(self):
        adapter = DouyinAutomationAdapter(config={}, selectors={})
        payload = normalize_douyin_publish_payload(self._make_job())
        context = AsyncMock()
        verify_page = AsyncMock()
        context.new_page = AsyncMock(return_value=verify_page)
        adapter._extract_body_text = AsyncMock(return_value="Morning Workout 已发布")
        adapter._fetch_work_list_record = AsyncMock(
            return_value={
                "platform_content_id": "720001",
                "platform_url": "https://www.douyin.com/video/720001",
                "published_at": "2026-04-09T12:30:00+08:00",
            }
        )
        adapter._wait_for_text = AsyncMock(return_value="Morning Workout 已发布")
        adapter._wait_for_backend_record = AsyncMock(return_value={})

        result = adapter.run_async(adapter._verify_publish_result(context, payload))

        self.assertTrue(result["verified"])
        self.assertEqual(result["platform_content_id"], "720001")
        self.assertEqual(result["platform_url"], "https://www.douyin.com/video/720001")
        self.assertEqual(result["published_at"], "2026-04-09T12:30:00+08:00")
        self.assertEqual(result["last_error"], "")

    def test_verify_publish_result_accepts_creator_center_row_without_content_id(self):
        adapter = DouyinAutomationAdapter(config={}, selectors={})
        payload = normalize_douyin_publish_payload(self._make_job())
        context = AsyncMock()
        verify_page = AsyncMock()
        verify_page.goto = AsyncMock(return_value=None)
        verify_page.close = AsyncMock(return_value=None)
        context.new_page = AsyncMock(return_value=verify_page)
        adapter._extract_body_text = AsyncMock(return_value="Morning Workout 审核中")
        adapter._fetch_work_list_record = AsyncMock(return_value={})
        adapter._wait_for_text = AsyncMock(return_value="Morning Workout 审核中")
        adapter._wait_for_backend_record = AsyncMock(
            return_value={
                "platform_content_id": "",
                "platform_url": "",
                "published_at": "2026-04-09T13:00:00+08:00",
                "matched_term": "Morning Workout",
                "row_text": "59:00 Morning Workout 审核中",
            }
        )

        result = adapter.run_async(adapter._verify_publish_result(context, payload))

        self.assertTrue(result["verified"])
        self.assertEqual(result["platform_content_id"], "")
        self.assertEqual(
            result["platform_url"],
            "https://creator.douyin.com/creator-micro/content/manage",
        )
        self.assertEqual(result["verification_source"], "creator_center_row")
        self.assertEqual(result["last_error"], "")

    def test_verify_publish_result_retries_work_list_until_backend_record_appears(self):
        adapter = DouyinAutomationAdapter(config={}, selectors={})
        payload = normalize_douyin_publish_payload(self._make_job())
        context = AsyncMock()
        verify_page = AsyncMock()
        context.new_page = AsyncMock(return_value=verify_page)
        adapter._extract_body_text = AsyncMock(return_value="Morning Workout 已发布")
        adapter._fetch_work_list_record = AsyncMock(
            side_effect=[
                {},
                {
                    "platform_content_id": "720099",
                    "platform_url": "https://www.douyin.com/video/720099",
                    "published_at": "2026-04-09T13:00:00+08:00",
                },
            ]
        )
        adapter._wait_for_text = AsyncMock(return_value="Morning Workout 已发布")
        adapter._wait_for_backend_record = AsyncMock(return_value={})

        with patch("core.publishing.adapters.douyin.asyncio.sleep", new=AsyncMock()):
            result = adapter.run_async(adapter._verify_publish_result(context, payload))

        self.assertTrue(result["verified"])
        self.assertEqual(result["platform_content_id"], "720099")
        self.assertEqual(adapter._fetch_work_list_record.await_count, 2)
        adapter._wait_for_backend_record.assert_not_awaited()

    def test_verify_publish_result_marks_sms_verification_as_manual_required(self):
        adapter = DouyinAutomationAdapter(config={}, selectors={})
        payload = normalize_douyin_publish_payload(self._make_job())
        context = AsyncMock()
        verify_page = AsyncMock()
        context.new_page = AsyncMock(return_value=verify_page)
        adapter._extract_body_text = AsyncMock(return_value="请输入短信验证码继续发布")
        adapter._fetch_work_list_record = AsyncMock(return_value={})
        adapter._wait_for_text = AsyncMock(return_value="")

        result = adapter.run_async(adapter._verify_publish_result(context, payload))

        self.assertFalse(result["verified"])
        self.assertTrue(result["manual_required"])
        self.assertIn("SMS verification", result["last_error"])

    def test_verify_publish_result_marks_manual_safety_gate_as_manual_required(self):
        adapter = DouyinAutomationAdapter(config={}, selectors={})
        payload = normalize_douyin_publish_payload(self._make_job())
        context = AsyncMock()
        verify_page = AsyncMock()
        context.new_page = AsyncMock(return_value=verify_page)
        adapter._extract_body_text = AsyncMock(return_value="请完成安全验证后继续")
        adapter._fetch_work_list_record = AsyncMock(return_value={})
        adapter._wait_for_text = AsyncMock(return_value="")

        result = adapter.run_async(adapter._verify_publish_result(context, payload))

        self.assertFalse(result["verified"])
        self.assertTrue(result["manual_required"])
        self.assertIn("manual confirmation", result["last_error"])


if __name__ == "__main__":
    unittest.main()

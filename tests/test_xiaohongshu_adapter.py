import unittest
from unittest.mock import AsyncMock

from core.publishing.adapters.xiaohongshu import (
    XiaohongshuAdapter,
    _extract_xiaohongshu_manager_record,
    extract_xiaohongshu_publish_response_outcome,
    is_xiaohongshu_cover_modal_open,
    is_xiaohongshu_cover_processing,
    is_xiaohongshu_upload_blocking,
    normalize_xiaohongshu_publish_payload,
)
from core.publishing.models import PublishJob


class _FakeVerificationPage:
    def __init__(self, *, url="", body_text="", links=None):
        self.url = url
        self._body_text = body_text
        self._links = list(links or [])
        self.goto = AsyncMock()
        self.close = AsyncMock()
        self.wait_for_timeout = AsyncMock()

    async def evaluate(self, script, *args):
        if "document.body?.innerText" in script:
            return self._body_text
        if "querySelectorAll('a[href]')" in script:
            return list(self._links)
        raise AssertionError(f"Unexpected evaluate script: {script}")


class TestXiaohongshuAdapter(unittest.TestCase):
    def _make_job(self, **overrides):
        data = {
            "platform_targets": ["xiaohongshu"],
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

    def test_normalize_publish_payload_moves_tags_into_body_text(self):
        job = self._make_job()

        payload = normalize_xiaohongshu_publish_payload(job)

        self.assertEqual(payload["title"], "Morning Workout")
        self.assertEqual(payload["video_path"], "/tmp/video.mp4")
        self.assertEqual(payload["cover_path"], "/tmp/cover.jpg")
        self.assertIn("#fitness", payload["body"])
        self.assertIn("#strength", payload["body"])
        self.assertNotIn("#extra", payload["body"])

    def test_normalize_publish_payload_truncates_title_to_xiaohongshu_limit(self):
        job = self._make_job(title="居家40分钟全身燃脂：无需器械，练就紧致线条")

        payload = normalize_xiaohongshu_publish_payload(job)

        self.assertEqual(payload["title"], "居家40分钟全身燃脂：无需器械，练就紧致")
        self.assertEqual(len(payload["title"]), 20)

    def test_normalize_publish_payload_prefers_platform_overrides(self):
        job = self._make_job(
            platform_payloads={
                "xiaohongshu": {
                    "title": "小红书标题更生活化一些",
                    "description": "小红书简介",
                    "tags": ["居家", "燃脂"],
                    "video_path": "/tmp/xhs.mp4",
                    "cover_path": "/tmp/xhs.jpg",
                }
            }
        )

        payload = normalize_xiaohongshu_publish_payload(job)

        self.assertEqual(payload["title"], "小红书标题更生活化一些")
        self.assertEqual(payload["video_path"], "/tmp/xhs.mp4")
        self.assertEqual(payload["cover_path"], "/tmp/xhs.jpg")
        self.assertIn("#居家", payload["body"])
        self.assertIn("小红书简介", payload["body"])

    def test_normalize_publish_payload_falls_back_when_override_title_is_too_short(self):
        job = self._make_job(
            title="小红书标题至少六个字",
            platform_payloads={
                "xiaohongshu": {
                    "title": "【UE5",
                    "description": "小红书简介",
                }
            },
        )

        payload = normalize_xiaohongshu_publish_payload(job)

        self.assertEqual(payload["title"], "小红书标题至少六个字")

    def test_normalize_publish_payload_preserves_extra_platform_fields(self):
        job = self._make_job(
            platform_payloads={
                "xiaohongshu": {
                    "visibility": "followers",
                    "poi_name": "上海徐汇",
                    "allow_comment": False,
                }
            }
        )

        payload = normalize_xiaohongshu_publish_payload(job)

        self.assertEqual(payload["visibility"], "followers")
        self.assertEqual(payload["poi_name"], "上海徐汇")
        self.assertFalse(payload["allow_comment"])

    def test_upload_blocking_ignores_transcode_notice(self):
        self.assertFalse(is_xiaohongshu_upload_blocking("转码中，不影响发布"))
        self.assertFalse(is_xiaohongshu_upload_blocking("转码中，不影响发布\n当前速度：1.17MB/s\n剩余时间：2min"))
        self.assertTrue(is_xiaohongshu_upload_blocking("上传中 88%\n剩余时间：4s"))

    def test_cover_helpers_detect_processing_and_modal_state(self):
        self.assertTrue(is_xiaohongshu_cover_processing("封面上传中\n视频加载中"))
        self.assertFalse(is_xiaohongshu_cover_processing("封面已完成"))
        self.assertTrue(is_xiaohongshu_cover_modal_open("设置封面\n上传图片\n取消\n确定"))
        self.assertFalse(is_xiaohongshu_cover_modal_open("封面已应用成功"))

    def test_extract_xiaohongshu_manager_record_supports_nested_response_link(self):
        record = _extract_xiaohongshu_manager_record(
            {
                "data": {
                    "items": [
                        {
                            "noteId": "xhs_123",
                            "title": "Morning Workout",
                            "shareLink": "https://www.xiaohongshu.com/explore/xhs_123?xsec_token=abc",
                            "status": "已发布",
                        }
                    ]
                }
            },
            "Morning Workout",
        )

        self.assertEqual(record["platform_content_id"], "xhs_123")
        self.assertEqual(
            record["platform_url"],
            "https://www.xiaohongshu.com/explore/xhs_123?xsec_token=abc",
        )
        self.assertTrue(record["manager_success"])

    def test_extract_xiaohongshu_publish_response_outcome_marks_policy_rejection_manual(self):
        outcome = extract_xiaohongshu_publish_response_outcome(
            {
                "success": False,
                "result": -9136,
                "need_retry": False,
                "can_show_err_msg_to_user": True,
                "msg": "因违反社区规范禁止发笔记",
            }
        )

        self.assertEqual(outcome["status"], "failed_manual")
        self.assertTrue(outcome["manual_required"])
        self.assertIn("因违反社区规范禁止发笔记", outcome["last_error"])
        self.assertIn("code=-9136", outcome["last_error"])

    def test_extract_xiaohongshu_publish_response_outcome_keeps_retryable_errors_retryable(self):
        outcome = extract_xiaohongshu_publish_response_outcome(
            {
                "success": False,
                "result": -1001,
                "need_retry": True,
                "msg": "网络繁忙，请稍后重试",
            }
        )

        self.assertEqual(outcome["status"], "failed_retryable")
        self.assertNotIn("manual_required", outcome)
        self.assertIn("网络繁忙", outcome["last_error"])

    def test_capabilities_and_validate_requirements_match_beta_automation(self):
        adapter = XiaohongshuAdapter(config={}, selectors={})

        self.assertEqual(adapter.stability_tier, "beta")
        self.assertEqual(adapter.execution_mode, "automation")
        self.assertFalse(adapter.capabilities.supports_native_schedule)
        self.assertTrue(adapter.capabilities.supports_cover)
        self.assertFalse(adapter.capabilities.supports_tags)
        self.assertTrue(adapter.capabilities.supports_description)

        adapter.validate(self._make_job())

        with self.assertRaisesRegex(ValueError, "video_path and title"):
            adapter.validate(self._make_job(video_path=""))

        with self.assertRaisesRegex(ValueError, "video_path and title"):
            adapter.validate(self._make_job(title=""))

    def test_publish_runs_async_flow_and_maps_standard_result(self):
        adapter = XiaohongshuAdapter(
            config={},
            selectors={
                "publish_url": "https://creator.xiaohongshu.com",
                "locators": {
                    "title": ["input"],
                    "description": ["textarea"],
                    "video_upload": ["input[type='file']"],
                    "cover_upload": ["input[type='file']"],
                    "publish_button": ["button"],
                },
            },
        )
        job = self._make_job()
        adapter._publish_async = AsyncMock(
            return_value={
                "platform_content_id": "xhs_123",
                "platform_url": "https://www.xiaohongshu.com/explore/xhs_123",
                "published_at": "2026-04-08T10:00:00+08:00",
                "last_error": "",
            }
        )

        result = adapter.publish(job)

        self.assertEqual(result["platform"], "xiaohongshu")
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["platform_content_id"], "xhs_123")
        self.assertEqual(result["platform_url"], "https://www.xiaohongshu.com/explore/xhs_123")
        self.assertEqual(result["scheduled_by"], "biyou")
        self.assertEqual(result["scheduled_for"], "")
        self.assertEqual(result["extra"]["title"], "Morning Workout")
        self.assertIn("#fitness", result["extra"]["body"])
        adapter._publish_async.assert_awaited_once()

    def test_schedule_returns_biyou_managed_result_with_normalized_payload(self):
        adapter = XiaohongshuAdapter(config={}, selectors={})
        job = self._make_job()

        result = adapter.schedule(job, "2026-04-08T10:00:00+08:00")

        self.assertEqual(result["platform"], "xiaohongshu")
        self.assertEqual(result["status"], "scheduled")
        self.assertEqual(result["scheduled_by"], "biyou")
        self.assertEqual(result["scheduled_for"], "2026-04-08T10:00:00+08:00")
        self.assertEqual(result["extra"]["cover_path"], "/tmp/cover.jpg")
        self.assertIn("#fitness", result["extra"]["body"])

    def test_publish_async_supports_video_only_upload_flow(self):
        adapter = XiaohongshuAdapter(
            config={},
            selectors={
                "publish_url": "https://creator.xiaohongshu.com",
                "locators": {
                    "title": ["input[placeholder*='填写标题']"],
                    "description": ["[contenteditable='true']"],
                    "video_upload": ["input[type='file']"],
                    "cover_upload": [],
                    "publish_button": ["button:text-is('发布')"],
                },
            },
        )
        payload = normalize_xiaohongshu_publish_payload(self._make_job())
        playwright = AsyncMock()
        context = AsyncMock()
        page = AsyncMock()
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        adapter._set_file_first = AsyncMock(return_value=None)
        adapter._wait_for_any_selector = AsyncMock(return_value="input[placeholder*='填写标题']")
        adapter._wait_for_upload_ready = AsyncMock(return_value="发布页面已准备好")
        adapter._fill_first = AsyncMock(return_value=None)
        adapter._apply_option_controls = AsyncMock(return_value=None)
        adapter._capture_publish_response_outcome = AsyncMock(return_value=None)
        adapter._click_first_force = AsyncMock(return_value=None)
        adapter._verify_publish_result = AsyncMock(
            return_value={
                "verified": True,
                "platform_content_id": "xhs_123",
                "platform_url": "https://creator.xiaohongshu.com/new/note-manager",
                "published_at": "",
                "last_error": "",
            }
        )

        result = adapter.run_async(adapter._publish_async(payload))

        self.assertEqual(result["platform_content_id"], "xhs_123")
        self.assertEqual(result["last_error"], "")
        adapter._set_file_first.assert_awaited_once_with(
            page,
            ["input[type='file']"],
            payload["video_path"],
        )
        self.assertEqual(
            adapter._fill_first.await_args_list[0].args,
            (page, ["input[placeholder*='填写标题']"], payload["title"]),
        )
        self.assertEqual(
            adapter._fill_first.await_args_list[1].args,
            (page, ["[contenteditable='true']"], payload["body"]),
        )
        adapter._wait_for_upload_ready.assert_awaited_once_with(page)
        adapter._apply_option_controls.assert_awaited_once_with(page, payload, {})
        adapter._capture_publish_response_outcome.assert_awaited_once_with(page)
        adapter._verify_publish_result.assert_awaited_once()

    def test_publish_async_falls_back_to_recommended_cover_when_image_upload_is_unavailable(self):
        adapter = XiaohongshuAdapter(
            config={},
            selectors={
                "publish_url": "https://creator.xiaohongshu.com",
                "locators": {
                    "title": ["input[placeholder*='填写标题']"],
                    "description": ["[contenteditable='true']"],
                    "video_upload": ["input.upload-input"],
                    "cover_upload": ["input[type='file'][accept*='image']"],
                    "recommended_cover": [
                        ".recommend .defaults > .default:nth-child(2)",
                        ".recommend .defaults > .default:nth-child(3)",
                    ],
                    "recommended_cover_applied_text": ["已应用推荐封面"],
                    "publish_button": ["button:text-is('发布')"],
                },
            },
        )
        payload = normalize_xiaohongshu_publish_payload(self._make_job())
        playwright = AsyncMock()
        context = AsyncMock()
        page = AsyncMock()
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        adapter._set_file_first = AsyncMock(
            side_effect=[None, RuntimeError("Unable to locate file target from selectors")]
        )
        adapter._wait_for_any_selector = AsyncMock(return_value="input[placeholder*='填写标题']")
        adapter._wait_for_text = AsyncMock(return_value="已应用推荐封面")
        adapter._wait_for_text_to_clear = AsyncMock(return_value="封面编辑器已关闭")
        adapter._wait_for_upload_ready = AsyncMock(return_value="发布页面已准备好")
        adapter._fill_first = AsyncMock(return_value=None)
        adapter._apply_option_controls = AsyncMock(return_value=None)
        adapter._capture_publish_response_outcome = AsyncMock(return_value=None)
        adapter._click_first_force = AsyncMock(return_value=None)
        adapter._js_click_text = AsyncMock(return_value=True)
        adapter._verify_publish_result = AsyncMock(
            return_value={
                "verified": True,
                "platform_content_id": "xhs_123",
                "platform_url": "https://creator.xiaohongshu.com/new/note-manager",
                "published_at": "",
                "last_error": "",
            }
        )

        result = adapter.run_async(adapter._publish_async(payload))

        self.assertEqual(result["platform_content_id"], "xhs_123")
        self.assertEqual(adapter._set_file_first.await_count, 2)
        self.assertEqual(result["cover_selection_state"], "selected")
        self.assertEqual(result["cover_confirmation_state"], "confirmed")
        self.assertEqual(result["cover_apply_method"], "recommended_cover")
        click_calls = [call_item.args for call_item in adapter._click_first_force.await_args_list]
        self.assertIn(
            (
                page,
                [
                    ".recommend .defaults > .default:nth-child(2)",
                    ".recommend .defaults > .default:nth-child(3)",
                ],
            ),
            click_calls,
        )
        adapter._capture_publish_response_outcome.assert_awaited_once_with(page)
        adapter._wait_for_text.assert_awaited_once_with(
            page,
            ["已应用推荐封面"],
            timeout_ms=5000,
            poll_ms=250,
        )
        adapter._verify_publish_result.assert_awaited_once()

    def test_apply_cover_falls_back_to_recommended_when_uploaded_cover_modal_stays_open(self):
        adapter = XiaohongshuAdapter(config={}, selectors={})
        page = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        payload = {
            "cover_path": "/tmp/cover.jpg",
        }
        locators = {
            "cover_activate": ["button:has-text('自定义封面')"],
            "cover_upload": ["input[type='file'][accept*='image']"],
            "cover_confirm": ["button:has-text('确定')"],
            "recommended_cover": [".recommend .defaults > .default:nth-child(2)"],
            "recommended_cover_applied_text": ["已应用推荐封面"],
        }
        adapter._click_first = AsyncMock(return_value=None)
        adapter._set_file_first = AsyncMock(return_value=None)
        adapter._wait_for_text_to_clear = AsyncMock(
            side_effect=[
                "封面已准备好",
                "设置封面\n上传图片\n获取封面建议\n取消\n确定",
                "封面编辑器已关闭",
            ]
        )
        adapter._js_click_text = AsyncMock(side_effect=[True, True])
        adapter._click_first_force = AsyncMock(return_value=None)
        adapter._wait_for_text = AsyncMock(return_value="已应用推荐封面")

        result = adapter.run_async(adapter._apply_cover(page, payload, locators))

        self.assertEqual(result["cover_selection_state"], "selected")
        self.assertEqual(result["cover_confirmation_state"], "confirmed")
        self.assertEqual(result["cover_apply_method"], "recommended_cover")
        click_calls = [call_item.args for call_item in adapter._click_first_force.await_args_list]
        self.assertIn(
            (page, [".recommend .defaults > .default:nth-child(2)"]),
            click_calls,
        )

    def test_publish_async_applies_configured_option_controls(self):
        adapter = XiaohongshuAdapter(
            config={},
            selectors={
                "publish_url": "https://creator.xiaohongshu.com",
                "locators": {
                    "title": ["input[placeholder*='填写标题']"],
                    "description": ["[contenteditable='true']"],
                    "video_upload": ["input[type='file']"],
                    "cover_upload": [],
                    "publish_button": ["button:text-is('发布')"],
                },
                "option_controls": {
                    "allow_comment": {
                        "kind": "boolean",
                        "selectors": ["[role='switch'][aria-label*='评论']"],
                        "enabled_selectors": ["[aria-checked='true']"],
                        "disabled_selectors": ["[aria-checked='false']"],
                    }
                },
            },
        )
        payload = normalize_xiaohongshu_publish_payload(
            self._make_job(
                platform_payloads={"xiaohongshu": {"allow_comment": False}}
            )
        )
        playwright = AsyncMock()
        context = AsyncMock()
        page = AsyncMock()
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        adapter._set_file_first = AsyncMock(return_value=None)
        adapter._wait_for_any_selector = AsyncMock(return_value="input[placeholder*='填写标题']")
        adapter._wait_for_upload_ready = AsyncMock(return_value="发布页面已准备好")
        adapter._fill_first = AsyncMock(return_value=None)
        adapter._apply_option_controls = AsyncMock(return_value=None)
        adapter._capture_publish_response_outcome = AsyncMock(return_value=None)
        adapter._click_first_force = AsyncMock(return_value=None)
        adapter._verify_publish_result = AsyncMock(
            return_value={
                "verified": True,
                "platform_content_id": "xhs_123",
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
        adapter = XiaohongshuAdapter(
            config={},
            selectors={
                "publish_url": "https://creator.xiaohongshu.com",
                "locators": {
                    "title": ["input[placeholder*='填写标题']"],
                    "description": ["[contenteditable='true']"],
                    "video_upload": ["input[type='file']"],
                    "cover_upload": [],
                    "publish_button": ["button:text-is('发布')"],
                },
            },
        )
        payload = normalize_xiaohongshu_publish_payload(self._make_job())
        playwright = AsyncMock()
        context = AsyncMock()
        page = AsyncMock()
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        adapter._set_file_first = AsyncMock(return_value=None)
        adapter._wait_for_any_selector = AsyncMock(return_value="input[placeholder*='填写标题']")
        adapter._wait_for_upload_ready = AsyncMock(return_value="上传中 88%\n剩余时间：4s")
        adapter._fill_first = AsyncMock(return_value=None)
        adapter._capture_publish_response_outcome = AsyncMock(return_value=None)
        adapter._click_first_force = AsyncMock(return_value=None)
        adapter._verify_publish_result = AsyncMock(return_value={})

        result = adapter.run_async(adapter._publish_async(payload))

        self.assertFalse(result["verified"])
        self.assertIn("still processing", result["last_error"])
        adapter._click_first_force.assert_not_awaited()
        adapter._verify_publish_result.assert_not_awaited()

    def test_verify_publish_result_extracts_note_link_from_manager_page(self):
        adapter = XiaohongshuAdapter(config={}, selectors={})
        payload = normalize_xiaohongshu_publish_payload(self._make_job())
        verify_page = _FakeVerificationPage(
            url="https://creator.xiaohongshu.com/new/note-manager",
            body_text="Morning Workout 已发布",
            links=[
                {
                    "href": "https://www.xiaohongshu.com/explore/xhs_123?xsec_token=abc",
                    "text": "Morning Workout\nA short session",
                }
            ],
        )
        context = AsyncMock()
        context.new_page = AsyncMock(return_value=verify_page)

        result = adapter.run_async(adapter._verify_publish_result(context, payload))

        self.assertTrue(result["verified"])
        self.assertEqual(result["platform_content_id"], "xhs_123")
        self.assertEqual(
            result["platform_url"],
            "https://www.xiaohongshu.com/explore/xhs_123?xsec_token=abc",
        )
        self.assertEqual(result["verification_source"], "note_manager_link")

    def test_verify_publish_result_prefers_response_record_before_dom_fallback(self):
        adapter = XiaohongshuAdapter(config={}, selectors={})
        payload = normalize_xiaohongshu_publish_payload(self._make_job())
        verify_page = _FakeVerificationPage(
            url="https://creator.xiaohongshu.com/new/note-manager",
            body_text="Morning Workout 已发布",
            links=[],
        )
        context = AsyncMock()
        context.new_page = AsyncMock(return_value=verify_page)
        adapter._fetch_note_manager_record = AsyncMock(
            return_value={
                "platform_content_id": "xhs_999",
                "platform_url": "https://www.xiaohongshu.com/explore/xhs_999",
                "manager_success": True,
            }
        )
        adapter._extract_body_text = AsyncMock(return_value="should not be used")

        result = adapter.run_async(adapter._verify_publish_result(context, payload))

        self.assertTrue(result["verified"])
        self.assertEqual(result["platform_content_id"], "xhs_999")
        self.assertEqual(result["verification_source"], "note_manager_response")
        adapter._extract_body_text.assert_not_awaited()

    def test_verify_publish_result_keeps_unknown_when_only_title_text_matches(self):
        adapter = XiaohongshuAdapter(config={}, selectors={})
        payload = normalize_xiaohongshu_publish_payload(self._make_job())
        verify_page = _FakeVerificationPage(
            url="https://creator.xiaohongshu.com/new/note-manager",
            body_text="Morning Workout",
            links=[],
        )
        context = AsyncMock()
        context.new_page = AsyncMock(return_value=verify_page)

        result = adapter.run_async(adapter._verify_publish_result(context, payload))

        self.assertFalse(result["verified"])
        self.assertEqual(result["verification_state"], "unknown")
        self.assertEqual(result["status"], "failed_retryable")
        self.assertIn("could not be verified", result["last_error"])

    def test_verify_publish_result_accepts_reviewing_row_without_note_link(self):
        adapter = XiaohongshuAdapter(config={}, selectors={})
        payload = normalize_xiaohongshu_publish_payload(
            self._make_job(title="居家40分钟全身燃脂：无需器械，练就紧致")
        )
        verify_page = _FakeVerificationPage(
            url="https://creator.xiaohongshu.com/new/note-manager",
            body_text="全部笔记(4)\n审核中\n居家40分钟全身燃脂：无需器械，练就紧致\n发布于 2026年04月09日 23:24",
            links=[],
        )
        context = AsyncMock()
        context.new_page = AsyncMock(return_value=verify_page)

        result = adapter.run_async(adapter._verify_publish_result(context, payload))

        self.assertTrue(result["verified"])
        self.assertEqual(result["platform_url"], "https://creator.xiaohongshu.com/new/note-manager")
        self.assertEqual(result["verification_source"], "note_manager_row")

    def test_verify_publish_result_accepts_reviewing_row_after_wait_without_note_link(self):
        adapter = XiaohongshuAdapter(config={}, selectors={})
        payload = normalize_xiaohongshu_publish_payload(
            self._make_job(title="居家40分钟全身燃脂：无需器械，练就紧致")
        )
        verify_page = _FakeVerificationPage(
            url="https://creator.xiaohongshu.com/new/note-manager",
            body_text="全部笔记(4)",
            links=[],
        )
        context = AsyncMock()
        context.new_page = AsyncMock(return_value=verify_page)
        adapter._extract_body_text = AsyncMock(
            side_effect=[
                "全部笔记(4)",
                "全部笔记(4)\n审核中\n居家40分钟全身燃脂：无需器械，练就紧致\n发布于 2026年04月09日 23:24",
            ]
        )
        adapter._fetch_note_manager_record = AsyncMock(return_value={})

        result = adapter.run_async(adapter._verify_publish_result(context, payload))

        self.assertTrue(result["verified"])
        self.assertEqual(result["platform_content_id"], "")
        self.assertEqual(result["platform_url"], "https://creator.xiaohongshu.com/new/note-manager")
        self.assertEqual(result["verification_source"], "note_manager_row")
        self.assertEqual(verify_page.wait_for_timeout.await_count, 1)
        self.assertEqual(adapter._fetch_note_manager_record.await_count, 2)

    def test_publish_async_uses_redirected_note_page_as_verified_success(self):
        adapter = XiaohongshuAdapter(
            config={},
            selectors={
                "publish_url": "https://creator.xiaohongshu.com",
                "locators": {
                    "title": ["input[placeholder*='填写标题']"],
                    "description": ["[contenteditable='true']"],
                    "video_upload": ["input[type='file']"],
                    "cover_upload": [],
                    "publish_button": ["button:text-is('发布')"],
                },
            },
        )
        payload = normalize_xiaohongshu_publish_payload(self._make_job())
        playwright = AsyncMock()
        context = AsyncMock()
        page = _FakeVerificationPage(
            url="https://www.xiaohongshu.com/explore/xhs_123",
            body_text="Morning Workout 发布成功",
        )
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        adapter._set_file_first = AsyncMock(return_value=None)
        adapter._wait_for_any_selector = AsyncMock(return_value="input[placeholder*='填写标题']")
        adapter._wait_for_upload_ready = AsyncMock(return_value="转码中，不影响发布")
        adapter._fill_first = AsyncMock(return_value=None)
        adapter._capture_publish_response_outcome = AsyncMock(return_value=None)
        adapter._click_first_force = AsyncMock(return_value=None)
        adapter._verify_publish_result = AsyncMock(
            return_value={
                "verified": False,
                "status": "failed_retryable",
                "verification_state": "unknown",
                "platform_content_id": "",
                "platform_url": "",
                "published_at": "",
                "last_error": "should not reach note-manager fallback",
            }
        )

        result = adapter.run_async(adapter._publish_async(payload))

        self.assertTrue(result["verified"])
        self.assertEqual(result["platform_content_id"], "xhs_123")
        self.assertEqual(result["platform_url"], "https://www.xiaohongshu.com/explore/xhs_123")
        self.assertEqual(result["verification_source"], "publish_page_url")
        adapter._verify_publish_result.assert_not_awaited()

    def test_publish_async_retries_publish_click_once_when_page_stays_unchanged(self):
        adapter = XiaohongshuAdapter(
            config={},
            selectors={
                "publish_url": "https://creator.xiaohongshu.com/publish/publish",
                "locators": {
                    "title": ["input[placeholder*='填写标题']"],
                    "description": ["[contenteditable='true']"],
                    "video_upload": ["input[type='file']"],
                    "cover_upload": [],
                    "publish_button": ["button:text-is('发布')"],
                },
            },
        )
        payload = normalize_xiaohongshu_publish_payload(self._make_job())
        playwright = AsyncMock()
        context = AsyncMock()
        page = AsyncMock()
        page.url = "https://creator.xiaohongshu.com/publish/publish"
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        adapter._set_file_first = AsyncMock(return_value=None)
        adapter._wait_for_any_selector = AsyncMock(return_value="input[placeholder*='填写标题']")
        adapter._apply_cover = AsyncMock(return_value="recommended")
        adapter._wait_for_upload_ready = AsyncMock(return_value="发布页面已准备好")
        adapter._fill_first = AsyncMock(return_value=None)
        adapter._apply_option_controls = AsyncMock(return_value=None)
        adapter._capture_publish_response_outcome = AsyncMock(return_value=None)
        adapter._click_first_force = AsyncMock(return_value=None)
        adapter._inspect_post_publish_page = AsyncMock(
            side_effect=[
                None,
                {
                    "verified": True,
                    "status": "published",
                    "verification_state": "verified",
                    "platform_content_id": "xhs_123",
                    "platform_url": "https://www.xiaohongshu.com/explore/xhs_123",
                    "published_at": "",
                    "last_error": "",
                    "verification_source": "publish_page_url",
                },
            ]
        )
        adapter._verify_publish_result = AsyncMock(return_value={})

        result = adapter.run_async(adapter._publish_async(payload))

        self.assertTrue(result["verified"])
        self.assertEqual(result["platform_content_id"], "xhs_123")
        adapter._capture_publish_response_outcome.assert_awaited_once_with(page)
        self.assertEqual(adapter._click_first_force.await_count, 1)
        self.assertEqual(
            adapter._click_first_force.await_args_list[0].args,
            (page, ["button:text-is('发布')"]),
        )
        adapter._verify_publish_result.assert_not_awaited()

    def test_publish_async_returns_publish_response_failure_without_note_manager_fallback(self):
        adapter = XiaohongshuAdapter(
            config={},
            selectors={
                "publish_url": "https://creator.xiaohongshu.com/publish/publish",
                "locators": {
                    "title": ["input[placeholder*='填写标题']"],
                    "description": ["[contenteditable='true']"],
                    "video_upload": ["input[type='file']"],
                    "cover_upload": [],
                    "publish_button": ["button:text-is('发布')"],
                },
            },
        )
        payload = normalize_xiaohongshu_publish_payload(self._make_job())
        playwright = AsyncMock()
        context = AsyncMock()
        page = AsyncMock()
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        adapter._set_file_first = AsyncMock(return_value=None)
        adapter._wait_for_any_selector = AsyncMock(return_value="input[placeholder*='填写标题']")
        adapter._apply_cover = AsyncMock(return_value={"cover_selection_state": "selected"})
        adapter._wait_for_upload_ready = AsyncMock(return_value="发布页面已准备好")
        adapter._fill_first = AsyncMock(return_value=None)
        adapter._apply_option_controls = AsyncMock(return_value=None)
        adapter._capture_publish_response_outcome = AsyncMock(
            return_value={
                "verified": False,
                "status": "failed_manual",
                "verification_state": "failed",
                "last_error": "Xiaohongshu publish rejected: 因违反社区规范禁止发笔记, code=-9136",
                "manual_required": True,
                "verification_source": "publish_response",
            }
        )
        adapter._inspect_post_publish_page = AsyncMock(return_value=None)
        adapter._verify_publish_result = AsyncMock(return_value={})

        result = adapter.run_async(adapter._publish_async(payload))

        self.assertEqual(result["status"], "failed_manual")
        self.assertTrue(result["manual_required"])
        self.assertEqual(result["verification_source"], "publish_response")
        self.assertIn("社区规范", result["last_error"])
        adapter._inspect_post_publish_page.assert_not_awaited()
        adapter._verify_publish_result.assert_not_awaited()

    def test_publish_marks_unverified_result_retryable_when_verification_is_unknown(self):
        adapter = XiaohongshuAdapter(config={}, selectors={})
        job = self._make_job()
        adapter._publish_async = AsyncMock(
            return_value={
                "verified": False,
                "status": "failed_retryable",
                "verification_state": "unknown",
                "platform_content_id": "",
                "platform_url": "",
                "published_at": "",
                "last_error": "Xiaohongshu publish result could not be verified from note manager",
            }
        )

        result = adapter.publish(job)

        self.assertEqual(result["status"], "failed_retryable")
        self.assertIn("could not be verified", result["last_error"])
        self.assertEqual(result["extra"]["verification_state"], "unknown")

    def test_publish_maps_note_manager_row_success_to_published_result(self):
        adapter = XiaohongshuAdapter(config={}, selectors={})
        job = self._make_job()
        adapter._publish_async = AsyncMock(
            return_value={
                "verified": True,
                "status": "published",
                "verification_state": "verified",
                "platform_content_id": "",
                "platform_url": "https://creator.xiaohongshu.com/new/note-manager",
                "published_at": "",
                "last_error": "",
                "verification_source": "note_manager_row",
            }
        )

        result = adapter.publish(job)

        self.assertEqual(result["status"], "published")
        self.assertEqual(result["platform_content_id"], "")
        self.assertEqual(result["platform_url"], "https://creator.xiaohongshu.com/new/note-manager")
        self.assertEqual(result["last_error"], "")
        self.assertEqual(result["extra"]["verification_state"], "verified")
        self.assertEqual(result["extra"]["verification_source"], "note_manager_row")

    def test_verify_publish_result_flags_login_gate_as_manual_required(self):
        adapter = XiaohongshuAdapter(config={}, selectors={})
        payload = normalize_xiaohongshu_publish_payload(self._make_job())
        verify_page = _FakeVerificationPage(
            url="https://creator.xiaohongshu.com/login",
            body_text="请先登录后继续操作",
            links=[],
        )
        context = AsyncMock()
        context.new_page = AsyncMock(return_value=verify_page)

        result = adapter.run_async(adapter._verify_publish_result(context, payload))

        self.assertFalse(result["verified"])
        self.assertTrue(result["manual_required"])
        self.assertEqual(result["status"], "failed_manual")
        self.assertEqual(result["verification_state"], "manual")
        self.assertIn("login", result["last_error"])


if __name__ == "__main__":
    unittest.main()

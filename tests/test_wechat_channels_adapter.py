import unittest
from unittest.mock import AsyncMock, Mock, call, patch

import core.publishing.adapters.wechat_channels as wechat_channels_module

from core.publishing.adapters.wechat_channels import (
    WechatChannelsAdapter,
    build_wechat_verify_terms,
    extract_wechat_manual_error,
    extract_wechat_post_list_snapshot,
    format_wechat_publish_time,
    normalize_wechat_publish_payload,
    sanitize_wechat_short_title,
)
from core.publishing.models import PublishJob


class TestWechatChannelsAdapter(unittest.TestCase):
    def _make_job(self, **overrides):
        data = {
            "platform_targets": ["wechat_channels"],
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

        payload = normalize_wechat_publish_payload(job)

        self.assertEqual(payload["title"], "Morning Workout")
        self.assertEqual(payload["video_path"], "/tmp/video.mp4")
        self.assertEqual(payload["cover_path"], "/tmp/cover.jpg")
        self.assertIn("#fitness", payload["description"])
        self.assertIn("#strength", payload["description"])
        self.assertNotIn("#extra", payload["description"])

    def test_normalize_publish_payload_truncates_short_title_to_wechat_guidance(self):
        job = self._make_job(title="居家40分钟全身燃脂：无需器械，练就紧致线条")

        payload = normalize_wechat_publish_payload(job)

        self.assertEqual(payload["title"], "居家40分钟全身燃脂：无需器械")
        self.assertEqual(len(payload["title"]), 15)

    def test_normalize_publish_payload_prefers_platform_overrides(self):
        job = self._make_job(
            platform_payloads={
                "wechat_channels": {
                    "title": "视频号标题",
                    "description": "视频号简介",
                    "tags": ["燃脂", "居家"],
                    "video_path": "/tmp/wechat.mp4",
                    "cover_path": "/tmp/wechat.jpg",
                }
            }
        )

        payload = normalize_wechat_publish_payload(job)

        self.assertEqual(payload["title"], "视频号标题")
        self.assertEqual(payload["video_path"], "/tmp/wechat.mp4")
        self.assertEqual(payload["cover_path"], "/tmp/wechat.jpg")
        self.assertIn("#燃脂", payload["description"])
        self.assertIn("视频号简介", payload["description"])

    def test_normalize_publish_payload_falls_back_when_override_title_is_too_short(self):
        job = self._make_job(
            title="视频号标题至少六个字",
            platform_payloads={
                "wechat_channels": {
                    "title": "【UE5",
                    "description": "视频号简介",
                }
            },
        )

        payload = normalize_wechat_publish_payload(job)

        self.assertEqual(payload["title"], "视频号标题至少六个字")

    def test_normalize_publish_payload_preserves_extra_platform_fields(self):
        job = self._make_job(
            platform_payloads={
                "wechat_channels": {
                    "allow_comment": False,
                    "declare_original": True,
                    "collection_name": "居家训练",
                }
            }
        )

        payload = normalize_wechat_publish_payload(job)

        self.assertFalse(payload["allow_comment"])
        self.assertTrue(payload["declare_original"])
        self.assertEqual(payload["collection_name"], "居家训练")

    def test_sanitize_wechat_short_title_removes_unsupported_punctuation(self):
        self.assertEqual(
            sanitize_wechat_short_title("居家40分钟全身燃脂：无需器械，练就紧致线条！"),
            "居家40分钟全身燃脂：无需器械",
        )

    def test_extract_wechat_post_list_snapshot_keeps_newest_object(self):
        snapshot = extract_wechat_post_list_snapshot(
            {
                "data": {
                    "list": [
                        {"objectId": "obj_new", "createTime": 1775747076},
                        {"objectId": "obj_old", "createTime": 1775740000},
                    ]
                }
            }
        )

        self.assertEqual(snapshot["count"], 2)
        self.assertEqual(snapshot["object_ids"], ["obj_new", "obj_old"])
        self.assertEqual(snapshot["newest_object_id"], "obj_new")
        self.assertEqual(snapshot["newest_create_time"], 1775747076)

    def test_extract_wechat_post_list_snapshot_supports_nested_records_and_matches_title(self):
        snapshot = extract_wechat_post_list_snapshot(
            {
                "data": {
                    "records": [
                        {
                            "postId": "export/new_post",
                            "title": "30分钟哑铃下肢塑形，打开就能跟练",
                            "createTime": 1775747076,
                        }
                    ]
                }
            },
            "30分钟哑铃下肢塑形",
        )

        self.assertEqual(snapshot["object_ids"], ["export/new_post"])
        self.assertEqual(snapshot["matched_object_id"], "export/new_post")
        self.assertEqual(snapshot["matched_create_time"], 1775747076)
        self.assertIn("30分钟哑铃下肢塑形", snapshot["titles"][0])

    def test_extract_wechat_post_list_snapshot_uses_short_title_and_ignores_nested_desc_dicts(self):
        snapshot = extract_wechat_post_list_snapshot(
            {
                "data": {
                    "list": [
                        {
                            "objectId": "export/abc123",
                            "exportId": "export/abc123",
                            "createTime": 1775748536,
                            "desc": {
                                "media": [],
                                "shortTitle": [{"shortTitle": "居家40分钟全身燃脂：无需器械"}],
                                "description": "",
                            },
                            "shortTitle": [{"shortTitle": "居家40分钟全身燃脂：无需器械"}],
                            "description": "",
                        }
                    ]
                }
            },
            "居家40分钟全身燃脂：无需器械",
        )

        self.assertEqual(snapshot["count"], 1)
        self.assertEqual(snapshot["object_ids"], ["export/abc123"])
        self.assertEqual(snapshot["newest_object_id"], "export/abc123")
        self.assertEqual(snapshot["matched_object_id"], "export/abc123")
        self.assertEqual(snapshot["matched_create_time"], 1775748536)
        self.assertEqual(snapshot["titles"], ["居家40分钟全身燃脂：无需器械"])

    def test_extract_wechat_post_list_snapshot_ignores_nested_bare_id_metadata_dicts(self):
        snapshot = extract_wechat_post_list_snapshot(
            {
                "data": {
                    "list": [
                        {
                            "objectId": "export/abc123",
                            "createTime": 1775748536,
                            "shortTitle": "居家40分钟全身燃脂：无需器械",
                            "desc": {
                                "media": [
                                    {
                                        "id": "metadata_only_id",
                                        "title": "不应该当成新作品",
                                    }
                                ]
                            },
                        }
                    ]
                }
            },
            "居家40分钟全身燃脂：无需器械",
        )

        self.assertEqual(snapshot["count"], 1)
        self.assertEqual(snapshot["object_ids"], ["export/abc123"])
        self.assertEqual(snapshot["matched_object_id"], "export/abc123")

    def test_build_wechat_verify_terms_keeps_short_and_prefix_terms(self):
        self.assertEqual(
            build_wechat_verify_terms("30分钟哑铃下肢塑形"),
            ["30分钟哑铃下肢塑形", "30分钟哑铃下肢塑", "30分钟哑铃下肢"],
        )

    def test_format_wechat_publish_time_formats_unix_seconds(self):
        self.assertTrue(format_wechat_publish_time(1775747076).startswith("2026-04-"))

    def test_extract_wechat_manual_error_detects_permission_and_admin_verification_gates(self):
        permission_error = extract_wechat_manual_error(
            "你还不能发表视频 当前登录账号不是视频号「必大器」的管理员或运营者"
        )
        admin_error = extract_wechat_manual_error(
            "管理员本人验证 为保障账号安全，需管理员扫码验证通过后可继续发表作品"
        )

        self.assertIn("manager/operator permission", permission_error)
        self.assertIn("admin QR verification", admin_error)

    def test_capabilities_and_validate_requirements_match_beta_automation(self):
        adapter = WechatChannelsAdapter(config={}, selectors={})

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
        adapter = WechatChannelsAdapter(
            config={},
            selectors={
                "publish_url": "https://channels.weixin.qq.com",
                "locators": {
                    "title": ["textarea"],
                    "description": ["[contenteditable='true']"],
                    "video_upload": ["input[type='file']"],
                    "cover_upload": ["input[type='file']"],
                    "publish_button": ["button"],
                },
            },
        )
        job = self._make_job()
        adapter._publish_async = AsyncMock(
            return_value={
                "platform_content_id": "wechat_123",
                "platform_url": "https://channels.weixin.qq.com/s/wechat_123",
                "published_at": "2026-04-08T10:00:00+08:00",
                "last_error": "",
            }
        )

        result = adapter.publish(job)

        self.assertEqual(result["platform"], "wechat_channels")
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["platform_content_id"], "wechat_123")
        self.assertEqual(result["platform_url"], "https://channels.weixin.qq.com/s/wechat_123")
        self.assertEqual(result["scheduled_by"], "biyou")
        self.assertEqual(result["scheduled_for"], "")
        self.assertEqual(result["extra"]["title"], "Morning Workout")
        adapter._publish_async.assert_awaited_once()

    def test_publish_marks_result_retryable_when_verification_does_not_confirm_publish(self):
        adapter = WechatChannelsAdapter(config={}, selectors={})
        job = self._make_job()
        adapter._publish_async = AsyncMock(
            return_value={
                "verified": False,
                "platform_content_id": "",
                "platform_url": "",
                "published_at": "",
                "last_error": "WeChat Channels publish result could not be verified from video list",
            }
        )

        result = adapter.publish(job)

        self.assertEqual(result["status"], "failed_retryable")
        self.assertIn("could not be verified", result["last_error"])

    def test_publish_keeps_retryable_status_when_verification_is_unknown(self):
        adapter = WechatChannelsAdapter(config={}, selectors={})
        job = self._make_job()
        adapter._publish_async = AsyncMock(
            return_value={
                "verified": False,
                "status": "failed_retryable",
                "platform_content_id": "",
                "platform_url": "",
                "published_at": "",
                "last_error": "WeChat Channels publish result could not be verified from video list",
            }
        )

        result = adapter.publish(job)

        self.assertEqual(result["status"], "failed_retryable")
        self.assertIn("could not be verified", result["last_error"])

    def test_verify_publish_result_uses_matched_object_id_from_snapshot(self):
        adapter = WechatChannelsAdapter(config={}, selectors={})
        context = AsyncMock()
        adapter._fetch_post_list_snapshot = AsyncMock(
            side_effect=[
                {
                    "count": 1,
                    "object_ids": ["export/old_post"],
                    "titles": ["旧视频"],
                    "newest_object_id": "export/old_post",
                    "newest_create_time": 1775740000,
                    "matched_object_id": "",
                    "matched_create_time": "",
                },
                {
                    "count": 1,
                    "object_ids": ["export/old_post"],
                    "titles": ["旧视频", "30分钟哑铃下肢塑形，打开就能跟练"],
                    "newest_object_id": "export/old_post",
                    "newest_create_time": 1775740000,
                    "matched_object_id": "export/new_post",
                    "matched_create_time": 1775747076,
                },
            ]
        )

        result = adapter.run_async(
            adapter._verify_publish_result(
                context,
                {
                    "count": 1,
                    "object_ids": ["export/old_post"],
                    "titles": ["旧视频"],
                    "newest_object_id": "export/old_post",
                    "newest_create_time": 1775740000,
                    "matched_object_id": "",
                    "matched_create_time": "",
                },
                "30分钟哑铃下肢塑形",
            )
        )

        self.assertTrue(result["verified"])
        self.assertEqual(result["platform_content_id"], "export/new_post")
        self.assertTrue(result["published_at"].startswith("2026-04-"))
        self.assertEqual(result["verification_source"], "post_list_response")

    def test_verify_publish_result_rejects_unrelated_new_object_without_title_match(self):
        adapter = WechatChannelsAdapter(config={}, selectors={})
        context = AsyncMock()
        adapter._fetch_post_list_snapshot = AsyncMock(
            return_value={
                "count": 2,
                "object_ids": ["export/old_post", "export/unrelated_new_post"],
                "titles": ["旧视频", "完全不相关的视频"],
                "newest_object_id": "export/unrelated_new_post",
                "newest_create_time": 1775747076,
                "matched_object_id": "",
                "matched_create_time": "",
            }
        )

        with patch.object(wechat_channels_module, "VERIFY_TIMEOUT_MS", 0), patch.object(
            wechat_channels_module,
            "VERIFY_POLL_MS",
            1,
        ):
            result = adapter.run_async(
                adapter._verify_publish_result(
                    context,
                    {
                        "count": 1,
                        "object_ids": ["export/old_post"],
                        "titles": ["旧视频"],
                        "newest_object_id": "export/old_post",
                        "newest_create_time": 1775740000,
                        "matched_object_id": "",
                        "matched_create_time": "",
                    },
                    "30分钟哑铃下肢塑形",
                )
            )

        self.assertFalse(result["verified"])
        self.assertIn("could not be verified", result["last_error"])

    def test_verify_publish_result_reuses_single_verify_page_across_polls(self):
        adapter = WechatChannelsAdapter(config={}, selectors={})
        context = AsyncMock()
        verify_page = AsyncMock()
        verify_page.close = AsyncMock()
        context.new_page = AsyncMock(return_value=verify_page)
        adapter._fetch_post_list_snapshot = AsyncMock(
            side_effect=[
                {
                    "count": 1,
                    "object_ids": ["export/old_post"],
                    "titles": ["旧视频"],
                    "newest_object_id": "export/old_post",
                    "newest_create_time": 1775740000,
                    "matched_object_id": "",
                    "matched_create_time": "",
                },
                {
                    "count": 1,
                    "object_ids": ["export/old_post"],
                    "titles": ["旧视频", "30分钟哑铃下肢塑形，打开就能跟练"],
                    "newest_object_id": "export/old_post",
                    "newest_create_time": 1775740000,
                    "matched_object_id": "export/new_post",
                    "matched_create_time": 1775747076,
                },
            ]
        )

        result = adapter.run_async(
            adapter._verify_publish_result(
                context,
                {
                    "count": 1,
                    "object_ids": ["export/old_post"],
                    "titles": ["旧视频"],
                    "newest_object_id": "export/old_post",
                    "newest_create_time": 1775740000,
                    "matched_object_id": "",
                    "matched_create_time": "",
                },
                "30分钟哑铃下肢塑形",
            )
        )

        self.assertTrue(result["verified"])
        context.new_page.assert_awaited_once()
        verify_page.close.assert_awaited_once()
        for call_item in adapter._fetch_post_list_snapshot.await_args_list:
            self.assertIs(call_item.kwargs["verify_page"], verify_page)

    def test_fetch_post_list_snapshot_returns_empty_snapshot_when_response_times_out(self):
        class _TimeoutContext:
            async def __aenter__(self):
                raise TimeoutError("timed out")

            async def __aexit__(self, exc_type, exc, tb):
                return False

        adapter = WechatChannelsAdapter(config={}, selectors={})
        verify_page = AsyncMock()
        verify_page.goto = AsyncMock()
        verify_page.expect_response = Mock(return_value=_TimeoutContext())

        result = adapter.run_async(
            adapter._fetch_post_list_snapshot(
                AsyncMock(),
                "",
                "Morning Workout",
                verify_page=verify_page,
            )
        )

        self.assertEqual(result["object_ids"], [])
        self.assertEqual(result["matched_object_id"], "")
        self.assertEqual(result["platform_url"], "https://channels.weixin.qq.com/platform/post/list")
        verify_page.goto.assert_awaited_once_with(
            "https://channels.weixin.qq.com/platform/post/list",
            wait_until="domcontentloaded",
        )

    def test_schedule_returns_biyou_managed_result_with_normalized_payload(self):
        adapter = WechatChannelsAdapter(config={}, selectors={})
        job = self._make_job()

        result = adapter.schedule(job, "2026-04-08T10:00:00+08:00")

        self.assertEqual(result["platform"], "wechat_channels")
        self.assertEqual(result["status"], "scheduled")
        self.assertEqual(result["scheduled_by"], "biyou")
        self.assertEqual(result["scheduled_for"], "2026-04-08T10:00:00+08:00")
        self.assertEqual(result["extra"]["cover_path"], "/tmp/cover.jpg")
        self.assertIn("#fitness", result["extra"]["description"])

    def test_publish_maps_verification_and_cover_state_into_extra(self):
        adapter = WechatChannelsAdapter(config={}, selectors={})
        job = self._make_job()
        adapter._publish_async = AsyncMock(
            return_value={
                "verified": True,
                "verification_source": "post_list_response",
                "platform_content_id": "wechat_123",
                "platform_url": "https://channels.weixin.qq.com/platform/post/list",
                "published_at": "",
                "last_error": "",
                "cover_requested": True,
                "cover_selection_state": "selected",
                "cover_confirmation_state": "unconfirmed",
                "cover_apply_method": "uploaded_image",
                "cover_last_error": "",
            }
        )

        result = adapter.publish(job)

        self.assertEqual(result["status"], "published")
        self.assertEqual(result["extra"]["verification_source"], "post_list_response")
        self.assertEqual(result["extra"]["cover_selection_state"], "selected")
        self.assertEqual(result["extra"]["cover_confirmation_state"], "unconfirmed")
        self.assertEqual(result["extra"]["cover_apply_method"], "uploaded_image")

    def test_publish_async_waits_for_processing_text_to_clear_before_publish(self):
        adapter = WechatChannelsAdapter(
            config={},
            selectors={
                "publish_url": "https://channels.weixin.qq.com",
                "frame_name": "content",
                "locators": {
                    "title": ["textarea"],
                    "description": ["[contenteditable='true']"],
                    "video_upload": ["input[type='file']"],
                    "cover_upload": [],
                    "publish_button": ["button"],
                },
            },
        )
        payload = normalize_wechat_publish_payload(self._make_job())
        playwright = AsyncMock()
        context = AsyncMock()
        page = AsyncMock()
        target = AsyncMock()
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        adapter._resolve_frame = AsyncMock(return_value=target)
        adapter._fetch_post_list_snapshot = AsyncMock(
            return_value={"count": 0, "object_ids": [], "newest_object_id": "", "newest_create_time": ""}
        )
        adapter._set_file_first = AsyncMock(return_value=None)
        adapter._wait_for_any_selector = AsyncMock(return_value="textarea")
        adapter._wait_for_text_to_clear = AsyncMock(return_value="页面已准备好")
        adapter._fill_first = AsyncMock(return_value=None)
        adapter._apply_option_controls = AsyncMock(return_value=None)
        adapter._click_first = AsyncMock(return_value=None)
        adapter._verify_publish_result = AsyncMock(
            return_value={
                "verified": True,
                "platform_content_id": "",
                "platform_url": "https://channels.weixin.qq.com/platform/post/list",
                "published_at": "",
                "last_error": "",
            }
        )

        result = adapter.run_async(adapter._publish_async(payload))

        self.assertTrue(result["verified"])
        adapter._fill_first.assert_has_awaits(
            [
                call(target, ["textarea"], payload["title"]),
                call(target, ["[contenteditable='true']"], payload["description"]),
            ]
        )
        self.assertEqual(adapter._resolve_frame.await_count, 6)
        adapter._fetch_post_list_snapshot.assert_awaited_once_with(
            context.browser_context,
            "content",
            payload["title"],
        )
        adapter._wait_for_text_to_clear.assert_awaited_once_with(
            target,
            ["上传中", "处理中"],
            timeout_ms=600000,
            poll_ms=1000,
        )
        adapter._apply_option_controls.assert_awaited_once_with(target, payload, {})
        adapter._click_first.assert_awaited_once_with(target, ["button"])
        adapter._verify_publish_result.assert_awaited_once_with(
            context.browser_context,
            {
                "count": 0,
                "object_ids": [],
                "newest_object_id": "",
                "newest_create_time": "",
            },
            payload["title"],
        )

    def test_publish_async_applies_configured_option_controls(self):
        adapter = WechatChannelsAdapter(
            config={},
            selectors={
                "publish_url": "https://channels.weixin.qq.com",
                "frame_name": "content",
                "locators": {
                    "title": ["textarea"],
                    "description": ["[contenteditable='true']"],
                    "video_upload": ["input[type='file']"],
                    "cover_upload": [],
                    "publish_button": ["button"],
                },
                "option_controls": {
                    "declare_original": {
                        "kind": "click",
                        "selectors": ["button:has-text('声明原创')"],
                    }
                },
            },
        )
        payload = normalize_wechat_publish_payload(
            self._make_job(
                platform_payloads={"wechat_channels": {"declare_original": True}}
            )
        )
        playwright = AsyncMock()
        context = AsyncMock()
        page = AsyncMock()
        target = AsyncMock()
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        adapter._resolve_frame = AsyncMock(return_value=target)
        adapter._fetch_post_list_snapshot = AsyncMock(
            return_value={"count": 0, "object_ids": [], "newest_object_id": "", "newest_create_time": ""}
        )
        adapter._set_file_first = AsyncMock(return_value=None)
        adapter._wait_for_any_selector = AsyncMock(return_value="textarea")
        adapter._wait_for_text_to_clear = AsyncMock(return_value="页面已准备好")
        adapter._fill_first = AsyncMock(return_value=None)
        adapter._apply_option_controls = AsyncMock(return_value=None)
        adapter._click_first = AsyncMock(return_value=None)
        adapter._verify_publish_result = AsyncMock(
            return_value={
                "verified": True,
                "platform_content_id": "",
                "platform_url": "",
                "published_at": "",
                "last_error": "",
            }
        )

        adapter.run_async(adapter._publish_async(payload))

        adapter._apply_option_controls.assert_awaited_once_with(
            target,
            payload,
            adapter.selectors["option_controls"],
        )

    def test_publish_async_stops_before_publish_when_video_is_still_processing(self):
        adapter = WechatChannelsAdapter(
            config={},
            selectors={
                "publish_url": "https://channels.weixin.qq.com",
                "frame_name": "content",
                "locators": {
                    "title": ["textarea"],
                    "description": ["[contenteditable='true']"],
                    "video_upload": ["input[type='file']"],
                    "cover_upload": [],
                    "publish_button": ["button"],
                },
            },
        )
        payload = normalize_wechat_publish_payload(self._make_job())
        playwright = AsyncMock()
        context = AsyncMock()
        page = AsyncMock()
        target = AsyncMock()
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        adapter._resolve_frame = AsyncMock(return_value=target)
        adapter._fetch_post_list_snapshot = AsyncMock(
            return_value={"count": 0, "object_ids": [], "newest_object_id": "", "newest_create_time": ""}
        )
        adapter._set_file_first = AsyncMock(return_value=None)
        adapter._wait_for_any_selector = AsyncMock(return_value="textarea")
        adapter._wait_for_text_to_clear = AsyncMock(return_value="上传中 处理中")
        adapter._fill_first = AsyncMock(return_value=None)
        adapter._click_first = AsyncMock(return_value=None)
        adapter._verify_publish_result = AsyncMock(return_value={})

        result = adapter.run_async(adapter._publish_async(payload))

        self.assertFalse(result["verified"])
        self.assertIn("still processing", result["last_error"])
        self.assertEqual(adapter._resolve_frame.await_count, 4)
        adapter._click_first.assert_not_awaited()
        adapter._verify_publish_result.assert_not_awaited()

    def test_publish_async_records_cover_as_selected_but_unconfirmed_without_confirmation_signal(self):
        adapter = WechatChannelsAdapter(
            config={},
            selectors={
                "publish_url": "https://channels.weixin.qq.com",
                "frame_name": "content",
                "locators": {
                    "title": ["textarea"],
                    "description": ["[contenteditable='true']"],
                    "video_upload": ["input[type='file']"],
                    "cover_activate": ["button:has-text('更换封面')"],
                    "cover_upload": ["input[type='file'][accept*='image']"],
                    "publish_button": ["button"],
                },
            },
        )
        payload = normalize_wechat_publish_payload(self._make_job())
        playwright = AsyncMock()
        context = AsyncMock()
        page = AsyncMock()
        target = AsyncMock()
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        adapter._resolve_frame = AsyncMock(return_value=target)
        adapter._fetch_post_list_snapshot = AsyncMock(
            return_value={"count": 0, "object_ids": [], "newest_object_id": "", "newest_create_time": ""}
        )
        adapter._set_file_first = AsyncMock(return_value=None)
        adapter._wait_for_any_selector = AsyncMock(return_value="textarea")
        adapter._wait_for_text_to_clear = AsyncMock(return_value="页面已准备好")
        adapter._fill_first = AsyncMock(return_value=None)
        adapter._apply_option_controls = AsyncMock(return_value=None)
        adapter._click_first = AsyncMock(return_value=None)
        adapter._js_click_text = AsyncMock(side_effect=[True, True])
        adapter._extract_body_text = AsyncMock(return_value="")
        adapter._verify_publish_result = AsyncMock(
            return_value={
                "verified": True,
                "verification_source": "post_list_response",
                "platform_content_id": "wechat_123",
                "platform_url": "https://channels.weixin.qq.com/platform/post/list",
                "published_at": "",
                "last_error": "",
            }
        )

        with patch("core.publishing.adapters.wechat_channels.os.path.exists", return_value=True):
            result = adapter.run_async(adapter._publish_async(payload))

        self.assertTrue(result["verified"])
        self.assertEqual(result["cover_selection_state"], "selected")
        self.assertEqual(result["cover_confirmation_state"], "unconfirmed")
        self.assertEqual(result["cover_apply_method"], "uploaded_image")
        self.assertEqual(adapter._set_file_first.await_count, 2)

    def test_publish_async_stops_before_publish_when_validation_error_is_present(self):
        adapter = WechatChannelsAdapter(
            config={},
            selectors={
                "publish_url": "https://channels.weixin.qq.com",
                "frame_name": "content",
                "locators": {
                    "title": ["textarea"],
                    "description": ["[contenteditable='true']"],
                    "video_upload": ["input[type='file']"],
                    "cover_upload": [],
                    "publish_button": ["button"],
                },
            },
        )
        payload = normalize_wechat_publish_payload(self._make_job())
        playwright = AsyncMock()
        context = AsyncMock()
        page = AsyncMock()
        target = AsyncMock()
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        adapter._resolve_frame = AsyncMock(return_value=target)
        adapter._fetch_post_list_snapshot = AsyncMock(
            return_value={"count": 0, "object_ids": [], "newest_object_id": "", "newest_create_time": ""}
        )
        adapter._set_file_first = AsyncMock(return_value=None)
        adapter._wait_for_any_selector = AsyncMock(return_value="textarea")
        adapter._wait_for_text_to_clear = AsyncMock(return_value="标题包含特殊字符")
        adapter._fill_first = AsyncMock(return_value=None)
        adapter._click_first = AsyncMock(return_value=None)
        adapter._verify_publish_result = AsyncMock(return_value={})

        result = adapter.run_async(adapter._publish_async(payload))

        self.assertFalse(result["verified"])
        self.assertIn("validation failed", result["last_error"])
        adapter._click_first.assert_not_awaited()
        adapter._verify_publish_result.assert_not_awaited()

    def test_publish_async_stops_after_click_when_validation_error_appears(self):
        adapter = WechatChannelsAdapter(
            config={},
            selectors={
                "publish_url": "https://channels.weixin.qq.com",
                "frame_name": "content",
                "locators": {
                    "title": ["textarea"],
                    "description": ["[contenteditable='true']"],
                    "video_upload": ["input[type='file']"],
                    "cover_upload": [],
                    "publish_button": ["button"],
                },
            },
        )
        payload = normalize_wechat_publish_payload(self._make_job())
        playwright = AsyncMock()
        context = AsyncMock()
        page = AsyncMock()
        target = AsyncMock()
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        adapter._resolve_frame = AsyncMock(return_value=target)
        adapter._fetch_post_list_snapshot = AsyncMock(
            return_value={"count": 0, "object_ids": [], "newest_object_id": "", "newest_create_time": ""}
        )
        adapter._set_file_first = AsyncMock(return_value=None)
        adapter._wait_for_any_selector = AsyncMock(return_value="textarea")
        adapter._wait_for_text_to_clear = AsyncMock(return_value="页面已准备好")
        adapter._fill_first = AsyncMock(return_value=None)
        adapter._click_first = AsyncMock(return_value=None)
        adapter._extract_body_text = AsyncMock(return_value="请填写短标题")
        adapter._verify_publish_result = AsyncMock(return_value={})

        result = adapter.run_async(adapter._publish_async(payload))

        self.assertFalse(result["verified"])
        self.assertIn("validation failed", result["last_error"])
        adapter._click_first.assert_awaited_once()
        adapter._verify_publish_result.assert_not_awaited()

    def test_publish_async_stops_before_publish_when_manual_gate_is_present(self):
        adapter = WechatChannelsAdapter(
            config={},
            selectors={
                "publish_url": "https://channels.weixin.qq.com",
                "frame_name": "content",
                "locators": {
                    "title": ["textarea"],
                    "description": ["[contenteditable='true']"],
                    "video_upload": ["input[type='file']"],
                    "cover_upload": [],
                    "publish_button": ["button"],
                },
            },
        )
        payload = normalize_wechat_publish_payload(self._make_job())
        playwright = AsyncMock()
        context = AsyncMock()
        page = AsyncMock()
        target = AsyncMock()
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        adapter._resolve_frame = AsyncMock(return_value=target)
        adapter._fetch_post_list_snapshot = AsyncMock(
            return_value={"count": 0, "object_ids": [], "newest_object_id": "", "newest_create_time": ""}
        )
        adapter._set_file_first = AsyncMock(return_value=None)
        adapter._wait_for_any_selector = AsyncMock(return_value="textarea")
        adapter._wait_for_text_to_clear = AsyncMock(
            return_value="你还不能发表视频 当前登录账号不是视频号「必大器」的管理员或运营者"
        )
        adapter._fill_first = AsyncMock(return_value=None)
        adapter._click_first = AsyncMock(return_value=None)
        adapter._verify_publish_result = AsyncMock(return_value={})

        result = adapter.run_async(adapter._publish_async(payload))

        self.assertFalse(result["verified"])
        self.assertTrue(result["manual_required"])
        self.assertEqual(result["status"], "failed_manual")
        self.assertIn("manager/operator permission", result["last_error"])
        adapter._click_first.assert_not_awaited()
        adapter._verify_publish_result.assert_not_awaited()

    def test_publish_async_maps_missing_content_frame_on_login_page_to_manual_login_error(self):
        adapter = WechatChannelsAdapter(
            config={},
            selectors={
                "publish_url": "https://channels.weixin.qq.com/platform/post/create",
                "frame_name": "content",
                "locators": {
                    "title": ["textarea"],
                    "description": ["[contenteditable='true']"],
                    "video_upload": ["input[type='file']"],
                    "cover_upload": [],
                    "publish_button": ["button"],
                },
            },
        )
        payload = normalize_wechat_publish_payload(self._make_job())
        playwright = AsyncMock()
        context = AsyncMock()
        page = AsyncMock()
        page.url = "https://channels.weixin.qq.com/login.html"
        page.wait_for_timeout = AsyncMock()
        page.evaluate = AsyncMock(return_value="微信扫码登录 视频号助手")
        adapter._open_page = AsyncMock(return_value=(playwright, context, page))
        adapter._fetch_post_list_snapshot = AsyncMock(
            return_value={"count": 0, "object_ids": [], "newest_object_id": "", "newest_create_time": ""}
        )
        adapter._resolve_frame = AsyncMock(side_effect=RuntimeError("Unable to locate frame: content"))
        adapter._set_file_first = AsyncMock(return_value=None)
        adapter._verify_publish_result = AsyncMock(return_value={})

        result = adapter.run_async(adapter._publish_async(payload))

        self.assertFalse(result["verified"])
        self.assertEqual(result["status"], "failed_manual")
        self.assertTrue(result["manual_required"])
        self.assertIn("requires login", result["last_error"])
        adapter._set_file_first.assert_not_awaited()
        adapter._verify_publish_result.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

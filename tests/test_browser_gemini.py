import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from core.utils import browser_gemini


class TestBrowserGemini(unittest.TestCase):
    class _FakePage:
        def __init__(self, url="", closed=False):
            self.url = url
            self._closed = closed

        def is_closed(self):
            return self._closed

    class _FakeContext:
        def __init__(self, pages=None):
            self.pages = list(pages or [])
            self.created_pages = 0

        async def new_page(self):
            self.created_pages += 1
            page = TestBrowserGemini._FakePage("about:blank")
            self.pages.append(page)
            return page

    class _FakeBrowser:
        def __init__(self, contexts=None):
            self.contexts = list(contexts or [])
            self.created_contexts = 0

        async def new_context(self):
            self.created_contexts += 1
            context = TestBrowserGemini._FakeContext()
            self.contexts.append(context)
            return context

    def test_hard_text_only_detection_matches_refusal(self):
        self.assertTrue(
            browser_gemini._looks_like_hard_text_only_response(
                "I can't directly edit images here, but I can help write a prompt."
            )
        )
        self.assertTrue(
            browser_gemini._looks_like_hard_text_only_response(
                "我无法直接编辑图片，但我可以帮你写提示词。"
            )
        )

    def test_hard_text_only_detection_does_not_flag_generation_status(self):
        self.assertFalse(
            browser_gemini._looks_like_hard_text_only_response(
                "Working on it. I am generating the edited image now."
            )
        )
        self.assertFalse(
            browser_gemini._looks_like_hard_text_only_response(
                "正在处理这张图，请稍候。"
            )
        )

    def test_generation_failure_detection_matches_ui_failure_banner(self):
        self.assertTrue(
            browser_gemini._looks_like_generation_failure(
                "",
                "Gemini Image Editing Request Failed 与 Gemini 对话",
            )
        )
        self.assertTrue(
            browser_gemini._looks_like_generation_failure(
                "",
                "图片编辑请求失败，请重试",
            )
        )
        self.assertTrue(
            browser_gemini._looks_like_generation_failure(
                "",
                "Gemini Image Editing Request Declined. Try a different request.",
            )
        )
        self.assertTrue(
            browser_gemini._looks_like_generation_failure(
                "",
                "图片编辑请求被拒绝，请换个请求。",
            )
        )
        self.assertFalse(
            browser_gemini._looks_like_generation_failure(
                "",
                "正在生成图片，请稍候。",
            )
        )

    def test_generation_in_progress_detection_handles_ui_copy(self):
        self.assertTrue(
            browser_gemini._looks_like_generation_in_progress(
                "Working on it now.",
                "Gemini is generating your image. Please wait.",
            )
        )
        self.assertTrue(
            browser_gemini._looks_like_generation_in_progress(
                "我来处理。",
                "正在生成图片，请稍候。",
            )
        )
        self.assertTrue(
            browser_gemini._looks_like_generation_in_progress(
                "Integrating the Simplified Text",
                "Evaluating Text Integration",
            )
        )

    def test_cover_screenshot_fallback_disabled_by_default(self):
        self.assertFalse(browser_gemini._should_use_screenshot_cover_fallback())

    def test_cover_screenshot_fallback_can_be_enabled_explicitly(self):
        with patch.object(browser_gemini, "_config", {"services": {"cover": {"allow_screenshot_fallback": True}}}):
            self.assertTrue(browser_gemini._should_use_screenshot_cover_fallback())

    def test_prompt_text_match_requires_real_content(self):
        expected = "Edit this image and replace the visible English text with Simplified Chinese."
        self.assertTrue(
            browser_gemini._prompt_text_matches(
                "Edit this image and replace the visible English text with Simplified Chinese.",
                expected,
            )
        )
        self.assertFalse(browser_gemini._prompt_text_matches("", expected))
        self.assertFalse(browser_gemini._prompt_text_matches("short", expected))

    def test_attachment_present_detects_new_preview(self):
        baseline = {
            "file_names": [],
            "remove_buttons": 0,
            "preview_images": 0,
        }
        current = {
            "file_names": [],
            "remove_buttons": 1,
            "preview_images": 1,
        }
        self.assertTrue(
            browser_gemini._attachment_present(current, baseline, "cover.png")
        )
        self.assertFalse(
            browser_gemini._attachment_progress_active(
                {
                    "progress_elements": 0,
                    "body_text": "Upload file. Ready to send.",
                }
            )
        )
        self.assertTrue(
            browser_gemini._attachment_progress_active(
                {
                    "progress_elements": 0,
                    "body_text": "Uploading image. Please wait.",
                }
            )
        )

    def test_prompt_dispatch_detects_composer_clear(self):
        initial = "Perform a surgical text-only localization edit on this thumbnail."
        self.assertTrue(
            browser_gemini._prompt_dispatch_likely_started(initial, "")
        )
        self.assertTrue(
            browser_gemini._prompt_dispatch_likely_started(initial, "short")
        )
        self.assertFalse(
            browser_gemini._prompt_dispatch_likely_started(initial, initial)
        )

    def test_ensure_usable_account_keeps_current_available_account(self):
        original_index = browser_gemini._account_index
        browser_gemini._account_index = 2
        page = object()
        try:
            with patch.object(browser_gemini, "_refresh_account_config"), patch.object(
                browser_gemini,
                "_probe_single_account",
                AsyncMock(return_value={"valid": True, "status": "available", "cooldown_until": None}),
            ), patch.object(
                browser_gemini,
                "_probe_account_pool",
                AsyncMock(),
            ) as probe_pool:
                result = asyncio.run(browser_gemini._ensure_usable_account(page))
                self.assertTrue(result)
                self.assertEqual(browser_gemini._account_index, 2)
                probe_pool.assert_not_called()
        finally:
            browser_gemini._account_index = original_index

    def test_ensure_usable_account_rotates_only_after_current_fails(self):
        original_index = browser_gemini._account_index
        browser_gemini._account_index = 2
        page = object()
        try:
            with patch.object(browser_gemini, "_refresh_account_config"), patch.object(
                browser_gemini,
                "_probe_single_account",
                AsyncMock(return_value={"valid": True, "status": "limited_until", "cooldown_until": "2099-01-01T00:00"}),
            ), patch.object(
                browser_gemini,
                "_probe_account_pool",
                AsyncMock(),
            ) as probe_pool, patch.object(
                browser_gemini,
                "_get_next_available_account",
                return_value=1,
            ):
                result = asyncio.run(browser_gemini._ensure_usable_account(page))
                self.assertTrue(result)
                self.assertEqual(browser_gemini._account_index, 1)
                probe_pool.assert_awaited_once_with(page, force=True)
        finally:
            browser_gemini._account_index = original_index

    def test_probe_single_account_marks_upload_sign_in_prompt_as_invalid_login(self):
        page = AsyncMock()
        page.url = "https://gemini.google.com/app?authuser=2"

        with patch.object(
            browser_gemini,
            "_read_page_text",
            AsyncMock(return_value="登录账号即可上传文件"),
        ):
            result = asyncio.run(browser_gemini._probe_single_account(page, 2))

        self.assertEqual(result["status"], "invalid_login")
        self.assertIn("upload", (result.get("reason") or "").lower())

    def test_get_or_create_gemini_page_reuses_live_gemini_tab(self):
        existing = self._FakePage("https://gemini.google.com/app?authuser=2")
        context = self._FakeContext([existing, self._FakePage("about:blank")])
        browser = self._FakeBrowser([context])

        resolved_context, page = asyncio.run(browser_gemini._get_or_create_gemini_page(browser))

        self.assertIs(resolved_context, context)
        self.assertIs(page, existing)
        self.assertEqual(context.created_pages, 0)

    def test_get_or_create_gemini_page_opens_new_tab_when_only_non_gemini_tabs_exist(self):
        context = self._FakeContext(
            [
                self._FakePage("https://example.com"),
                self._FakePage("https://news.ycombinator.com", closed=True),
            ]
        )
        browser = self._FakeBrowser([context])

        resolved_context, page = asyncio.run(browser_gemini._get_or_create_gemini_page(browser))

        self.assertIs(resolved_context, context)
        self.assertEqual(page.url, "about:blank")
        self.assertEqual(context.created_pages, 1)

    def test_get_or_create_gemini_page_does_not_reuse_blank_tab(self):
        blank = self._FakePage("about:blank")
        context = self._FakeContext([blank])
        browser = self._FakeBrowser([context])

        resolved_context, page = asyncio.run(browser_gemini._get_or_create_gemini_page(browser))

        self.assertIs(resolved_context, context)
        self.assertIsNot(page, blank)
        self.assertEqual(page.url, "about:blank")
        self.assertEqual(context.created_pages, 1)

    def test_model_matches_target_accepts_matching_text_or_mode_id(self):
        target = {"key": "Thinking", "browser_mode_id": "5bf011840784117a"}

        self.assertTrue(
            browser_gemini._model_matches_target(
                "思考",
                None,
                target,
            )
        )
        self.assertTrue(
            browser_gemini._model_matches_target(
                "快速",
                "5bf011840784117a",
                target,
            )
        )
        self.assertFalse(
            browser_gemini._model_matches_target(
                "快速",
                "fbb127bbb056c959",
                target,
            )
        )

    def test_saved_image_looks_valid_rejects_empty_file(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            path = tmp.name
        try:
            self.assertFalse(browser_gemini._saved_image_looks_valid(path))
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_close_browser_skips_kill_when_browser_not_owned(self):
        original = browser_gemini._OWNS_DEBUG_BROWSER
        browser_gemini._OWNS_DEBUG_BROWSER = False
        try:
            with patch("subprocess.run") as mock_run:
                browser_gemini.close_browser()
            mock_run.assert_not_called()
        finally:
            browser_gemini._OWNS_DEBUG_BROWSER = original

    def test_close_browser_kills_only_when_browser_is_owned(self):
        original = browser_gemini._OWNS_DEBUG_BROWSER
        browser_gemini._OWNS_DEBUG_BROWSER = True
        try:
            with patch("subprocess.run") as mock_run:
                browser_gemini.close_browser()
            mock_run.assert_called_once()
            self.assertFalse(browser_gemini._OWNS_DEBUG_BROWSER)
        finally:
            browser_gemini._OWNS_DEBUG_BROWSER = original


if __name__ == "__main__":
    unittest.main()

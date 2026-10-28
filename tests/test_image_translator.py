import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from core.services.image_translator import ImageTranslationService


class TestImageTranslator(unittest.TestCase):
    class _BrowserLLM:
        provider = "gemini_web"
        use_browser = True

    class _WebApiLLM:
        provider = "gemini_web"
        use_browser = False

    class _CaptureLLM:
        provider = "test"
        use_browser = False

        def __init__(self):
            self.prompt = ""

        async def generate_image(self, prompt, input_image_path, output_path):
            self.prompt = prompt
            return output_path

    def test_compact_browser_prompt_detects_browser_mode(self):
        self.assertTrue(
            ImageTranslationService._should_use_compact_browser_prompt(
                self._BrowserLLM()
            )
        )
        self.assertFalse(
            ImageTranslationService._should_use_compact_browser_prompt(
                self._WebApiLLM()
            )
        )

    def test_compact_browser_prompt_stays_short_and_keeps_hints(self):
        prompt = ImageTranslationService._build_compact_browser_prompt(
            title="Unreal Engine 5.7 - Cinematic Lighting for Characters - Tutorial",
            aspect_ratio="16:9",
            translation_block="- MUST write '电影级角色灯光' when you see 'Cinematic Lighting for Characters'",
            cover_display_ratio="3:4",
            cover_layout="关键文案放在画面中央 3:4 安全区。",
        )

        self.assertIn("Edit the uploaded thumbnail image only.", prompt)
        self.assertIn("Reference title: Unreal Engine 5.7 - Cinematic Lighting for Characters - Tutorial", prompt)
        self.assertIn("Optional wording hints for visible text only:", prompt)
        self.assertIn("电影级角色灯光", prompt)
        self.assertIn("centered 3:4 safe area", prompt)
        self.assertIn("关键文案放在画面中央 3:4 安全区。", prompt)
        self.assertNotIn("Reference Description:", prompt)
        self.assertNotIn("no refusal", prompt.lower())

    def test_process_formats_platform_cover_variant_prompt_fields(self):
        llm = self._CaptureLLM()
        metadata = {
            "title": "居家40分钟全身燃脂：无需器械，练就紧致线条",
            "description": "各位好！今天带来一套高效居家跟练。",
            "platform_name": "xiaohongshu",
            "cover_style": "更像笔记标题，生活化、经验感、自述感更强。",
            "cover_text": "居家燃脂跟练",
            "cover_layout": "小红书优先做 3:4 竖版笔记封面。",
            "cover_display_ratio": "3:4",
        }

        with patch("core.services.image_translator.LLMFactory.get_service", return_value=llm), \
            patch.object(ImageTranslationService, "_get_translations", new=AsyncMock(return_value=("", {}))):
            output_path = asyncio.run(
                ImageTranslationService.process(
                    "/tmp/source.jpg",
                    "/tmp/output.jpg",
                    aspect_ratio="16:9",
                    prompt_template=(
                        "platform={platform_name}\n"
                        "style={cover_style}\n"
                        "text={cover_text}\n"
                        "layout={cover_layout}\n"
                        "display={cover_display_ratio}\n"
                        "title={title}"
                    ),
                    metadata=metadata,
                )
            )

        self.assertEqual(output_path, "/tmp/output.jpg")
        self.assertIn("platform=xiaohongshu", llm.prompt)
        self.assertIn("style=更像笔记标题，生活化、经验感、自述感更强。", llm.prompt)
        self.assertIn("text=居家燃脂跟练", llm.prompt)
        self.assertIn("layout=小红书优先做 3:4 竖版笔记封面。", llm.prompt)
        self.assertIn("display=3:4", llm.prompt)
        self.assertIn("title=居家40分钟全身燃脂：无需器械，练就紧致线条", llm.prompt)



if __name__ == "__main__":
    unittest.main()

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from core.services.llm.gemini_service import GeminiService


class TestGeminiService(unittest.TestCase):
    def test_generate_text_web_falls_back_to_browser_even_when_use_browser_false(self):
        service = GeminiService(
            {
                "provider": "gemini_web",
                "model": "Flash",
                "use_browser": False,
            }
        )

        with patch(
            "core.services.llm.gemini_service.get_web_client",
            new=AsyncMock(side_effect=RuntimeError("webapi blocked")),
        ), patch(
            "core.utils.browser_gemini.generate_text_browser",
            new=AsyncMock(return_value="browser text"),
        ) as browser_mock:
            result = asyncio.run(service._generate_text_web("hello"))

        self.assertEqual(result, "browser text")
        browser_mock.assert_awaited_once_with("hello", model="Flash")

    def test_generate_image_falls_back_to_flash_when_thinking_is_unavailable_in_browser(self):
        service = GeminiService(
            {
                "provider": "gemini_web",
                "model": "Thinking",
                "use_browser": True,
            }
        )

        browser_calls = []
        web_calls = []

        async def fake_browser(prompt, input_image_path, output_path, model=None):
            browser_calls.append(model)
            if model == "Thinking":
                raise ValueError(
                    "MODEL_UNAVAILABLE: Required model 'Thinking' is present but disabled in the current Gemini session."
                )
            if model == "Flash":
                return output_path
            return None

        async def fake_web(prompt, input_image_path, output_path, model=None):
            web_calls.append(model)
            return None

        with patch.object(
            service,
            "_try_browser_generation",
            new=AsyncMock(side_effect=fake_browser),
        ), patch.object(
            service,
            "_generate_image_web",
            new=AsyncMock(side_effect=fake_web),
        ), patch(
            "core.utils.decorators.asyncio.sleep",
            new=AsyncMock(),
        ):
            result = asyncio.run(
                service.generate_image(
                    "prompt",
                    input_image_path="/tmp/source.jpg",
                    output_path="/tmp/generated.jpg",
                )
            )

        self.assertEqual(result, "/tmp/generated.jpg")
        self.assertEqual(browser_calls, ["Thinking", "Flash"])
        self.assertEqual(web_calls, [])


if __name__ == "__main__":
    unittest.main()

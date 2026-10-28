import unittest

from core.modules.translator import _sanitize_activity_keyword


class TestActivityKeyword(unittest.TestCase):
    def test_sanitize_gemini_leak(self):
        raw = "显示思路\\nGemini 说\\n\\n科技"
        self.assertEqual(_sanitize_activity_keyword(raw), "科技")

    def test_reject_multiline_garbage(self):
        raw = "Gemini 说\\n\\n科技\\n更多内容"
        self.assertEqual(_sanitize_activity_keyword(raw), "科技")

    def test_reject_long_or_spaced(self):
        raw = "人工 智能"
        self.assertEqual(_sanitize_activity_keyword(raw), "")


if __name__ == "__main__":
    unittest.main()

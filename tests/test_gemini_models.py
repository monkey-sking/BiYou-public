import unittest
import re

from core.utils import gemini_models


class TestGeminiModels(unittest.TestCase):
    def test_normalize_browser_labels(self):
        self.assertEqual(gemini_models.normalize_gemini_model_key("快速"), "Flash")
        self.assertEqual(gemini_models.normalize_gemini_model_key("思考"), "Thinking")
        self.assertEqual(gemini_models.normalize_gemini_model_key("Pro"), "Pro")

    def test_normalize_web_and_official_names(self):
        self.assertEqual(
            gemini_models.normalize_gemini_model_key("gemini-3.0-flash-thinking"),
            "Thinking",
        )
        self.assertEqual(
            gemini_models.normalize_gemini_model_key("gemini-3.0-flash"),
            "Flash",
        )
        self.assertEqual(
            gemini_models.normalize_gemini_model_key("gemini-2.5-pro"),
            "Pro",
        )

    def test_normalize_current_ui_description(self):
        self.assertEqual(
            gemini_models.normalize_gemini_model_key("Pro 使用 3.1 Pro 处理高阶数学和代码任务"),
            "Pro",
        )

    def test_model_specs_expose_browser_mode_ids(self):
        specs = gemini_models.get_gemini_web_model_specs()
        self.assertEqual(specs["Flash"]["browser_mode_id"], "fbb127bbb056c959")
        self.assertEqual(specs["Thinking"]["browser_mode_id"], "5bf011840784117a")
        self.assertRegex(specs["Pro"]["browser_mode_id"], re.compile(r"^[0-9a-f]{16}$"))

    def test_model_specs_keep_pro_alias_even_if_package_name_lags(self):
        spec = gemini_models.get_gemini_web_model_spec("Pro")
        self.assertEqual(spec["key"], "Pro")
        self.assertIn("Pro", spec["browser_labels"])


if __name__ == "__main__":
    unittest.main()

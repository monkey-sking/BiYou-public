import unittest
from unittest.mock import patch

from core.utils import cookie


class TestCookieProfileResolution(unittest.TestCase):
    def test_resolve_browser_app_info_uses_effective_profile_by_default(self):
        with patch("core.utils.config.load_config", return_value={"browser": "chrome"}), patch.object(
            cookie,
            "resolve_browser_profile_dir",
            return_value="/tmp/.biyou-workflow-browser-data",
        ), patch.object(cookie.sys, "platform", "darwin"), patch("os.path.exists", return_value=True):
            browser_key, executable, profile = cookie.resolve_browser_app_info(browser="chrome")

        self.assertEqual(browser_key, "chrome")
        self.assertEqual(profile, "/tmp/.biyou-workflow-browser-data")
        self.assertTrue(executable.endswith("Google Chrome"))

    def test_resolve_browser_app_info_keeps_explicit_profile_override(self):
        with patch("core.utils.config.load_config", return_value={"browser": "chrome"}), patch.object(
            cookie,
            "resolve_browser_profile_dir",
            return_value="/tmp/.biyou-workflow-browser-data",
        ), patch.object(cookie.sys, "platform", "darwin"), patch("os.path.exists", return_value=True):
            _, _, profile = cookie.resolve_browser_app_info(
                browser="chrome",
                user_data_dir="/tmp/custom-profile",
            )

        self.assertEqual(profile, "/tmp/custom-profile")


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch

import main


class TestPreflightChecks(unittest.TestCase):
    @patch("core.utils.cookie.get_bilibili_cookies", return_value=None)
    @patch("builtins.input", side_effect=AssertionError("input should not be called"))
    @patch("core.utils.cookie.open_browser_for_login")
    @patch("core.utils.cookie.get_gemini_cookies", return_value={"secure_1psid": "token"})
    @patch("core.utils.cookie.validate_gemini_cookies", return_value=(True, None, "tester@example.com"))
    @patch.object(
        main,
        "_validate_bilibili_browser_session",
        return_value=(True, "uname=tester"),
        create=True,
    )
    def test_preflight_accepts_bilibili_browser_session_fallback(
        self,
        _fallback,
        _validate_gemini,
        _get_gemini,
        open_browser,
        _input,
        _get_bilibili,
    ):
        result = main.pre_flight_checks(auto_confirm=True)

        self.assertTrue(result)
        open_browser.assert_not_called()

    @patch("core.utils.cookie.get_bilibili_cookies", return_value=None)
    @patch("builtins.input", side_effect=AssertionError("input should not be called"))
    @patch("core.utils.cookie.open_browser_for_login")
    @patch.object(
        main,
        "_validate_bilibili_browser_session",
        return_value=(False, "browser session unavailable"),
        create=True,
    )
    def test_preflight_auto_confirm_fails_fast_when_bilibili_auth_missing(
        self,
        _fallback,
        open_browser,
        _input,
        _get_bilibili,
    ):
        result = main.pre_flight_checks(auto_confirm=True)

        self.assertFalse(result)
        open_browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()

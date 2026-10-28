import os
import unittest
from pathlib import Path
from unittest.mock import patch

from core.utils import browser_config


class TestBrowserConfig(unittest.TestCase):
    def test_default_profile_dir_uses_workflow_profile_name(self):
        self.assertEqual(
            browser_config.get_default_browser_profile_dir(),
            str(Path.home() / "Library/Application Support/Citro Labs/ego lite"),
        )

    def test_resolve_profile_prefers_initialized_configured_profile(self):
        configured = "/tmp/biyou-profile"
        with patch.object(
            browser_config,
            "browser_profile_has_state",
            side_effect=lambda path: path == configured,
        ):
            resolved = browser_config.resolve_browser_profile_dir(
                config={"browser_profile": configured}
            )
        self.assertEqual(resolved, configured)

    def test_resolve_profile_keeps_configured_workflow_profile_even_without_state(self):
        configured = "/tmp/.biyou-workflow-browser-data"
        with patch.object(
            browser_config,
            "browser_profile_has_state",
            return_value=False,
        ):
            resolved = browser_config.resolve_browser_profile_dir(
                config={"browser_profile": configured}
            )
        self.assertEqual(resolved, configured)

    def test_explicit_custom_profile_is_used_directly(self):
        custom = os.path.abspath("/tmp/custom-browser-profile")
        resolved = browser_config.resolve_browser_profile_dir(
            configured_profile=custom
        )
        self.assertEqual(resolved, custom)

    def test_invalid_debug_port_uses_default(self):
        port = browser_config.get_browser_debug_port({"browser_debug_port": "99999"})
        self.assertEqual(port, browser_config.DEFAULT_BROWSER_DEBUG_PORT)

    def test_prepare_browser_launch_reuses_exact_match(self):
        proc = {"pid": 1, "debug_port": 9333, "user_data_dir": "/tmp/profile"}
        with patch.object(
            browser_config,
            "find_running_browser_process",
            side_effect=[proc],
        ), patch.object(
            browser_config,
            "is_local_port_open",
            return_value=True,
        ):
            launch = browser_config.prepare_browser_launch(
                browser_executable="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                profile_dir="/tmp/profile",
                preferred_debug_port=9333,
            )
        self.assertEqual(launch["action"], "reuse")
        self.assertEqual(launch["debug_port"], 9333)

    def test_prepare_browser_launch_requests_restart_when_profile_running_without_debug_port(self):
        proc = {"pid": 42, "debug_port": None, "user_data_dir": "/tmp/profile"}
        with patch.object(
            browser_config,
            "find_running_browser_process",
            side_effect=[None, proc],
        ), patch.object(
            browser_config,
            "is_local_port_open",
            return_value=False,
        ):
            launch = browser_config.prepare_browser_launch(
                browser_executable="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                profile_dir="/tmp/profile",
                preferred_debug_port=39222,
            )
        self.assertEqual(launch["action"], "launch")
        self.assertEqual(launch["debug_port"], 39222)
        self.assertTrue(launch["restart_required"])
        self.assertEqual(launch["process"], proc)

    def test_prepare_browser_launch_moves_port_on_conflict(self):
        with patch.object(
            browser_config,
            "find_running_browser_process",
            side_effect=[None, None],
        ), patch.object(
            browser_config,
            "is_local_port_open",
            return_value=True,
        ), patch.object(
            browser_config,
            "find_available_debug_port",
            return_value=9333,
        ):
            launch = browser_config.prepare_browser_launch(
                browser_executable="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                profile_dir="/tmp/profile",
                preferred_debug_port=39222,
            )
        self.assertEqual(launch["action"], "launch")
        self.assertEqual(launch["debug_port"], 9333)


if __name__ == "__main__":
    unittest.main()

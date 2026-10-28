import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from core.modules import subtitle_retry


class TestSubtitleRetry(unittest.TestCase):
    def test_delay_scales_with_video_duration_and_is_bounded(self):
        self.assertEqual(subtitle_retry.calculate_delay_seconds({"duration": 120}), 600)
        self.assertEqual(subtitle_retry.calculate_delay_seconds({"duration": 7200}), 3600)
        self.assertEqual(subtitle_retry.calculate_delay_seconds({"duration": 40000}), 7200)

    def test_schedule_after_bilibili_success_spawns_single_history_retry(self):
        metadata = {
            "duration": 7200,
            "uploaded_bvid": "BV1abc12345",
            "video_path": "/tmp/demo.mp4",
        }

        with mock.patch.object(subtitle_retry, "subprocess") as subprocess_mock:
            subtitle_retry.schedule_after_bilibili_upload(
                metadata,
                "/tmp/demo.json",
                dry_run=False,
            )

        popen_args = subprocess_mock.Popen.call_args.args[0]
        self.assertIn("-m", popen_args)
        self.assertIn("core.modules.subtitle_retry", popen_args)
        self.assertIn("--history-path", popen_args)
        self.assertIn("/tmp/demo.json", popen_args)
        self.assertIn("--delay-seconds", popen_args)
        self.assertIn("3600", popen_args)

    def test_single_retry_success_is_silent_and_marks_history(self):
        history = {
            "id": "abcdefghijk",
            "uploaded_bvid": "BV1abc12345",
            "video_path": "/tmp/demo.mp4",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, "demo.json")
            with open(history_path, "w", encoding="utf-8") as f:
                json.dump(history, f)

            with mock.patch.object(subtitle_retry, "download_subtitles", return_value=["/tmp/sub.srt"]), \
                mock.patch.object(subtitle_retry, "upload_to_bilibili", return_value=True), \
                mock.patch.object(subtitle_retry, "clean_temp_files"):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    result = subtitle_retry.run_single_history_retry(
                        history_path,
                        max_attempts=3,
                        sleep_seconds=0,
                    )

            self.assertEqual(result.status, "uploaded")
            self.assertEqual(output.getvalue(), "")
            with open(history_path, "r", encoding="utf-8") as f:
                updated = json.load(f)
            self.assertIs(updated["subtitle_uploaded"], True)

    def test_single_retry_notifies_only_after_third_failure(self):
        history = {
            "id": "abcdefghijk",
            "uploaded_bvid": "BV1abc12345",
            "video_path": "/tmp/demo.mp4",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, "demo.json")
            with open(history_path, "w", encoding="utf-8") as f:
                json.dump(history, f)

            with mock.patch.object(subtitle_retry, "download_subtitles", return_value=None), \
                mock.patch.object(subtitle_retry, "clean_temp_files"):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    result = subtitle_retry.run_single_history_retry(
                        history_path,
                        max_attempts=3,
                        sleep_seconds=0,
                    )

            self.assertEqual(result.status, "failed")
            text = output.getvalue()
            self.assertIn("BiYou 字幕上传失败", text)
            self.assertIn("abcdefghijk", text)
            self.assertIn("3/3", text)
            with open(history_path, "r", encoding="utf-8") as f:
                updated = json.load(f)
            self.assertEqual(updated["_subtitle_retry_count"], 3)


if __name__ == "__main__":
    unittest.main()

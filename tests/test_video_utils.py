import os
import tempfile
import unittest
from unittest.mock import patch

from core.utils import video_utils


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestVideoUtils(unittest.TestCase):
    def test_verify_video_integrity_rejects_audio_only_media(self):
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
            tmp.write(b"x" * 4096)
            tmp_path = tmp.name

        calls = []

        def fake_run(cmd, capture_output=True, text=True, timeout=30):
            calls.append(cmd[0])
            if cmd[0] == "ffprobe":
                return _FakeCompletedProcess(
                    returncode=0,
                    stdout='{"streams":[{"codec_type":"audio"}]}',
                )
            if cmd[0] == "ffmpeg":
                return _FakeCompletedProcess(returncode=0, stderr="")
            raise AssertionError(f"Unexpected command: {cmd[0]}")

        try:
            with patch("core.utils.video_utils.subprocess.run", side_effect=fake_run):
                ok = video_utils.verify_video_integrity(tmp_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        self.assertFalse(ok)
        self.assertEqual(calls, ["ffprobe"])


if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import unittest
from io import BytesIO
from unittest.mock import patch

from core.modules import downloader


class _FakeHTTPResponse:
    def __init__(self, payload: bytes):
        self._buffer = BytesIO(payload)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size=-1):
        return self._buffer.read(size)


class _FakeYoutubeDL:
    def __init__(self, opts):
        self.opts = opts
        self.params = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def download(self, urls):
        target = self.params["outtmpl"]["default"]
        with open(target, "wb") as f:
            f.write(b"x" * 2048)


class _AuthFailYoutubeDL:
    def __init__(self, opts):
        self.opts = opts
        self.params = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, url, download=True):
        raise RuntimeError("Sign in to confirm you're not a bot")


class _MergedPathYoutubeDL:
    def __init__(self, opts):
        self.opts = opts
        self.params = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, url, download=True):
        title = "Merged Output Example"
        target = os.path.join(self.opts["outtmpl"].rsplit("/", 1)[0], f"{title}.mp4")
        with open(target, "wb") as f:
            f.write(b"x" * 4096)
        return {"title": title}

    def prepare_filename(self, info):
        title = info.get("title", "Merged Output Example")
        return os.path.join(self.opts["outtmpl"].rsplit("/", 1)[0], f"{title}.webm")


class TestDownloader(unittest.TestCase):
    def tearDown(self):
        downloader.get_yt_dlp_runtime_status.cache_clear()

    def test_download_progressive_fallback_stream_prefers_direct_http_download(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "core.modules.downloader.urllib.request.urlopen",
                return_value=_FakeHTTPResponse(b"x" * 2048),
            ), patch("core.modules.downloader.yt_dlp.YoutubeDL", _FakeYoutubeDL), patch(
                "core.modules.downloader.print_t"
            ):
                path = downloader._download_progressive_fallback_stream(
                    "https://rr.googlevideo.com/videoplayback?id=1",
                    video_dir=tmpdir,
                    title_hint="Example Video",
                    stream_headers_override={"referer": "https://www.youtube.com/watch?v=1"},
                    user_agent_override="ua",
                    temp_cookie_path=None,
                )

            self.assertEqual(path, os.path.join(tmpdir, "Example Video.mp4"))
            self.assertTrue(os.path.exists(path))
            self.assertGreater(os.path.getsize(path), 1024)

    def test_warn_if_ytdlp_runtime_is_outdated_when_version_is_old(self):
        downloader.get_yt_dlp_runtime_status.cache_clear()

        with patch.object(downloader.yt_dlp.version, "__version__", "2026.03.03"), patch(
            "core.modules.downloader.metadata.version",
            return_value="0.5.0",
        ), patch("core.modules.downloader.shutil.which", return_value="/usr/bin/deno"), patch(
            "core.modules.downloader.print_t"
        ) as print_t, patch("core.modules.downloader._console") as console:
            status = downloader.warn_if_ytdlp_runtime_is_outdated()

        self.assertFalse(status["version_ok"])
        self.assertEqual(status["version"], "2026.03.03")
        print_t.assert_called_once()
        console.assert_called_once()

    def test_fetch_video_info_prefers_browser_sniff_before_manual_login_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sniff_result = {
                "title": "Sniffed Video",
                "description": "Sniffed description",
                "thumbnail": "https://example.com/thumb.jpg",
                "id": "abc123",
                "url": None,
                "ua": "ua",
                "cookies": [{"name": "SID", "value": "x"}],
                "is_sabr_control": False,
                "progressive_stream_url": "https://rr.googlevideo.com/videoplayback?id=1",
                "playability_status": "OK",
                "playability_reason": None,
                "requires_login": False,
            }

            with patch("core.modules.downloader.load_config", return_value={"video_source_dir": tmpdir, "browser": "chrome"}), \
                patch("core.modules.downloader.warn_if_ytdlp_runtime_is_outdated"), \
                patch("core.modules.downloader._find_local_info_by_video_id", return_value=None), \
                patch("core.modules.downloader.apply_youtube_cookie_source", return_value="browser"), \
                patch("core.modules.downloader.refresh_youtube_cookies", return_value=False), \
                patch("core.modules.downloader.prompt_youtube_login") as prompt_youtube_login, \
                patch("core.modules.downloader.print_t"), \
                patch("core.modules.downloader.should_try_browser_sniff", return_value=True), \
                patch("core.modules.downloader.yt_dlp.YoutubeDL", _AuthFailYoutubeDL), \
                patch("core.modules.browser_downloader.sniff_video_info", return_value=sniff_result):
                info = downloader.fetch_video_info("https://www.youtube.com/watch?v=abc123")

        self.assertIsNotNone(info)
        self.assertEqual(info["title"], "Sniffed Video")
        self.assertEqual(info["id"], "abc123")
        self.assertEqual(
            info["progressive_stream_fallback_url"],
            "https://rr.googlevideo.com/videoplayback?id=1",
        )
        prompt_youtube_login.assert_not_called()

    def test_download_video_stream_resolves_merged_output_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            info = {"title": "Merged Output Example"}

            with patch("core.modules.downloader.load_config", return_value={"video_source_dir": tmpdir, "browser": "chrome"}), \
                patch("core.modules.downloader.warn_if_ytdlp_runtime_is_outdated"), \
                patch("core.modules.downloader.cleanup_temp_files"), \
                patch("core.modules.downloader.apply_youtube_cookie_source", return_value="browser"), \
                patch("core.modules.downloader.verify_video_integrity", return_value=True), \
                patch("core.modules.downloader.print_t"), \
                patch("core.modules.downloader.yt_dlp.YoutubeDL", _MergedPathYoutubeDL):
                path = downloader.download_video_stream("https://www.youtube.com/watch?v=abc123", info=info)
                exists_after_download = os.path.exists(path)

            self.assertEqual(path, os.path.join(tmpdir, "Merged Output Example.mp4"))
            self.assertTrue(exists_after_download)

    def test_resolve_downloaded_video_path_prefers_real_video_stream(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_only = os.path.join(tmpdir, "Example.f251-20.webm")
            merged_video = os.path.join(tmpdir, "Example.mp4")

            with open(audio_only, "wb") as f:
                f.write(b"a" * 8192)
            with open(merged_video, "wb") as f:
                f.write(b"v" * 4096)

            with patch(
                "core.modules.downloader.file_has_video_stream",
                side_effect=lambda path: path == merged_video,
            ):
                resolved = downloader._resolve_downloaded_video_path(audio_only)

            self.assertEqual(resolved, merged_video)


if __name__ == "__main__":
    unittest.main()

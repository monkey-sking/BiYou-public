import unittest

from core.utils.url_utils import (
    is_supported_video_url,
    parse_youtube_video_id,
    split_supported_video_urls,
)


class TestUrlValidation(unittest.TestCase):
    def test_parse_watch_url(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        self.assertEqual(parse_youtube_video_id(url), "dQw4w9WgXcQ")

    def test_parse_shorts_url(self):
        url = "https://youtube.com/shorts/dQw4w9WgXcQ?feature=share"
        self.assertEqual(parse_youtube_video_id(url), "dQw4w9WgXcQ")

    def test_reject_non_video_youtube_page(self):
        self.assertFalse(is_supported_video_url("https://www.youtube.com/@OpenAI"))
        self.assertFalse(is_supported_video_url("https://www.youtube.com/playlist?list=PL123"))

    def test_reject_generic_web_page(self):
        self.assertFalse(is_supported_video_url("https://example.com/article"))

    def test_split_valid_and_invalid_urls(self):
        valid_urls, invalid_urls = split_supported_video_urls([
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://example.com/article",
            "https://youtu.be/9bZkp7q19f0",
            "https://www.youtube.com/@OpenAI",
        ])

        self.assertEqual(valid_urls, [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/9bZkp7q19f0",
        ])
        self.assertEqual(invalid_urls, [
            "https://example.com/article",
            "https://www.youtube.com/@OpenAI",
        ])


if __name__ == "__main__":
    unittest.main()

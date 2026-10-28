import unittest
from unittest.mock import AsyncMock, patch

from core.modules import browser_downloader


class _FakePage:
    def __init__(self, url="about:blank"):
        self.url = url
        self.close = AsyncMock()
        self._closed = False

    def is_closed(self):
        return self._closed


class _FakeContext:
    def __init__(self, pages=None):
        self.pages = list(pages or [])
        self._new_pages = []

    async def new_page(self):
        page = _FakePage("https://new.example.test")
        self._new_pages.append(page)
        self.pages.append(page)
        return page


class _FakeBrowser:
    def __init__(self, contexts=None):
        self.contexts = list(contexts or [])
        self.close = AsyncMock()


class _FakePlaywright:
    def __init__(self, browser):
        self.chromium = self
        self._browser = browser

    async def connect_over_cdp(self, _cdp_url):
        return self._browser


class TestBrowserDownloader(unittest.TestCase):
    def test_merge_player_stream_data_exposes_progressive_fallback(self):
        result = {}
        player_data = {
            "title": "Example",
            "description": "Desc",
            "thumbnail": "https://example.com/thumb.jpg",
            "id": "abc123",
            "visitor_data": "visitor-token",
            "progressive_formats": [{"url": "https://rr.googlevideo.com/videoplayback?id=1"}],
            "adaptive_formats": [{"itag": 137}],
            "progressive_stream_url": "https://rr.googlevideo.com/videoplayback?id=1",
        }

        found_progressive = browser_downloader._merge_player_stream_data(result, player_data)

        self.assertTrue(found_progressive)
        self.assertEqual(result["title"], "Example")
        self.assertEqual(result["progressive_stream_url"], player_data["progressive_stream_url"])
        self.assertEqual(result["visitor_data"], "visitor-token")


class TestBrowserDownloaderAsync(unittest.IsolatedAsyncioTestCase):
    async def test_get_browser_page_reuse_does_not_close_shared_browser_or_existing_page(self):
        existing_page = _FakePage("https://www.youtube.com/watch?v=abc")
        context = _FakeContext([existing_page])
        browser = _FakeBrowser([context])
        playwright = _FakePlaywright(browser)

        with patch.object(browser_downloader, "_get_browser_launch_config", return_value=("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/tmp/by-profile")), \
             patch.object(browser_downloader, "_load_profile_cookies", return_value=[]), \
             patch.object(browser_downloader, "prepare_browser_launch", return_value={"action": "reuse", "debug_port": 39222, "port_changed": False}), \
             patch.object(browser_downloader, "update_config"), \
             patch.object(browser_downloader.asyncio, "sleep", new=AsyncMock()):
            session = await browser_downloader._get_browser_page(playwright)

        self.assertIs(session.page, existing_page)
        self.assertIs(session.context, context)
        await session.close()
        existing_page.close.assert_not_awaited()
        browser.close.assert_not_awaited()

    async def test_get_browser_page_closes_task_opened_page_and_launched_browser(self):
        context = _FakeContext([])
        browser = _FakeBrowser([context])
        playwright = _FakePlaywright(browser)

        with patch.object(browser_downloader, "_get_browser_launch_config", return_value=("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/tmp/by-profile")), \
             patch.object(browser_downloader, "_load_profile_cookies", return_value=[]), \
             patch.object(browser_downloader, "prepare_browser_launch", return_value={"action": "launch", "debug_port": 39222, "port_changed": False}), \
             patch.object(browser_downloader, "update_config"), \
             patch.object(browser_downloader.asyncio, "sleep", new=AsyncMock()), \
             patch.object(browser_downloader.subprocess, "Popen"):
            session = await browser_downloader._get_browser_page(playwright)

        await session.close()
        session.page.close.assert_awaited_once()
        browser.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import AsyncMock

from core.publishing.adapters.kuaishou_sau import KuaishouSAUAdapter


class _FakeLocator:
    def __init__(self, count=0):
        self._count = count
        self.click = AsyncMock()

    async def count(self):
        return self._count

    @property
    def first(self):
        return self


class _FakePage:
    def __init__(self, *, text_locators=None, role_locators=None, css_locators=None):
        self._text_locators = text_locators or {}
        self._role_locators = role_locators or {}
        self._css_locators = css_locators or {}
        self.wait_for_timeout = AsyncMock()
        self.goto = AsyncMock()
        self.close = AsyncMock()
        self.url = "https://cp.kuaishou.com/article/publish/video"

    def get_by_text(self, text, exact=False):
        return self._text_locators.get((text, exact), _FakeLocator())

    def get_by_role(self, role, name=None):
        return self._role_locators.get((role, name), _FakeLocator())

    def locator(self, selector):
        return self._css_locators.get(selector, _FakeLocator())


class TestKuaishouSAUAdapter(unittest.TestCase):
    def _build_adapter(self):
        return KuaishouSAUAdapter(config={}, selectors={})

    def test_dismiss_draft_clicks_abandon_when_continue_editing_is_visible(self):
        adapter = self._build_adapter()
        abandon = _FakeLocator(1)
        page = _FakePage(
            text_locators={
                ("继续编辑", True): _FakeLocator(1),
            },
            role_locators={
                ("button", "放弃"): abandon,
            },
        )

        result = adapter.run_async(adapter._dismiss_draft_semantic(page))

        self.assertTrue(result)
        self.assertEqual(abandon.click.await_count, 1)
        self.assertEqual(page.wait_for_timeout.await_count, 1)

    def test_click_publish_prefers_explicit_publish_button(self):
        adapter = self._build_adapter()
        publish_button = _FakeLocator(1)
        generic_primary = _FakeLocator(1)
        cover_confirm = _FakeLocator(1)
        page = _FakePage(
            role_locators={
                ("button", "确认"): cover_confirm,
                ("button", "发布"): publish_button,
            },
            css_locators={
                "div[class*='_button-primary']": generic_primary,
            },
        )

        result = adapter.run_async(adapter._click_publish_semantic(page))

        self.assertTrue(result)
        self.assertEqual(cover_confirm.click.await_count, 1)
        self.assertEqual(publish_button.click.await_count, 1)
        self.assertEqual(generic_primary.click.await_count, 0)

    def test_click_publish_falls_back_to_generic_primary_button(self):
        adapter = self._build_adapter()
        generic_primary = _FakeLocator(1)
        page = _FakePage(
            css_locators={
                "div[class*='_button-primary']": generic_primary,
            },
        )

        result = adapter.run_async(adapter._click_publish_semantic(page))

        self.assertTrue(result)
        self.assertEqual(generic_primary.click.await_count, 1)

    def test_verify_publish_result_accepts_current_status_two_manager_page(self):
        adapter = self._build_adapter()
        page = _FakePage()
        page.url = "https://cp.kuaishou.com/article/manage/video?status=2&from=publish"
        adapter._extract_manager_rows = AsyncMock(
            return_value=[
                {
                    "title": "手把手教你实现电影级交互",
                    "text": "手把手教你实现电影级交互 审核中",
                    "platform_content_id": "",
                    "platform_url": "",
                }
            ]
        )
        adapter._match_manager_rows = AsyncMock(
            return_value={
                "verified": True,
                "platform_content_id": "",
                "platform_url": "https://cp.kuaishou.com/article/manage/video",
                "published_at": "",
                "last_error": "",
            }
        )

        result = adapter.run_async(
            adapter._verify_publish_result(
                context=None,
                payload={"title": "手把手教你实现电影级交互"},
                verify_page=page,
            )
        )

        self.assertTrue(result["verified"])
        self.assertEqual(page.goto.await_count, 0)
        self.assertEqual(page.close.await_count, 0)
        adapter._extract_manager_rows.assert_awaited_once_with(page)


if __name__ == "__main__":
    unittest.main()

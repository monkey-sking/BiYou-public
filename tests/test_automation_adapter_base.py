import ast
import asyncio
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

from core.publishing.adapters.automation_base import (
    AutomationAdapterBase,
    build_browser_launch_context,
)


class _FakeLocator:
    def __init__(self, count: int | list[int], *, visible: bool = True, editable: bool = True):
        self._count = count
        self._visible = visible
        self._editable = editable
        self.fill = AsyncMock()
        self.set_input_files = AsyncMock()
        self.click = AsyncMock()
        self.is_visible = AsyncMock(return_value=visible)
        self.is_editable = AsyncMock(return_value=editable)
        self._handle = _FakeElementHandle()
        self.element_handle = AsyncMock(return_value=self._handle)

    async def count(self):
        if isinstance(self._count, list):
            if len(self._count) > 1:
                return self._count.pop(0)
            return self._count[0]
        return self._count

    @property
    def first(self):
        return self

    def nth(self, index):
        return self


class _FakePage:
    def __init__(self, locator_counts: dict[str, int], context=None):
        self.locator_counts = locator_counts
        self.locators = {}
        self.goto = AsyncMock()
        self.close = AsyncMock()
        self.wait_for_timeout = AsyncMock()
        self.context = context

    def locator(self, selector: str):
        if selector not in self.locators:
            self.locators[selector] = _FakeLocator(self.locator_counts.get(selector, 0))
        return self.locators[selector]


class _FakeLocatorGroup:
    def __init__(self, locators):
        self._locators = list(locators)

    async def count(self):
        return len(self._locators)

    def nth(self, index):
        return self._locators[index]


class _FakeContext:
    def __init__(self, page: _FakePage, pages=None):
        self.page = page
        self._pages = list(pages) if pages is not None else [page]
        self.new_page = AsyncMock(return_value=page)
        self.new_cdp_session = AsyncMock()

    @property
    def pages(self):
        return list(self._pages)


class _FakeChromium:
    def __init__(self, browser):
        self.browser = browser
        self.launch_persistent_context = AsyncMock()
        self.connect_over_cdp = AsyncMock(return_value=browser)


class _FakePlaywright:
    def __init__(self, chromium: _FakeChromium):
        self.chromium = chromium
        self.stop = AsyncMock()


class _FakeBrowser:
    def __init__(self, context: _FakeContext):
        self.contexts = [context]
        self.close = AsyncMock()


class _FakeElementHandle:
    def __init__(self):
        self.evaluate = AsyncMock()


class _FakeFrameTarget:
    def __init__(self, locator_counts: dict[str, int], backing_page: _FakePage, evaluate_result=False):
        self.locator_counts = locator_counts
        self.locators = {}
        self.wait_for_timeout = AsyncMock()
        self.evaluate = AsyncMock(return_value=evaluate_result)
        self.page = backing_page

    def locator(self, selector: str):
        if selector not in self.locators:
            self.locators[selector] = _FakeLocator(self.locator_counts.get(selector, 0))
        return self.locators[selector]


class _FakeCDPSession:
    def __init__(self):
        async def _send(method, payload=None):
            if method == "DOM.enable":
                return {}
            if method == "DOM.getFlattenedDocument":
                return {
                    "nodes": [
                        {
                            "nodeId": 7,
                            "attributes": [
                                "data-biyou-cdp-upload-target",
                                "biyou-upload-testmarker",
                            ],
                        }
                    ]
                }
            if method == "DOM.setFileInputFiles":
                return {}
            return {}

        self.send = AsyncMock(side_effect=_send)
        self.detach = AsyncMock()


class TestAutomationAdapterBase(unittest.TestCase):
    def test_file_upload_helpers_are_defined_once(self):
        source = Path(
            "${HOME}/biyou/core/publishing/adapters/automation_base.py"
        ).read_text(encoding="utf-8")
        module = ast.parse(source)
        class_node = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "AutomationAdapterBase"
        )

        helper_names = {
            "_set_file_via_cdp",
            "_set_file_via_selector_cdp",
            "_set_file_first",
        }
        counts = {name: 0 for name in helper_names}
        for item in class_node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name in counts:
                counts[item.name] += 1

        self.assertEqual(
            counts,
            {
                "_set_file_via_cdp": 1,
                "_set_file_via_selector_cdp": 1,
                "_set_file_first": 1,
            },
        )

    def test_build_browser_launch_context_uses_runtime_config(self):
        config = {
            "browser_profile": "~/.biyou-workflow-browser-data",
            "browser_debug_port": 39222,
        }

        context = build_browser_launch_context(config)

        self.assertTrue(context["profile_dir"].endswith(".biyou-workflow-browser-data"))
        self.assertEqual(context["debug_port"], 39222)
        self.assertFalse(context["profile_fallback"])

    def test_base_exposes_beta_automation_runtime_defaults(self):
        adapter = AutomationAdapterBase(config={}, selectors={})

        self.assertEqual(adapter.stability_tier, "beta")
        self.assertEqual(adapter.execution_mode, "automation")
        self.assertEqual(adapter.build_runtime()["debug_port"], 39222)

    def test_helper_methods_use_first_matching_selector(self):
        adapter = AutomationAdapterBase(config={}, selectors={})
        page = _FakePage({"textarea": 0, "input[type='text']": 1})

        asyncio.run(adapter._fill_first(page, ["textarea", "input[type='text']"], "hello"))

        self.assertEqual(page.locators["input[type='text']"].fill.await_count, 1)
        self.assertEqual(page.locators["input[type='text']"].fill.await_args.args, ("hello",))

    def test_fill_first_skips_hidden_or_non_editable_matches(self):
        adapter = AutomationAdapterBase(config={}, selectors={})
        page = _FakePage({})
        page.locators["input:not([type='file'])"] = _FakeLocatorGroup(
            [_FakeLocator(1, visible=False), _FakeLocator(1, visible=True, editable=True)]
        )
        page.locators["textarea"] = _FakeLocatorGroup([_FakeLocator(1, visible=True, editable=True)])

        asyncio.run(
            adapter._fill_first(page, ["input:not([type='file'])", "textarea"], "hello")
        )

        first_hidden = page.locators["input:not([type='file'])"].nth(0)
        second_visible = page.locators["input:not([type='file'])"].nth(1)
        self.assertEqual(first_hidden.fill.await_count, 0)
        self.assertEqual(second_visible.fill.await_args.args, ("hello",))

    def test_fill_first_uses_dom_selector_fallback_when_locator_count_is_zero(self):
        adapter = AutomationAdapterBase(config={}, selectors={})
        page = _FakePage({})
        page.evaluate = AsyncMock(return_value=True)
        adapter._selector_exists_in_dom = AsyncMock(return_value=True)
        adapter._fill_via_selector_dom = AsyncMock(return_value=None)

        asyncio.run(adapter._fill_first(page, ["div.input-editor[contenteditable]"], "hello"))

        adapter._selector_exists_in_dom.assert_awaited_once_with(
            page, "div.input-editor[contenteditable]"
        )
        adapter._fill_via_selector_dom.assert_awaited_once_with(
            page, "div.input-editor[contenteditable]", "hello"
        )

    def test_wait_for_any_selector_returns_first_available_selector(self):
        adapter = AutomationAdapterBase(config={}, selectors={})
        page = _FakePage({"textarea": 0, "input[type='text']": 1})

        found = asyncio.run(
            adapter._wait_for_any_selector(
                page,
                ["textarea", "input[type='text']"],
                timeout_ms=200,
                poll_ms=10,
            )
        )

        self.assertEqual(found, "input[type='text']")
        self.assertEqual(page.wait_for_timeout.await_count, 0)

    def test_wait_for_any_selector_ignores_hidden_matches(self):
        adapter = AutomationAdapterBase(config={}, selectors={})
        page = _FakePage({})
        page.locators["input:not([type='file'])"] = _FakeLocatorGroup([_FakeLocator(1, visible=False)])
        page.locators["textarea"] = _FakeLocatorGroup([_FakeLocator(1, visible=True)])

        found = asyncio.run(
            adapter._wait_for_any_selector(
                page,
                ["input:not([type='file'])", "textarea"],
                timeout_ms=10,
                poll_ms=10,
            )
        )

        self.assertEqual(found, "textarea")

    def test_wait_for_any_selector_uses_visible_later_match(self):
        adapter = AutomationAdapterBase(config={}, selectors={})
        page = _FakePage({})
        page.locators["input:not([type='file'])"] = _FakeLocatorGroup(
            [_FakeLocator(1, visible=False), _FakeLocator(1, visible=True)]
        )

        found = asyncio.run(
            adapter._wait_for_any_selector(
                page,
                ["input:not([type='file'])"],
                timeout_ms=10,
                poll_ms=10,
            )
        )

        self.assertEqual(found, "input:not([type='file'])")

    def test_wait_for_any_selector_returns_dom_match_when_locator_count_is_zero(self):
        adapter = AutomationAdapterBase(config={}, selectors={})
        page = _FakePage({})
        page.evaluate = AsyncMock(return_value=True)
        adapter._selector_exists_in_dom = AsyncMock(return_value=True)

        found = asyncio.run(
            adapter._wait_for_any_selector(
                page,
                ["div.input-editor[contenteditable]"],
                timeout_ms=10,
                poll_ms=10,
            )
        )

        self.assertEqual(found, "div.input-editor[contenteditable]")
        adapter._selector_exists_in_dom.assert_awaited_once_with(
            page, "div.input-editor[contenteditable]"
        )

    def test_wait_for_any_selector_accepts_visible_non_editable_controls(self):
        adapter = AutomationAdapterBase(config={}, selectors={})
        page = _FakePage({})
        page.locators["button:has-text('发布')"] = _FakeLocatorGroup(
            [_FakeLocator(1, visible=True, editable=False)]
        )

        found = asyncio.run(
            adapter._wait_for_any_selector(
                page,
                ["button:has-text('发布')"],
                timeout_ms=10,
                poll_ms=10,
            )
        )

        self.assertEqual(found, "button:has-text('发布')")

    def test_fill_first_skips_checkbox_inputs(self):
        adapter = AutomationAdapterBase(config={}, selectors={})
        page = _FakePage({})
        checkbox = _FakeLocator(1, visible=True, editable=True)
        checkbox.get_attribute = AsyncMock(side_effect=lambda name: "checkbox" if name == "type" else None)
        text_input = _FakeLocator(1, visible=True, editable=True)
        text_input.get_attribute = AsyncMock(side_effect=lambda name: "text" if name == "type" else None)
        page.locators["input"] = _FakeLocatorGroup([checkbox, text_input])

        asyncio.run(adapter._fill_first(page, ["input"], "hello"))

        self.assertEqual(checkbox.fill.await_count, 0)
        self.assertEqual(text_input.fill.await_args.args, ("hello",))

    def test_click_first_waits_for_enabled_target(self):
        adapter = AutomationAdapterBase(config={}, selectors={})
        page = _FakePage({})
        delayed_button = _FakeLocator(1, visible=True, editable=True)
        delayed_button.is_enabled = AsyncMock(side_effect=[False, False, True])
        page.locators["button"] = _FakeLocatorGroup([delayed_button])

        asyncio.run(
            adapter._click_first(
                page,
                ["button"],
                timeout_ms=50,
                poll_ms=10,
            )
        )

        self.assertEqual(page.wait_for_timeout.await_count, 2)
        self.assertEqual(delayed_button.click.await_count, 1)

    def test_click_first_force_clicks_visible_target_with_force(self):
        adapter = AutomationAdapterBase(config={}, selectors={})
        page = _FakePage({})
        button = _FakeLocator(1, visible=True, editable=True)
        page.locators["button"] = _FakeLocatorGroup([button])

        asyncio.run(
            adapter._click_first_force(
                page,
                ["button"],
                timeout_ms=10,
                poll_ms=10,
            )
        )

        self.assertEqual(button.click.await_count, 1)
        self.assertEqual(button.click.await_args.kwargs, {"force": True})

    def test_click_first_uses_dom_selector_fallback_when_locator_count_is_zero(self):
        adapter = AutomationAdapterBase(config={}, selectors={})
        page = _FakePage({})
        page.evaluate = AsyncMock(return_value=True)
        adapter._selector_exists_in_dom = AsyncMock(return_value=True)
        adapter._click_via_selector_dom = AsyncMock(return_value=None)

        asyncio.run(
            adapter._click_first(
                page,
                ["button:has-text('发表')"],
                timeout_ms=10,
                poll_ms=10,
            )
        )

        adapter._selector_exists_in_dom.assert_awaited_once_with(
            page, "button:has-text('发表')"
        )
        adapter._click_via_selector_dom.assert_awaited_once_with(
            page, "button:has-text('发表')"
        )

    def test_apply_option_controls_fills_configured_text_field(self):
        adapter = AutomationAdapterBase(config={}, selectors={})
        page = _FakePage({})
        adapter._fill_first = AsyncMock(return_value=None)

        asyncio.run(
            adapter._apply_option_controls(
                page,
                {"poi_name": "上海徐汇"},
                {
                    "poi_name": {
                        "kind": "fill",
                        "selectors": ["input[placeholder*='地点']"],
                    }
                },
            )
        )

        adapter._fill_first.assert_awaited_once_with(
            page,
            ["input[placeholder*='地点']"],
            "上海徐汇",
        )

    def test_apply_option_controls_clicks_choice_with_activate_selector(self):
        adapter = AutomationAdapterBase(config={}, selectors={})
        page = _FakePage({})
        adapter._click_first = AsyncMock(return_value=None)

        asyncio.run(
            adapter._apply_option_controls(
                page,
                {"visibility": "friends"},
                {
                    "visibility": {
                        "kind": "choice",
                        "activate": ["button:has-text('谁可以看')"],
                        "options": {
                            "friends": ["div:text-is('好友可见')"],
                        },
                    }
                },
            )
        )

        self.assertEqual(
            adapter._click_first.await_args_list,
            [
                call(page, ["button:has-text('谁可以看')"], timeout_ms=15000, poll_ms=250),
                call(page, ["div:text-is('好友可见')"], timeout_ms=15000, poll_ms=250),
            ],
        )

    def test_apply_option_controls_skips_boolean_toggle_when_state_already_matches(self):
        adapter = AutomationAdapterBase(config={}, selectors={})
        page = _FakePage({})
        adapter._selector_matches_any = AsyncMock(return_value=True)
        adapter._click_first = AsyncMock(return_value=None)
        adapter._wait_for_any_selector = AsyncMock(return_value="[aria-checked='false']")

        asyncio.run(
            adapter._apply_option_controls(
                page,
                {"allow_comment": False},
                {
                    "allow_comment": {
                        "kind": "boolean",
                        "selectors": ["[role='switch'][aria-label*='评论']"],
                        "enabled_selectors": ["[aria-checked='true']"],
                        "disabled_selectors": ["[aria-checked='false']"],
                    }
                },
            )
        )

        adapter._selector_matches_any.assert_awaited_once_with(page, ["[aria-checked='false']"])
        adapter._click_first.assert_not_awaited()
        adapter._wait_for_any_selector.assert_not_awaited()

    def test_apply_option_controls_toggles_boolean_when_state_differs(self):
        adapter = AutomationAdapterBase(config={}, selectors={})
        page = _FakePage({})
        adapter._selector_matches_any = AsyncMock(side_effect=[False, True])
        adapter._click_first = AsyncMock(return_value=None)
        adapter._wait_for_any_selector = AsyncMock(return_value="[aria-checked='false']")

        asyncio.run(
            adapter._apply_option_controls(
                page,
                {"allow_comment": False},
                {
                    "allow_comment": {
                        "kind": "boolean",
                        "selectors": ["[role='switch'][aria-label*='评论']"],
                        "enabled_selectors": ["[aria-checked='true']"],
                        "disabled_selectors": ["[aria-checked='false']"],
                    }
                },
            )
        )

        self.assertEqual(
            adapter._selector_matches_any.await_args_list,
            [
                call(page, ["[aria-checked='false']"]),
                call(page, ["[aria-checked='true']"]),
            ],
        )
        adapter._click_first.assert_awaited_once_with(
            page,
            ["[role='switch'][aria-label*='评论']"],
            timeout_ms=15000,
            poll_ms=250,
        )
        adapter._wait_for_any_selector.assert_awaited_once_with(
            page,
            ["[aria-checked='false']"],
            timeout_ms=15000,
            poll_ms=250,
        )

    def test_apply_option_controls_clicks_explicit_action_for_truthy_payload(self):
        adapter = AutomationAdapterBase(config={}, selectors={})
        page = _FakePage({})
        adapter._click_first = AsyncMock(return_value=None)

        asyncio.run(
            adapter._apply_option_controls(
                page,
                {"declare_original": True},
                {
                    "declare_original": {
                        "kind": "click",
                        "selectors": ["button:has-text('声明原创')"],
                    }
                },
            )
        )

        adapter._click_first.assert_awaited_once_with(
            page,
            ["button:has-text('声明原创')"],
            timeout_ms=15000,
            poll_ms=250,
        )

    def test_set_file_first_uses_cdp_fallback_for_large_remote_file_uploads(self):
        adapter = AutomationAdapterBase(config={}, selectors={})
        page = _FakePage({})
        file_locator = _FakeLocator(1, visible=True, editable=True)
        file_locator.set_input_files = AsyncMock(
            side_effect=RuntimeError(
                "Cannot transfer files larger than 50Mb to a browser not co-located with the server"
            )
        )
        page.locators["input[type='file']"] = _FakeLocatorGroup([file_locator])
        adapter._set_file_via_cdp = AsyncMock(return_value=None)

        asyncio.run(
            adapter._set_file_first(
                page,
                ["input[type='file']"],
                "/tmp/video.mp4",
                timeout_ms=10,
                poll_ms=10,
            )
        )

        adapter._set_file_via_cdp.assert_awaited_once_with(page, file_locator, "/tmp/video.mp4")

    def test_set_file_via_cdp_targets_matched_input_and_cleans_up_marker(self):
        adapter = AutomationAdapterBase(config={}, selectors={})
        page = _FakePage({}, context=_FakeContext(None))
        cdp_session = _FakeCDPSession()
        page.context.new_cdp_session = AsyncMock(return_value=cdp_session)
        locator = _FakeLocator(1)

        with patch(
            "core.publishing.adapters.automation_base.uuid.uuid4",
            return_value=SimpleNamespace(hex="testmarker"),
        ):
            asyncio.run(
                adapter._set_file_via_cdp(
                    page,
                    locator,
                    "/tmp/video.mp4",
                )
            )

        handle = locator._handle
        self.assertEqual(handle.evaluate.await_count, 2)
        self.assertEqual(
            cdp_session.send.await_args_list[1].args,
            ("DOM.getFlattenedDocument", {"depth": -1, "pierce": True}),
        )
        self.assertEqual(
            cdp_session.send.await_args_list[2].args,
            ("DOM.setFileInputFiles", {"nodeId": 7, "files": ["/tmp/video.mp4"]}),
        )
        self.assertEqual(cdp_session.detach.await_count, 1)

    def test_set_file_via_cdp_ignores_detach_failure_when_session_is_already_closed(self):
        adapter = AutomationAdapterBase(config={}, selectors={})
        page = _FakePage({}, context=_FakeContext(None))
        cdp_session = _FakeCDPSession()
        cdp_session.detach.side_effect = RuntimeError("No session with given id")
        page.context.new_cdp_session = AsyncMock(return_value=cdp_session)
        locator = _FakeLocator(1)

        with patch(
            "core.publishing.adapters.automation_base.uuid.uuid4",
            return_value=SimpleNamespace(hex="testmarker"),
        ):
            asyncio.run(
                adapter._set_file_via_cdp(
                    page,
                    locator,
                    "/tmp/video.mp4",
                )
            )

        self.assertEqual(cdp_session.detach.await_count, 1)

    def test_open_page_reuses_running_browser_via_cdp(self):
        adapter = AutomationAdapterBase(config={"browser_debug_port": 40000}, selectors={})
        page = _FakePage({})
        context = _FakeContext(page)
        page.context = context
        browser = _FakeBrowser(context)
        chromium = _FakeChromium(browser)
        playwright = _FakePlaywright(chromium)

        factory = type("Factory", (), {"start": AsyncMock(return_value=playwright)})()

        with patch(
            "core.publishing.adapters.automation_base.async_playwright",
            return_value=factory,
        ), patch(
            "core.publishing.adapters.automation_base.resolve_browser_app_info",
            return_value=("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/tmp/by-profile"),
        ), patch(
            "core.publishing.adapters.automation_base.prepare_browser_launch",
            return_value={
                "action": "reuse",
                "debug_port": 40000,
                "port_changed": False,
                "process": {"pid": 1234},
            },
        ):
            returned_playwright, returned_context, returned_page = asyncio.run(
                adapter._open_page("https://example.com/publish")
            )

        self.assertIs(returned_playwright, playwright)
        self.assertIs(returned_page, page)
        self.assertEqual(context.new_page.await_count, 1)
        self.assertEqual(chromium.launch_persistent_context.await_count, 0)
        self.assertEqual(chromium.connect_over_cdp.await_count, 1)
        self.assertEqual(
            chromium.connect_over_cdp.await_args.args,
            ("http://127.0.0.1:40000",),
        )
        asyncio.run(returned_context.close())
        self.assertEqual(page.close.await_count, 1)
        self.assertEqual(browser.close.await_count, 0)
        self.assertEqual(page.goto.await_args.args, ("https://example.com/publish",))

    def test_open_page_reuses_existing_matching_tab_before_opening_a_new_one(self):
        adapter = AutomationAdapterBase(config={"browser_debug_port": 40000}, selectors={})
        new_page = _FakePage({})
        existing_page = _FakePage({})
        existing_page.url = "https://example.com/publish"
        existing_page.context = None
        context = _FakeContext(new_page, pages=[existing_page, new_page])
        new_page.context = context
        existing_page.context = context
        browser = _FakeBrowser(context)
        chromium = _FakeChromium(browser)
        playwright = _FakePlaywright(chromium)

        factory = type("Factory", (), {"start": AsyncMock(return_value=playwright)})()

        with patch(
            "core.publishing.adapters.automation_base.async_playwright",
            return_value=factory,
        ), patch(
            "core.publishing.adapters.automation_base.resolve_browser_app_info",
            return_value=("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/tmp/by-profile"),
        ), patch(
            "core.publishing.adapters.automation_base.prepare_browser_launch",
            return_value={
                "action": "reuse",
                "debug_port": 40000,
                "port_changed": False,
                "process": {"pid": 1234},
            },
        ):
            returned_playwright, returned_context, returned_page = asyncio.run(
                adapter._open_page("https://example.com/publish")
            )

        self.assertIs(returned_playwright, playwright)
        self.assertIs(returned_page, existing_page)
        self.assertEqual(context.new_page.await_count, 0)
        self.assertEqual(existing_page.goto.await_count, 0)
        asyncio.run(returned_context.close())
        self.assertEqual(existing_page.close.await_count, 0)
        self.assertEqual(browser.close.await_count, 0)

    def test_open_page_relaunches_browser_when_reuse_port_is_stale(self):
        adapter = AutomationAdapterBase(config={"browser_debug_port": 40000}, selectors={})
        page = _FakePage({})
        context = _FakeContext(page)
        page.context = context
        browser = _FakeBrowser(context)
        chromium = _FakeChromium(browser)
        chromium.connect_over_cdp = AsyncMock(
            side_effect=[RuntimeError("connect ECONNREFUSED"), browser]
        )
        playwright = _FakePlaywright(chromium)

        factory = type("Factory", (), {"start": AsyncMock(return_value=playwright)})()

        with patch(
            "core.publishing.adapters.automation_base.async_playwright",
            return_value=factory,
        ), patch(
            "core.publishing.adapters.automation_base.resolve_browser_app_info",
            return_value=("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/tmp/by-profile"),
        ), patch(
            "core.publishing.adapters.automation_base.prepare_browser_launch",
            return_value={
                "action": "reuse",
                "debug_port": 40000,
                "port_changed": False,
                "process": {"pid": 1234},
            },
        ), patch.object(adapter, "_launch_browser_process") as launch_browser:
            returned_playwright, returned_context, returned_page = asyncio.run(
                adapter._open_page("https://example.com/publish")
            )

        self.assertIs(returned_playwright, playwright)
        self.assertIs(returned_page, page)
        self.assertEqual(chromium.connect_over_cdp.await_count, 2)
        launch_browser.assert_called_once_with(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/tmp/by-profile",
            40000,
        )
        asyncio.run(returned_context.close())
        self.assertEqual(page.close.await_count, 1)
        self.assertEqual(browser.close.await_count, 0)

    def test_open_page_closes_launched_browser_instance_when_task_started_it(self):
        adapter = AutomationAdapterBase(config={"browser_debug_port": 40000}, selectors={})
        page = _FakePage({})
        context = _FakeContext(page)
        page.context = context
        browser = _FakeBrowser(context)
        chromium = _FakeChromium(browser)
        playwright = _FakePlaywright(chromium)

        factory = type("Factory", (), {"start": AsyncMock(return_value=playwright)})()

        with patch(
            "core.publishing.adapters.automation_base.async_playwright",
            return_value=factory,
        ), patch(
            "core.publishing.adapters.automation_base.resolve_browser_app_info",
            return_value=("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/tmp/by-profile"),
        ), patch(
            "core.publishing.adapters.automation_base.prepare_browser_launch",
            return_value={
                "action": "launch",
                "debug_port": 40000,
                "port_changed": False,
                "process": None,
            },
        ), patch.object(adapter, "_launch_browser_process"):
            returned_playwright, returned_context, returned_page = asyncio.run(
                adapter._open_page("https://example.com/publish")
            )

        self.assertIs(returned_playwright, playwright)
        self.assertIs(returned_page, page)
        asyncio.run(returned_context.close())
        self.assertEqual(page.close.await_count, 1)
        self.assertEqual(browser.close.await_count, 1)

    def test_open_page_restarts_same_profile_before_launch_when_debug_port_missing(self):
        adapter = AutomationAdapterBase(config={"browser_debug_port": 40000}, selectors={})
        page = _FakePage({})
        context = _FakeContext(page)
        page.context = context
        browser = _FakeBrowser(context)
        chromium = _FakeChromium(browser)
        playwright = _FakePlaywright(chromium)

        factory = type("Factory", (), {"start": AsyncMock(return_value=playwright)})()

        with patch(
            "core.publishing.adapters.automation_base.async_playwright",
            return_value=factory,
        ), patch(
            "core.publishing.adapters.automation_base.resolve_browser_app_info",
            return_value=("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/tmp/by-profile"),
        ), patch(
            "core.publishing.adapters.automation_base.prepare_browser_launch",
            return_value={
                "action": "launch",
                "debug_port": 40000,
                "port_changed": False,
                "restart_required": True,
                "process": {"pid": 4321},
            },
        ), patch(
            "core.publishing.adapters.automation_base.terminate_browser_process",
            return_value=True,
        ) as terminate_browser, patch.object(adapter, "_launch_browser_process") as launch_browser:
            returned_playwright, returned_context, returned_page = asyncio.run(
                adapter._open_page("https://example.com/publish")
            )

        self.assertIs(returned_playwright, playwright)
        self.assertIs(returned_page, page)
        terminate_browser.assert_called_once_with({"pid": 4321})
        launch_browser.assert_called_once_with(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/tmp/by-profile",
            40000,
        )
        asyncio.run(returned_context.close())
        self.assertEqual(page.close.await_count, 1)
        self.assertEqual(browser.close.await_count, 1)

    def test_set_file_first_falls_back_to_cdp_for_large_files_over_cdp(self):
        adapter = AutomationAdapterBase(config={}, selectors={})
        page = _FakePage({"input[type='file']": 1})
        context = _FakeContext(page)
        page.context = context
        session = AsyncMock()
        session.send = AsyncMock(
            side_effect=[
                {},
                {
                    "nodes": [
                        {
                            "nodeId": 9,
                            "attributes": [
                                "data-biyou-cdp-upload-target",
                                "biyou-upload-testmarker",
                            ],
                        }
                    ]
                },
                {},
            ]
        )
        session.detach = AsyncMock()
        context.new_cdp_session.return_value = session
        page.locators["input[type='file']"] = _FakeLocator(1)
        page.locators["input[type='file']"].set_input_files.side_effect = RuntimeError(
            "Cannot transfer files larger than 50Mb to a browser not co-located with the server"
        )

        with patch(
            "core.publishing.adapters.automation_base.uuid.uuid4",
            return_value=SimpleNamespace(hex="testmarker"),
        ):
            asyncio.run(
                adapter._set_file_first(
                    page,
                    ["input[type='file']"],
                    "/tmp/video.mp4",
                )
            )

        self.assertEqual(context.new_cdp_session.await_count, 1)
        self.assertEqual(
            session.send.await_args_list[2].args,
            ("DOM.setFileInputFiles", {"nodeId": 9, "files": ["/tmp/video.mp4"]}),
        )

    def test_set_file_first_uses_selector_cdp_fallback_when_frame_locator_count_stays_zero(self):
        adapter = AutomationAdapterBase(config={}, selectors={})
        backing_page = _FakePage({})
        backing_context = _FakeContext(backing_page)
        backing_page.context = backing_context
        frame = _FakeFrameTarget(
            {"input[type='file']": 0},
            backing_page=backing_page,
            evaluate_result=True,
        )
        adapter._set_file_via_selector_cdp = AsyncMock(return_value=None)

        asyncio.run(
            adapter._set_file_first(
                frame,
                ["input[type='file']"],
                "/tmp/video.mp4",
                timeout_ms=10,
                poll_ms=10,
            )
        )

        adapter._set_file_via_selector_cdp.assert_awaited_once_with(
            frame,
            "input[type='file']",
            "/tmp/video.mp4",
        )

    def test_set_file_via_selector_cdp_ignores_detach_failure_when_session_is_already_closed(self):
        adapter = AutomationAdapterBase(config={}, selectors={})
        backing_page = _FakePage({})
        backing_context = _FakeContext(backing_page)
        backing_page.context = backing_context
        session = _FakeCDPSession()
        session.detach.side_effect = RuntimeError("No session with given id")
        backing_context.new_cdp_session = AsyncMock(return_value=session)
        frame = _FakeFrameTarget(
            {"input[type='file']": 0},
            backing_page=backing_page,
            evaluate_result=True,
        )
        frame.evaluate = AsyncMock(side_effect=[True, None])

        with patch(
            "core.publishing.adapters.automation_base.uuid.uuid4",
            return_value=SimpleNamespace(hex="testmarker"),
        ):
            asyncio.run(
                adapter._set_file_via_selector_cdp(
                    frame,
                    "input[type='file']",
                    "/tmp/video.mp4",
                )
            )

        self.assertEqual(session.detach.await_count, 1)

    def test_set_file_first_waits_for_input_to_appear(self):
        adapter = AutomationAdapterBase(config={}, selectors={})
        page = _FakePage({})
        page.locators["input[type='file']"] = _FakeLocator([0, 0, 1])

        asyncio.run(
            adapter._set_file_first(
                page,
                ["input[type='file']"],
                "/tmp/video.mp4",
                timeout_ms=50,
                poll_ms=10,
            )
        )

        self.assertEqual(page.wait_for_timeout.await_count, 2)
        self.assertEqual(
            page.locators["input[type='file']"].set_input_files.await_args.args,
            ("/tmp/video.mp4",),
        )


if __name__ == "__main__":
    unittest.main()

from core.utils.logger import log
import asyncio
import os
import subprocess
import uuid

from core.utils.browser_config import (
    get_browser_runtime_config,
    prepare_browser_launch,
    terminate_browser_process,
)
from core.utils.cookie import resolve_browser_app_info

try:
    from playwright.async_api import async_playwright
except ImportError:  # pragma: no cover - exercised when dependency is absent locally.
    def async_playwright():
        raise RuntimeError("playwright is required for automation adapters")


def build_browser_launch_context(config):
    runtime = get_browser_runtime_config(config=config)
    return {
        "profile_dir": runtime["profile_dir"],
        "debug_port": runtime["debug_port"],
        "profile_fallback": runtime["profile_fallback"],
    }


class _PageScopedContext:
    def __init__(
        self,
        browser,
        context,
        page,
        *,
        close_page_on_exit=True,
        close_browser_on_exit=False,
    ):
        self._browser = browser
        self._context = context
        self._page = page
        self._close_page_on_exit = bool(close_page_on_exit)
        self._close_browser_on_exit = bool(close_browser_on_exit)

    @property
    def browser_context(self):
        return self._context

    @property
    def pages(self):
        return self._context.pages

    async def new_page(self):
        return await self._context.new_page()

    async def close(self):
        preview = os.environ.get("BIYOU_PREVIEW")
        if self._close_page_on_exit and not preview:
            close_page = getattr(self._page, "close", None)
            if callable(close_page):
                maybe_close = close_page()
                if asyncio.iscoroutine(maybe_close):
                    await maybe_close
        if self._close_browser_on_exit and not preview:
            close_browser = getattr(self._browser, "close", None)
            if callable(close_browser):
                maybe_browser_close = close_browser()
                if asyncio.iscoroutine(maybe_browser_close):
                    await maybe_browser_close


class AutomationAdapterBase:
    stability_tier = "beta"
    execution_mode = "automation"

    def __init__(self, config, selectors):
        self.config = config or {}
        self.selectors = selectors or {}

    def build_runtime(self):
        return build_browser_launch_context(self.config)

    def _find_existing_page(self, context, publish_url):
        target_url = (publish_url or "").rstrip("/")
        for page in reversed(getattr(context, "pages", []) or []):
            current_url = getattr(page, "url", "")
            if isinstance(current_url, str) and current_url.rstrip("/") == target_url:
                return page
        return None

    def _resolve_cdp_page(self, target):
        context = getattr(target, "context", None)
        if context is not None:
            return target

        page_attr = getattr(target, "page", None)
        if callable(page_attr):
            return page_attr()
        if page_attr is not None:
            return page_attr

        raise RuntimeError("Unable to resolve backing page for CDP operations")

    def _find_node_id_with_attribute(self, flattened_document, attr_name, attr_value):
        for node in flattened_document.get("nodes", []):
            attrs = node.get("attributes") or []
            if not attrs:
                continue
            pairs = zip(attrs[::2], attrs[1::2])
            for key, value in pairs:
                if key == attr_name and value == attr_value:
                    return node.get("nodeId")
        return None

    async def _iter_matching_locators(self, page, selector):
        locator = page.locator(selector)
        count = await locator.count()
        for index in range(count):
            yield locator.nth(index)

    async def _selector_exists_in_dom(self, target, selector):
        return await target.evaluate(
            """(selector) => {
                const isVisible = (el) => {
                    if (!el) {
                        return false;
                    }
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                };

                const parseSelector = (raw) => {
                    const match = raw.match(/^(.*?):(has-text|text-is)\\((['"])(.*)\\3\\)$/);
                    if (!match) {
                        return { base: raw, mode: 'css', text: '' };
                    }
                    return {
                        base: (match[1] || '').trim() || '*',
                        mode: match[2],
                        text: match[4] || '',
                    };
                };

                const findElement = (raw) => {
                    const parsed = parseSelector(raw);
                    if (parsed.mode === 'css') {
                        return Array.from(document.querySelectorAll(parsed.base)).find((el) => isVisible(el)) || null;
                    }
                    return Array.from(document.querySelectorAll(parsed.base)).find((el) => {
                        if (!isVisible(el)) {
                            return false;
                        }
                        const text = (el.innerText || el.textContent || '').trim();
                        if (parsed.mode === 'text-is') {
                            return text === parsed.text;
                        }
                        return text.includes(parsed.text);
                    }) || null;
                };

                return Boolean(findElement(selector));
            }""",
            selector,
        )

    async def _fill_via_selector_dom(self, target, selector, value):
        filled = await target.evaluate(
            """({ selector, value }) => {
                const isVisible = (el) => {
                    if (!el) {
                        return false;
                    }
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                };

                const parseSelector = (raw) => {
                    const match = raw.match(/^(.*?):(has-text|text-is)\\((['"])(.*)\\3\\)$/);
                    if (!match) {
                        return { base: raw, mode: 'css', text: '' };
                    }
                    return {
                        base: (match[1] || '').trim() || '*',
                        mode: match[2],
                        text: match[4] || '',
                    };
                };

                const findElement = (raw) => {
                    const parsed = parseSelector(raw);
                    if (parsed.mode === 'css') {
                        return Array.from(document.querySelectorAll(parsed.base)).find((el) => isVisible(el)) || null;
                    }
                    return Array.from(document.querySelectorAll(parsed.base)).find((el) => {
                        if (!isVisible(el)) {
                            return false;
                        }
                        const text = (el.innerText || el.textContent || '').trim();
                        if (parsed.mode === 'text-is') {
                            return text === parsed.text;
                        }
                        return text.includes(parsed.text);
                    }) || null;
                };

                const el = findElement(selector);
                if (!el) {
                    return false;
                }
                const tag = (el.tagName || '').toLowerCase();
                const type = (el.getAttribute('type') || '').toLowerCase();
                if (
                    tag === 'input' ||
                    tag === 'textarea' ||
                    el instanceof HTMLInputElement ||
                    el instanceof HTMLTextAreaElement
                ) {
                    if (['checkbox', 'radio', 'file', 'button', 'submit', 'reset', 'range', 'color', 'image', 'hidden'].includes(type)) {
                        return false;
                    }
                    el.focus();
                    el.value = value;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
                if (el.isContentEditable || el.getAttribute('contenteditable') === 'true') {
                    el.focus();
                    el.textContent = value;
                    el.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
                return false;
            }""",
            {"selector": selector, "value": value},
        )
        if not filled:
            raise RuntimeError(f"Unable to fill DOM target from selector: {selector}")

    async def _click_via_selector_dom(self, target, selector):
        clicked = await target.evaluate(
            """(selector) => {
                const isVisible = (el) => {
                    if (!el) {
                        return false;
                    }
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                };

                const parseSelector = (raw) => {
                    const match = raw.match(/^(.*?):(has-text|text-is)\\((['"])(.*)\\3\\)$/);
                    if (!match) {
                        return { base: raw, mode: 'css', text: '' };
                    }
                    return {
                        base: (match[1] || '').trim() || '*',
                        mode: match[2],
                        text: match[4] || '',
                    };
                };

                const findElement = (raw) => {
                    const parsed = parseSelector(raw);
                    if (parsed.mode === 'css') {
                        return Array.from(document.querySelectorAll(parsed.base)).find((el) => isVisible(el)) || null;
                    }
                    return Array.from(document.querySelectorAll(parsed.base)).find((el) => {
                        if (!isVisible(el)) {
                            return false;
                        }
                        const text = (el.innerText || el.textContent || '').trim();
                        if (parsed.mode === 'text-is') {
                            return text === parsed.text;
                        }
                        return text.includes(parsed.text);
                    }) || null;
                };

                const el = findElement(selector);
                if (!el) {
                    return false;
                }
                const disabled = el.disabled || el.getAttribute('aria-disabled') === 'true';
                if (disabled) {
                    return false;
                }
                el.click();
                return true;
            }""",
            selector,
        )
        if not clicked:
            raise RuntimeError(f"Unable to click DOM target from selector: {selector}")

    def _coerce_selector_list(self, selectors):
        if isinstance(selectors, str):
            selectors = [selectors]
        if not isinstance(selectors, (list, tuple)):
            return []
        return [str(selector).strip() for selector in selectors if str(selector).strip()]

    def _render_option_selectors(self, selectors, value):
        rendered = []
        rendered_value = str(value)
        for selector in self._coerce_selector_list(selectors):
            rendered.append(selector.replace("{value}", rendered_value))
        return rendered

    def _normalize_option_value(self, value):
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value).strip().lower()

    def _resolve_option_variant(self, variants, value):
        if not isinstance(variants, dict):
            return None
        normalized_value = self._normalize_option_value(value)
        for candidate, control in variants.items():
            if self._normalize_option_value(candidate) == normalized_value:
                return control
        return None

    def _extract_control_selectors(self, control, *, value):
        if isinstance(control, dict):
            selectors = control.get("selectors", [])
        else:
            selectors = control
        return self._render_option_selectors(selectors, value)

    def _coerce_option_bool(self, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"true", "1", "yes", "on"}:
            return True
        if text in {"false", "0", "no", "off"}:
            return False
        return None

    async def _selector_matches_any(self, target, selectors):
        for selector in self._coerce_selector_list(selectors):
            async for locator in self._iter_matching_locators(target, selector):
                is_visible = getattr(locator, "is_visible", None)
                if callable(is_visible) and not await is_visible():
                    continue
                return True
            if hasattr(target, "evaluate"):
                try:
                    found_via_dom = await self._selector_exists_in_dom(target, selector)
                except Exception:
                    found_via_dom = False
                if found_via_dom:
                    return True
        return False

    async def _click_option_activate(self, target, control, value, timeout_ms, poll_ms):
        activate_selectors = self._render_option_selectors(
            control.get("activate", []),
            value,
        )
        if activate_selectors:
            await self._click_first(
                target,
                activate_selectors,
                timeout_ms=timeout_ms,
                poll_ms=poll_ms,
            )

    async def _click_option_confirm(self, target, control, value, timeout_ms, poll_ms):
        confirm_selectors = self._render_option_selectors(
            control.get("confirm_selectors", []),
            value,
        )
        if confirm_selectors:
            await self._click_first(
                target,
                confirm_selectors,
                timeout_ms=timeout_ms,
                poll_ms=poll_ms,
            )

    async def _apply_option_choice(self, target, field_name, value, control, timeout_ms, poll_ms):
        await self._click_option_activate(target, control, value, timeout_ms, poll_ms)
        option_control = self._resolve_option_variant(
            control.get("options") or control.get("values"),
            value,
        )
        if option_control is None:
            raise RuntimeError(
                f"Unable to resolve option control '{field_name}' for value: {value}"
            )
        if isinstance(option_control, dict):
            await self._click_option_activate(
                target,
                option_control,
                value,
                timeout_ms,
                poll_ms,
            )
        selectors = self._extract_control_selectors(option_control, value=value)
        if not selectors:
            raise RuntimeError(
                f"Option control '{field_name}' has no selectors for value: {value}"
            )
        await self._click_first(
            target,
            selectors,
            timeout_ms=timeout_ms,
            poll_ms=poll_ms,
        )
        if isinstance(option_control, dict):
            await self._click_option_confirm(
                target,
                option_control,
                value,
                timeout_ms,
                poll_ms,
            )
        await self._click_option_confirm(target, control, value, timeout_ms, poll_ms)

    async def _apply_option_boolean(self, target, field_name, value, control, timeout_ms, poll_ms):
        bool_value = self._coerce_option_bool(value)
        if bool_value is None:
            raise RuntimeError(
                f"Option control '{field_name}' requires a boolean value, got: {value}"
            )

        mapped_values = control.get("options") or control.get("values")
        if isinstance(mapped_values, dict) and mapped_values:
            await self._apply_option_choice(
                target,
                field_name,
                bool_value,
                control,
                timeout_ms,
                poll_ms,
            )
            return

        await self._click_option_activate(target, control, bool_value, timeout_ms, poll_ms)
        selectors = self._extract_control_selectors(control, value=bool_value)
        if not selectors:
            raise RuntimeError(f"Option control '{field_name}' is missing toggle selectors")

        enabled_selectors = self._render_option_selectors(
            control.get("enabled_selectors", []),
            bool_value,
        )
        disabled_selectors = self._render_option_selectors(
            control.get("disabled_selectors", []),
            bool_value,
        )
        expected_selectors = enabled_selectors if bool_value else disabled_selectors
        opposite_selectors = disabled_selectors if bool_value else enabled_selectors

        if expected_selectors and await self._selector_matches_any(target, expected_selectors):
            return
        if opposite_selectors:
            await self._selector_matches_any(target, opposite_selectors)

        await self._click_first(
            target,
            selectors,
            timeout_ms=timeout_ms,
            poll_ms=poll_ms,
        )
        if expected_selectors:
            await self._wait_for_any_selector(
                target,
                expected_selectors,
                timeout_ms=timeout_ms,
                poll_ms=poll_ms,
            )
        await self._click_option_confirm(target, control, bool_value, timeout_ms, poll_ms)

    async def _log_verification_context(self, verify_page, platform_name: str, expected_title: str = ""):
        """Log page context when verification fails, to aid debugging."""
        try:
            debug_text = await self._extract_body_text(verify_page)
            title_hint = f" (expected: {expected_title[:30]})" if expected_title else ""
            log.warning(f"⚠️ {platform_name} 验证失败{title_hint}，页面文本片段: {debug_text[:300]}")
        except Exception:
            pass

    async def _apply_create_fallback(self, target, value, fallback, timeout_ms=15000, poll_ms=250):
        """When a fill option (e.g. collection) search finds no match, create it inline."""
        activate_selectors = self._coerce_selector_list(fallback.get("activate", []))
        if activate_selectors:
            await self._click_first(target, activate_selectors, timeout_ms=timeout_ms, poll_ms=poll_ms)
            await target.wait_for_timeout(2000)

        fill_selectors = self._coerce_selector_list(fallback.get("selectors", []))
        if fill_selectors:
            await self._fill_first(target, fill_selectors, str(value))
            await target.wait_for_timeout(500)

        confirm_selectors = self._coerce_selector_list(fallback.get("confirm_selectors", []))
        if confirm_selectors:
            await self._click_first(target, confirm_selectors, timeout_ms=timeout_ms, poll_ms=poll_ms)
            await target.wait_for_timeout(2000)
            log.info(f"✅ 合集 '{value}' 创建成功")

    async def _check_preview_pause(self, page, platform_name=""):
        """If BIYOU_PREVIEW env is set, pause for user inspection before publish."""
        if not os.environ.get("BIYOU_PREVIEW"):
            return True
        label = f" [{platform_name}]" if platform_name else ""
        log.info(f"⏸️  预览模式{label}：表单已填写完毕，请在浏览器中检查。")
        print(f"\n{'='*50}")
        print(f"  ⏸️  预览模式{label}")
        print(f"  请在浏览器中检查填写内容，完成后按 Enter 跳过发布...")
        print(f"{'='*50}")
        try:
            import sys
            if sys.stdin.isatty():
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, input)
            else:
                # Non-interactive: keep browser open for 5 minutes
                log.info(f"⏸️  非交互模式，浏览器保持 5 分钟供检查...")
                await page.wait_for_timeout(300000)
        except EOFError:
            log.info(f"⏸️  非交互模式，浏览器保持 5 分钟供检查...")
            await page.wait_for_timeout(300000)
        log.info(f"⏭️  跳过发布{label}，继续下一个平台...")
        return False

    async def _apply_option_control(
        self,
        target,
        field_name,
        value,
        control,
        timeout_ms=15000,
        poll_ms=250,
    ):
        kind = str(control.get("kind", "fill")).strip().lower()
        if kind == "fill":
            await self._click_option_activate(target, control, value, timeout_ms, poll_ms)
            selectors = self._extract_control_selectors(control, value=value)
            if not selectors:
                raise RuntimeError(f"Option control '{field_name}' is missing fill selectors")
            await self._fill_first(target, selectors, str(value))

            create_fallback = control.get("create_fallback")
            if create_fallback:
                try:
                    await self._click_option_confirm(target, control, value, timeout_ms=5000, poll_ms=poll_ms)
                except RuntimeError:
                    log.info(f"🔍 '{value}' 未找到，尝试创建...")
                    await self._apply_create_fallback(target, value, create_fallback, timeout_ms, poll_ms)
            else:
                await self._click_option_confirm(target, control, value, timeout_ms, poll_ms)
            return

        if kind == "click":
            if value in (None, "", False):
                return
            await self._click_option_activate(target, control, value, timeout_ms, poll_ms)
            selectors = self._extract_control_selectors(control, value=value)
            if not selectors:
                raise RuntimeError(f"Option control '{field_name}' is missing click selectors")
            await self._click_first(
                target,
                selectors,
                timeout_ms=timeout_ms,
                poll_ms=poll_ms,
            )
            await self._click_option_confirm(target, control, value, timeout_ms, poll_ms)
            return

        if kind == "choice":
            await self._apply_option_choice(
                target,
                field_name,
                value,
                control,
                timeout_ms,
                poll_ms,
            )
            return

        if kind == "boolean":
            await self._apply_option_boolean(
                target,
                field_name,
                value,
                control,
                timeout_ms,
                poll_ms,
            )
            return

        raise RuntimeError(f"Unsupported option control kind '{kind}' for field '{field_name}'")

    async def _apply_option_controls(self, target, payload, option_controls, timeout_ms=15000, poll_ms=250):
        if not isinstance(option_controls, dict) or not option_controls:
            return target

        for field_name, control in option_controls.items():
            if not isinstance(control, dict):
                continue
            if field_name not in payload:
                continue
            value = payload.get(field_name)
            if value in (None, "", []):
                continue
            required = control.get("required", True)
            try:
                await self._apply_option_control(
                    target,
                    field_name,
                    value,
                    control,
                    timeout_ms=timeout_ms,
                    poll_ms=poll_ms,
                )
            except (RuntimeError, Exception) as exc:
                if required is False:
                    log.warning(f"⚠️ Optional control '{field_name}' skipped: {exc}")
                else:
                    raise
        return target

    async def _resolve_frame(self, page, frame_name, timeout_ms=10000, poll_ms=250):
        if not frame_name:
            return page

        elapsed = 0
        while elapsed <= timeout_ms:
            frame = page.frame(name=frame_name)
            if frame is not None:
                return frame
            await page.wait_for_timeout(poll_ms)
            elapsed += poll_ms

        raise RuntimeError(f"Unable to locate frame: {frame_name}")

    async def _extract_body_text(self, target):
        return await target.evaluate(
            """() => {
                return (document.body?.innerText || "").trim();
            }"""
        )

    async def _wait_for_text(self, target, expected_terms, timeout_ms=30000, poll_ms=1000):
        terms = [term.strip() for term in expected_terms if isinstance(term, str) and term.strip()]
        if not terms:
            raise RuntimeError("No expected terms provided")

        elapsed = 0
        while elapsed <= timeout_ms:
            body_text = await self._extract_body_text(target)
            if any(term in body_text for term in terms):
                return body_text
            await target.wait_for_timeout(poll_ms)
            elapsed += poll_ms

        return ""

    async def _wait_for_text_to_clear(self, target, blocked_terms, timeout_ms=30000, poll_ms=1000):
        terms = [term.strip() for term in blocked_terms if isinstance(term, str) and term.strip()]
        if not terms:
            return ""

        elapsed = 0
        while elapsed <= timeout_ms:
            body_text = await self._extract_body_text(target)
            if not any(term in body_text for term in terms):
                return body_text
            await target.wait_for_timeout(poll_ms)
            elapsed += poll_ms

        return body_text

    async def _is_fillable_text_locator(self, locator):
        is_visible = getattr(locator, "is_visible", None)
        if callable(is_visible) and not await is_visible():
            return False

        is_editable = getattr(locator, "is_editable", None)
        if callable(is_editable) and not await is_editable():
            return False

        get_attribute = getattr(locator, "get_attribute", None)
        if callable(get_attribute):
            input_type = await get_attribute("type")
            if input_type and input_type.lower() in {
                "checkbox",
                "radio",
                "file",
                "button",
                "submit",
                "reset",
                "range",
                "color",
                "image",
                "hidden",
            }:
                return False

        return True

    async def _is_visible_locator(self, locator):
        is_visible = getattr(locator, "is_visible", None)
        if callable(is_visible):
            return await is_visible()
        return True

    def _launch_browser_process(self, browser_executable, profile_dir, debug_port):
        # 2026-08-08: 浏览器操作审计日志（定位窗口来源）
        try:
            import sys as _sys
            _sys.path.insert(0, os.path.expanduser("~/.hermes/scripts"))
            import browser_activity_log
            browser_activity_log.log_browser(
                "launch_browser", browser_executable,
                f"automation_base._launch_browser_process port={debug_port} profile={profile_dir}",
                extra={"script": "BiYou automation_base.py"})
        except Exception:
            pass
        cmd = [
            browser_executable,
            f"--remote-debugging-port={debug_port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars",
            "--mute-audio",
            "--autoplay-policy=user-gesture-required",
            "--window-size=1280,800",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
        ]
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    async def _open_page(self, publish_url):
        runtime = self.build_runtime()
        browser_key, browser_executable, profile_dir = resolve_browser_app_info(
            browser=self.config.get("browser"),
            user_data_dir=runtime["profile_dir"],
        )
        launch = prepare_browser_launch(
            browser_executable=browser_executable,
            profile_dir=profile_dir,
            preferred_debug_port=runtime["debug_port"],
        )
        cdp_url = f"http://127.0.0.1:{launch['debug_port']}"
        playwright = await async_playwright().start()
        if launch["action"] == "launch":
            # 2026-08-10: 不 Popen 启动浏览器（窗口堆积根因）。直接尝试连接，
            # 失败则抛错走 cookie API 上传路径。
            pass
        try:
            browser = await playwright.chromium.connect_over_cdp(cdp_url)
        except Exception:
            # 2026-08-10: 不再重复 Popen 启动浏览器（窗口堆积根因）。旧逻辑在
            # reuse 连接失败时 terminate + Popen 新起一个 —— 每次上传失败都会
            # 多一个进程。现在失败即抛错，让上层走 cookie API 上传路径。
            await playwright.stop()
            raise RuntimeError(
                f"无法连接 {browser_key} CDP 实例 (port {launch['debug_port']})。"
                f"请确认 ego 浏览器已运行。"
            )

        if not getattr(browser, "contexts", None):
            await playwright.stop()
            raise RuntimeError("Unable to connect to configured browser context")

        context = browser.contexts[0]
        page = self._find_existing_page(context, publish_url)
        close_page_on_exit = False
        close_browser_on_exit = launch["action"] == "launch"
        if page is None:
            page = await context.new_page()
            await page.goto(publish_url)
            close_page_on_exit = True
        return (
            playwright,
            _PageScopedContext(
                browser,
                context,
                page,
                close_page_on_exit=close_page_on_exit,
                close_browser_on_exit=close_browser_on_exit,
            ),
            page,
        )

    async def _fill_first(self, page, selectors, value):
        for selector in selectors:
            async for locator in self._iter_matching_locators(page, selector):
                if not await self._is_fillable_text_locator(locator):
                    continue
                await locator.fill(value)
                return
            if hasattr(page, "evaluate"):
                try:
                    found_via_dom = await self._selector_exists_in_dom(page, selector)
                except Exception:
                    found_via_dom = False
                if found_via_dom:
                    try:
                        await self._fill_via_selector_dom(page, selector, value)
                        return
                    except RuntimeError:
                        continue
        raise RuntimeError(f"Unable to locate fill target from selectors: {selectors}")

    async def _wait_for_any_selector(self, page, selectors, timeout_ms=15000, poll_ms=250):
        selectors = [selector for selector in selectors if selector]
        if not selectors:
            raise RuntimeError("No selectors provided")

        elapsed = 0
        while elapsed <= timeout_ms:
            for selector in selectors:
                async for locator in self._iter_matching_locators(page, selector):
                    if not await self._is_visible_locator(locator):
                        continue
                    return selector
                if hasattr(page, "evaluate"):
                    try:
                        found_via_dom = await self._selector_exists_in_dom(page, selector)
                    except Exception:
                        found_via_dom = False
                    if found_via_dom:
                        return selector
            await page.wait_for_timeout(poll_ms)
            elapsed += poll_ms

        raise RuntimeError(f"Unable to locate any selector from: {selectors}")

    async def _set_file_via_cdp(self, target, locator, file_path):
        cdp_page = self._resolve_cdp_page(target)
        context = cdp_page.context
        cdp_session = await context.new_cdp_session(cdp_page)
        marker_attribute = "data-biyou-cdp-upload-target"
        marker_value = f"biyou-upload-{uuid.uuid4().hex}"
        handle = await locator.element_handle()
        if handle is None:
            raise RuntimeError("Unable to resolve file input handle for CDP upload fallback")
        try:
            await cdp_session.send("DOM.enable")
            await handle.evaluate(
                """(el, marker) => {
                    el.setAttribute(marker.name, marker.value);
                }""",
                {"name": marker_attribute, "value": marker_value},
            )
            flattened = await cdp_session.send(
                "DOM.getFlattenedDocument",
                {
                    "depth": -1,
                    "pierce": True,
                },
            )
            node_id = self._find_node_id_with_attribute(
                flattened,
                marker_attribute,
                marker_value,
            )
            if not node_id:
                raise RuntimeError("Unable to locate CDP file target from matched input")
            await cdp_session.send(
                "DOM.setFileInputFiles",
                {
                    "nodeId": node_id,
                    "files": [file_path],
                },
            )
        finally:
            try:
                await handle.evaluate(
                    """(el, markerName) => {
                        el.removeAttribute(markerName);
                    }""",
                    marker_attribute,
                )
            except Exception:
                pass
            detach = getattr(cdp_session, "detach", None)
            if callable(detach):
                try:
                    maybe_detach = detach()
                    if asyncio.iscoroutine(maybe_detach):
                        await maybe_detach
                except Exception:
                    pass

    async def _set_file_via_selector_cdp(self, target, selector, file_path):
        cdp_page = self._resolve_cdp_page(target)
        context = cdp_page.context
        cdp_session = await context.new_cdp_session(cdp_page)
        marker_attribute = "data-biyou-cdp-upload-target"
        marker_value = f"biyou-upload-{uuid.uuid4().hex}"
        try:
            found = await target.evaluate(
                """({ selector, marker }) => {
                    const el = document.querySelector(selector);
                    if (!el) {
                        return false;
                    }
                    el.setAttribute(marker.name, marker.value);
                    return true;
                }""",
                {
                    "selector": selector,
                    "marker": {"name": marker_attribute, "value": marker_value},
                },
            )
            if not found:
                raise RuntimeError(f"Unable to locate DOM file target from selector: {selector}")
            await cdp_session.send("DOM.enable")
            flattened = await cdp_session.send(
                "DOM.getFlattenedDocument",
                {
                    "depth": -1,
                    "pierce": True,
                },
            )
            node_id = self._find_node_id_with_attribute(
                flattened,
                marker_attribute,
                marker_value,
            )
            if not node_id:
                raise RuntimeError("Unable to locate CDP file target from selector marker")
            await cdp_session.send(
                "DOM.setFileInputFiles",
                {
                    "nodeId": node_id,
                    "files": [file_path],
                },
            )
        finally:
            try:
                await target.evaluate(
                    """(markerName) => {
                        const el = document.querySelector(`[${markerName}]`);
                        if (el) {
                            el.removeAttribute(markerName);
                        }
                    }""",
                    marker_attribute,
                )
            except Exception:
                pass
            detach = getattr(cdp_session, "detach", None)
            if callable(detach):
                try:
                    maybe_detach = detach()
                    if asyncio.iscoroutine(maybe_detach):
                        await maybe_detach
                except Exception:
                    pass

    async def _set_file_first(self, page, selectors, file_path, timeout_ms=15000, poll_ms=250):
        selectors = [selector for selector in selectors if selector]
        elapsed = 0
        while elapsed <= timeout_ms:
            for selector in selectors:
                async for locator in self._iter_matching_locators(page, selector):
                    try:
                        await locator.set_input_files(file_path)
                        return
                    except Exception as exc:
                        if "Cannot transfer files larger than 50Mb" not in str(exc):
                            raise
                        await self._set_file_via_cdp(page, locator, file_path)
                        return
                if hasattr(page, "evaluate"):
                    try:
                        found_via_dom = await self._selector_exists_in_dom(page, selector)
                    except Exception:
                        found_via_dom = False
                    if found_via_dom:
                        await self._set_file_via_selector_cdp(page, selector, file_path)
                        return
            await page.wait_for_timeout(poll_ms)
            elapsed += poll_ms
        raise RuntimeError(f"Unable to locate file target from selectors: {selectors}")

    async def _apply_cover_robust(self, target, locators, cover_path):
        if not cover_path or not os.path.exists(cover_path):
            log.warning(f"⚠️ Cover path invalid or missing: {cover_path}")
            return False

        try:
            # 1. 触发封面激活 (打开上传框)
            activate_selectors = locators.get("cover_activate", [])
            if activate_selectors:
                await self._click_first(target, activate_selectors, timeout_ms=5000)
                await target.wait_for_timeout(2000)

            # 2. 上传文件
            upload_selectors = locators.get("cover_upload", [])
            await self._set_file_first(target, upload_selectors, cover_path)
            log.info(f"⏳ 封面文件已挂载，正在等待平台预览渲染: {os.path.basename(cover_path)}")
            await target.wait_for_timeout(6000) # 强制等待，极其重要

            # 3. 核心：暴力 JS 点击确认按钮
            # 我们不信任 selector，直接按文本点！
            log.info("🔘 正在通过 JS 强行点击确认按钮...")
            patterns = ["确定", "确认", "完成", "Confirm", "OK"]
            clicked = await self._js_click_text(target, patterns)

            if not clicked:
                # 如果没点到，最后尝试一次常规 selector
                confirm_selectors = locators.get("cover_confirm", [])
                if confirm_selectors:
                    try:
                        await self._click_first(target, confirm_selectors, timeout_ms=3000)
                        clicked = True
                    except Exception:
                        pass

            if clicked:
                log.info("✅ 封面确认成功")
                await target.wait_for_timeout(2000)
                return True
            else:
                log.warning("⚠️ 未能找到确认按钮，封面可能未挂载成功")
                return False

        except Exception as exc:
            log.error(f"❌ Robust cover apply failed: {exc}")
            return False

    async def _set_file_via_chooser(self, target, click_selectors, file_path, timeout_ms=30000):
        """
        Most robust way: Click a button and wait for the OS file chooser to appear.
        """
        if not file_path:
            return

        try:
            async with target.expect_file_chooser(timeout=timeout_ms) as fc_info:
                await self._click_first(target, click_selectors, timeout_ms=timeout_ms)
            file_chooser = await fc_info.value
            await file_chooser.set_files(file_path)
            log.info(f"✅ File set via chooser: {os.path.basename(file_path)}")
        except Exception as exc:
            log.warning(f"⚠️ File chooser approach failed: {exc}. Falling back to input search.")
            # Fallback to standard input setting
            await self._set_file_first(target, ["input[type='file']"], file_path)

    async def _js_click_text(self, target, text_patterns):
        """
        Force click an element containing specific text using browser-side JS.
        Useful for buttons in complex modals/popups.
        """
        if isinstance(text_patterns, str):
            text_patterns = [text_patterns]

        success = await target.evaluate(
            """(patterns) => {
                const elements = Array.from(document.querySelectorAll('button, div, span, a, li'));
                for (const pattern of patterns) {
                    const found = elements.find(el =>
                        el.innerText.includes(pattern) &&
                        el.offsetParent !== null // Visible
                    );
                    if (found) {
                        found.click();
                        return true;
                    }
                }
                return false;
            }""",
            text_patterns
        )
        return success

    async def _click_first(self, page, selectors, timeout_ms=30000, poll_ms=250):
        selectors = [selector for selector in selectors if selector]
        elapsed = 0
        while elapsed <= timeout_ms:
            for selector in selectors:
                async for locator in self._iter_matching_locators(page, selector):
                    is_visible = getattr(locator, "is_visible", None)
                    if callable(is_visible) and not await is_visible():
                        continue
                    is_enabled = getattr(locator, "is_enabled", None)
                    if callable(is_enabled) and not await is_enabled():
                        continue
                    await locator.click()
                    return
                if hasattr(page, "evaluate"):
                    try:
                        found_via_dom = await self._selector_exists_in_dom(page, selector)
                    except Exception:
                        found_via_dom = False
                    if found_via_dom:
                        try:
                            await self._click_via_selector_dom(page, selector)
                            return
                        except RuntimeError:
                            continue
            await page.wait_for_timeout(poll_ms)
            elapsed += poll_ms
        raise RuntimeError(f"Unable to locate clickable target from selectors: {selectors}")

    async def _click_first_force(self, page, selectors, timeout_ms=30000, poll_ms=250):
        selectors = [selector for selector in selectors if selector]
        elapsed = 0
        while elapsed <= timeout_ms:
            for selector in selectors:
                async for locator in self._iter_matching_locators(page, selector):
                    is_visible = getattr(locator, "is_visible", None)
                    if callable(is_visible) and not await is_visible():
                        continue
                    is_enabled = getattr(locator, "is_enabled", None)
                    if callable(is_enabled) and not await is_enabled():
                        continue
                    await locator.click(force=True)
                    return
                if hasattr(page, "evaluate"):
                    try:
                        found_via_dom = await self._selector_exists_in_dom(page, selector)
                    except Exception:
                        found_via_dom = False
                    if found_via_dom:
                        try:
                            await self._click_via_selector_dom(page, selector)
                            return
                        except RuntimeError:
                            continue
            await page.wait_for_timeout(poll_ms)
            elapsed += poll_ms
        raise RuntimeError(f"Unable to locate clickable target from selectors: {selectors}")

    def run_async(self, coro):
        return asyncio.run(coro)

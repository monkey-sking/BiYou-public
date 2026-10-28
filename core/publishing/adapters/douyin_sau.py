"""
Douyin (抖音) publishing adapter using semantic Playwright locators
inspired by social-auto-upload (https://github.com/dreammis/social-auto-upload, ~10k⭐).

Instead of YAML-configured CSS selectors (which break when websites update),
this adapter uses Playwright's native locator API (get_by_text, get_by_role,
xpath following-sibling, etc.) which is more resilient to DOM changes.

Reuses BiYou's browser management (CDP connection to existing Chrome profile).
"""
import asyncio
import os
import re

from core.publishing.adapters.automation_base import AutomationAdapterBase
from core.publishing.adapters.base import BasePublishingAdapter
from core.publishing.adapters.douyin import (
    DOUYIN_TITLE_LIMIT,
    DOUYIN_DESCRIPTION_LIMIT,
    build_douyin_duration_limit_error,
    build_platform_url,
    build_verify_terms,
    detect_manual_block_error,
    extract_platform_content_id,
    normalize_douyin_publish_payload,
)
from core.publishing.models import AdapterCapabilities, PublishJob
from core.utils.logger import log

# ── Constants ──────────────────────────────────────────────────────────────────
DOUYIN_UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"
DOUYIN_MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage"
DOUYIN_MANAGE_URL_PATTERN = re.compile(r"creator\.douyin\.com/creator-micro/content")

POST_UPLOAD_FORM_TIMEOUT_MS = 600_000  # 10 min
POST_UPLOAD_FORM_POLL_MS = 1_000
VERIFY_TIMEOUT_MS = 180_000  # 3 min
VERIFY_POLL_MS = 5_000

UPLOAD_BLOCKING_TERMS = ["上传中", "处理中"]
SMS_VERIFICATION_TERMS = ["短信验证码", "选择其他验证方式", "获取验证码", "接收短信验证码"]
MANUAL_BLOCKING_TERMS = [
    "安全验证", "身份验证", "扫码登录", "登录失效", "验证后继续", "验证账号",
]
MANAGER_SUCCESS_TERMS = ("审核中", "已发布")


# ── Adapter ────────────────────────────────────────────────────────────────────
class DouyinSAUAdapter(AutomationAdapterBase, BasePublishingAdapter):
    """
    Douyin adapter using semantic Playwright locators (SAU-style).

    Key differences from DouyinAutomationAdapter:
    - Uses get_by_text(), get_by_role(), xpath ancestor/following-sibling instead of
      fragile CSS selectors from YAML config.
    - Uses expect_file_chooser() for robust video upload.
    - Handles Douyin modal interception natively.
    - More resilient to website UI changes.
    """

    platform_name = "douyin"
    stability_tier = "beta"
    execution_mode = "automation"
    capabilities = AdapterCapabilities(
        supports_native_schedule=False,
        supports_cover=True,
        supports_tags=False,
        supports_description=True,
    )

    # ── Validation ────────────────────────────────────────────────────────────
    def validate(self, job: PublishJob) -> None:
        if not job.video_path or not job.cover_path or not job.title:
            raise ValueError("Douyin publish requires video_path, cover_path, and title")

    # ── Semantic Locator Helpers ───────────────────────────────────────────────

    async def _upload_video_semantic(self, page, video_path: str) -> bool:
        """Upload video using file_chooser pattern (SAU's approach)."""
        # Primary: scoped video file input (most precise)
        try:
            upload_input = page.locator("input[type='file'][accept*='video']")
            if await upload_input.count() > 0:
                await upload_input.first.set_input_files(video_path)
                log.info(f"📤 视频已通过video-accept输入上传: {os.path.basename(video_path)}")
                return True
        except Exception:
            pass

        # Fallback: file input inside upload container
        try:
            upload_input = page.locator("div:has-text('上传视频') input[type='file']")
            if await upload_input.count() > 0:
                await upload_input.first.set_input_files(video_path)
                log.info(f"📤 视频已通过上传容器输入上传: {os.path.basename(video_path)}")
                return True
        except Exception:
            pass

        # Fallback: file_chooser triggered by upload button
        try:
            upload_btn = page.locator("button:has-text('上传视频'), div:has-text('上传视频')")
            if await upload_btn.count() > 0:
                async with page.expect_file_chooser(timeout=30_000) as fc_info:
                    await upload_btn.first.click()
                file_chooser = await fc_info.value
                await file_chooser.set_files(video_path)
                log.info(f"📤 视频已通过文件选择器上传: {os.path.basename(video_path)}")
                return True
        except Exception:
            pass

        # Last fallback: CDP file input
        try:
            file_input_selectors = ["input[type='file']"]
            await self._set_file_first(page, file_input_selectors, video_path)
            log.info(f"📤 视频已通过CDP上传: {os.path.basename(video_path)}")
            return True
        except Exception as exc:
            log.error(f"❌ 视频上传失败: {exc}")
            return False

    async def _wait_for_upload_complete(self, page, timeout_ms=600_000, poll_ms=2_000) -> bool:
        """Poll until video upload finishes and form becomes available."""
        elapsed = 0
        while elapsed <= timeout_ms:
            body = await self._extract_body_text(page)

            # Check for upload failure
            if "上传失败" in body:
                log.warning("⚠️ 抖音视频上传失败")
                return False

            # Check for manual block
            block_error = detect_manual_block_error(body)
            if block_error:
                log.warning(f"⚠️ 抖音发布被阻断: {block_error}")
                return False

            # Check if still uploading
            if "上传中" not in body and "处理中" not in body:
                # Also check that the title/description form has appeared
                try:
                    desc_section = page.get_by_text("作品描述", exact=True)
                    title_input = page.locator("input[type='text']")
                    if await desc_section.count() > 0 or await title_input.count() > 0:
                        log.info("✅ 抖音视频上传完成，表单已就绪")
                        return True
                except Exception:
                    pass
                # Even without form, if upload terms are gone, it's likely done
                log.info("✅ 抖音视频上传完成")
                return True

            await page.wait_for_timeout(poll_ms)
            elapsed += poll_ms
        log.error("❌ 抖音视频上传超时")
        return False

    async def _fill_title_and_description_semantic(self, page, title: str, description: str) -> bool:
        """
        Fill title and description using SAU's approach:
        Locate '作品描述' label → navigate to ancestor div → then sibling →
        find title input and description editor inside.
        """
        try:
            # SAU's approach: find "作品描述" and navigate through the DOM tree
            desc_label = page.get_by_text("作品描述", exact=True)
            if await desc_label.count() > 0:
                # Navigate: label → ancestor div[2] → following-sibling div[1]
                desc_section = desc_label.locator("xpath=ancestor::div[2]").locator(
                    "xpath=following-sibling::div[1]"
                )
                if await desc_section.count() > 0:
                    # Title input (first text input in the section)
                    title_input = desc_section.locator("input[type='text']").first
                    if await title_input.count() > 0:
                        await title_input.click()
                        await page.wait_for_timeout(200)
                        await page.keyboard.press("Control+KeyA")
                        await page.keyboard.press("Delete")
                        await title_input.fill(title[:DOUYIN_TITLE_LIMIT])
                        log.info(f"✅ 抖音标题已填写 ({len(title[:DOUYIN_TITLE_LIMIT])} 字)")

                    # Description editor (zone-container contenteditable)
                    desc_editor = desc_section.locator(
                        ".zone-container[contenteditable='true']"
                    ).first
                    if await desc_editor.count() > 0:
                        await desc_editor.click()
                        await page.wait_for_timeout(200)
                        await page.keyboard.press("Control+KeyA")
                        await page.keyboard.press("Delete")
                        await page.keyboard.type(description[:DOUYIN_DESCRIPTION_LIMIT], delay=20)
                        log.info(f"✅ 抖音描述已填写 ({len(description[:DOUYIN_DESCRIPTION_LIMIT])} 字)")
                        return True
        except Exception as exc:
            log.warning(f"⚠️ 抖音SAU方式填写失败: {exc}")

        # Fallback: use YAML-configured selectors
        try:
            locators = self.selectors.get("locators", {})
            title_selectors = locators.get("title", [])
            desc_selectors = locators.get("description", [])
            if title_selectors:
                await self._fill_first(page, title_selectors, title[:DOUYIN_TITLE_LIMIT])
            if desc_selectors:
                await self._fill_first(page, desc_selectors, description[:DOUYIN_DESCRIPTION_LIMIT])
            if title_selectors or desc_selectors:
                log.info("✅ 抖音标题/描述已填写(YAML fallback)")
                return True
        except Exception as exc:
            log.warning(f"⚠️ 抖音YAML方式填写失败: {exc}")

        # Last fallback: try generic inputs
        try:
            # Title: first text input
            title_input = page.locator("input[type='text']").first
            if await title_input.count() > 0:
                await title_input.fill(title[:DOUYIN_TITLE_LIMIT])
                log.info("✅ 抖音标题已填写(generic input)")
            # Description: contenteditable
            desc_editor = page.locator(".zone-container[contenteditable='true']").first
            if await desc_editor.count() > 0:
                await desc_editor.click()
                await page.keyboard.type(description[:DOUYIN_DESCRIPTION_LIMIT], delay=20)
                log.info("✅ 抖音描述已填写(generic editor)")
            return True
        except Exception as exc:
            log.error(f"❌ 抖音标题/描述填写失败: {exc}")
            return False

    async def _set_cover_semantic(self, page, cover_path: str) -> bool:
        """
        Set cover thumbnail — multi-strategy approach:
        1. SAU modal flow: click cover area → upload tab → upload → confirm
        2. Direct hidden file input
        3. YAML-configured selectors fallback
        """
        if not cover_path or not os.path.exists(cover_path):
            log.warning(f"⚠️ 封面文件无效: {cover_path}")
            return False

        # ── Strategy 1: SAU cover modal flow ──────────────────────────────────
        try:
            # Find the cover trigger area
            cover_trigger = page.get_by_text("选择封面", exact=True)
            if await cover_trigger.count() == 0:
                cover_trigger = page.get_by_text("自定义封面", exact=True)
            if await cover_trigger.count() == 0:
                cover_trigger = page.get_by_text("设置封面", exact=True)

            if await cover_trigger.count() > 0:
                await cover_trigger.first.click()
                await page.wait_for_timeout(2000)

                # Check for cover modal (Douyin uses dy-creator-content-modal)
                modal = page.locator(
                    'div[id*="creator-content-modal"], '
                    'div.dy-creator-content-modal-wrap, '
                    'div[role="document"]'
                )
                if await modal.count() > 0:
                    # Try clicking "上传图片" tab
                    upload_tab = modal.get_by_text("上传图片", exact=True)
                    if await upload_tab.count() > 0:
                        await upload_tab.click()
                        await page.wait_for_timeout(1000)

                    # Upload file in modal
                    file_input = modal.locator('input[type="file"]')
                    if await file_input.count() > 0:
                        await file_input.first.set_input_files(cover_path)
                        log.info("📤 封面文件已上传到抖音(modal)")

                        # Wait for confirm button to become enabled (instead of hardcoded wait)
                        for btn_text in ["确定", "确认", "完成"]:
                            confirm_btn = modal.locator(f'button:has-text("{btn_text}")')
                            try:
                                await confirm_btn.first.wait_for(state="visible", timeout=10_000)
                                await confirm_btn.first.click()
                                log.info(f"✅ 抖音封面确认成功({btn_text})")
                                await page.wait_for_timeout(2000)
                                # Dismiss any lingering modal
                                await self._dismiss_lingering_modal(page)
                                return True
                            except Exception:
                                continue

            # If cover_trigger found but modal didn't work, dismiss stale dialogs
            await self._dismiss_lingering_modal(page)
        except Exception as exc:
            log.warning(f"⚠️ 抖音封面设置(SAU modal)失败: {exc}")
            await self._dismiss_lingering_modal(page)

        # ── Strategy 2: Direct semi-upload input ────────────────────────────────
        try:
            cover_input = page.locator(
                "input[class*='semi-upload'][type='file'], "
                "input.semi-upload-hidden-input[type='file']"
            )
            if await cover_input.count() > 0:
                await cover_input.first.set_input_files(cover_path)
                log.info("📤 封面文件已通过semi-upload输入上传")
                # Wait for confirm button to appear (instead of hardcoded wait)
                for btn_text in ["确定", "确认", "完成"]:
                    confirm_btn = page.locator(f'button:has-text("{btn_text}")')
                    try:
                        await confirm_btn.first.wait_for(state="visible", timeout=10_000)
                        await confirm_btn.first.click()
                        log.info(f"✅ 抖音封面确认成功(semi-upload, {btn_text})")
                        await page.wait_for_timeout(2000)
                        return True
                    except Exception:
                        continue
        except Exception as exc:
            log.warning(f"⚠️ 抖音封面设置(semi-upload)失败: {exc}")

        # ── Strategy 3: YAML-configured selectors fallback ─────────────────────
        try:
            locators = self.selectors.get("locators", {})
            result = await self._apply_cover_robust(page, locators, cover_path)
            if result:
                log.info("✅ 抖音封面设置成功(YAML fallback)")
                return True
        except Exception as exc:
            log.warning(f"⚠️ 抖音封面设置(YAML fallback)失败: {exc}")

        log.error("❌ 抖音封面设置失败: 所有策略均未成功")
        return False

    async def _dismiss_lingering_modal(self, page) -> None:
        """Dismiss any lingering Douyin creator modal that blocks interactions."""
        try:
            # Try clicking close buttons in Douyin-specific modals
            close_btn = page.locator(
                "div.dy-creator-content-modal-wrap button:has-text('关闭'), "
                "div.dy-creator-content-modal-wrap [class*='close'], "
                "div.semi-modal-wrap [class*='close'], "
                ".semi-modal-close"
            )
            if await close_btn.count() > 0:
                await close_btn.first.click(force=True)
                await page.wait_for_timeout(500)
                return
        except Exception:
            pass
        # Fallback: press Escape
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)
        except Exception:
            pass

    async def _click_publish_semantic(self, page) -> bool:
        """Click publish button — primary: class-scoped selector, then text match."""
        # Primary: class-based selector (most precise — from DOM inspection)
        try:
            btn = page.locator("button.button-dhlUZE.primary-cECiOJ")
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_timeout(3000)
                await self._handle_publish_intercept_modal(page)
                log.info("✅ 抖音发布按钮已点击(class)")
                return True
        except Exception:
            pass

        # Fallback: role-based selector scoped within form area
        try:
            # Scope to form container first, then find publish button
            form_area = page.locator("div[class*='publish-content'], div[class*='creator-content']")
            publish_btn = form_area.get_by_role("button", name="发布", exact=True)
            if await publish_btn.count() > 0:
                await publish_btn.first.click()
                await page.wait_for_timeout(3000)
                await self._handle_publish_intercept_modal(page)
                log.info("✅ 抖音发布按钮已点击(role-scoped)")
                return True
        except Exception:
            pass

        # Last fallback: any button with 发布 text (least precise)
        try:
            btn = page.locator("button:has-text('发布')")
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_timeout(3000)
                await self._handle_publish_intercept_modal(page)
                log.info("✅ 抖音发布按钮已点击(text)")
                return True
        except Exception:
            pass

        # Last fallback: YAML selectors
        locators = self.selectors.get("locators", {})
        publish_selectors = locators.get("publish_button", [])
        if publish_selectors:
            try:
                await self._click_first(page, publish_selectors)
                await page.wait_for_timeout(3000)
                await self._handle_publish_intercept_modal(page)
                log.info("✅ 抖音发布按钮已点击(YAML)")
                return True
            except Exception:
                pass

        return False

    async def _handle_publish_intercept_modal(self, page) -> None:
        """Handle Douyin's publish interception modal that blocks the publish click."""
        try:
            # Douyin may show a semi-modal that intercepts pointer events
            intercept_modal = page.locator(
                "div.dy-creator-content-modal-wrap, "
                "div.dy-creator-content-portal, "
                "div[role='modal']"
            )
            if await intercept_modal.count() > 0:
                # Try clicking confirm/continue buttons in the modal
                for btn_text in ["继续发布", "确认发布", "继续", "确定", "知道了", "我知道了"]:
                    confirm = intercept_modal.locator(f'button:has-text("{btn_text}")')
                    if await confirm.count() > 0:
                        await confirm.first.click()
                        await page.wait_for_timeout(1000)
                        log.info(f"✅ 已处理抖音发布拦截弹窗({btn_text})")
                        return

                # Try closing the modal
                close_btn = intercept_modal.locator(
                    "button:has-text('取消'), [class*='close']"
                )
                if await close_btn.count() > 0:
                    await close_btn.first.click()
                    await page.wait_for_timeout(1000)
        except Exception:
            pass

    # ── Verification ───────────────────────────────────────────────────────────

    async def _verify_publish_result(self, context, payload: dict) -> dict:
        """Verify publish result by checking the Douyin creator center manage page."""
        from core.publishing.adapters.douyin import (
            extract_work_list_record,
            format_douyin_published_at,
        )

        verify_page = await context.new_page()
        result_verified = False
        try:
            await verify_page.goto(DOUYIN_MANAGE_URL, wait_until="domcontentloaded")
            initial_body = await self._extract_body_text(verify_page)
            block_error = detect_manual_block_error(initial_body)
            if block_error:
                return {
                    "verified": False,
                    "manual_required": True,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": block_error,
                }

            # Try to extract from work_list API response
            try:
                async with verify_page.expect_response(
                    lambda response: "/work_list" in response.url
                ) as response_info:
                    await verify_page.goto(DOUYIN_MANAGE_URL, wait_until="domcontentloaded")
                response = await response_info.value
                work_list_record = extract_work_list_record(
                    await response.json(), payload["title"]
                )
                if work_list_record.get("platform_content_id"):
                    result_verified = True
                    return {
                        "verified": True,
                        "platform_content_id": work_list_record.get("platform_content_id", ""),
                        "platform_url": work_list_record.get("platform_url", ""),
                        "published_at": work_list_record.get("published_at", ""),
                        "last_error": "",
                    }
            except Exception:
                pass

            # Text-based verification
            terms = build_verify_terms(payload["title"])
            body_text = await self._wait_for_text(
                verify_page, terms,
                timeout_ms=VERIFY_TIMEOUT_MS, poll_ms=VERIFY_POLL_MS,
            )
            block_error = detect_manual_block_error(body_text or "")
            if block_error:
                return {
                    "verified": False,
                    "manual_required": True,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": block_error,
                }

            if body_text and any(term in body_text for term in MANAGER_SUCCESS_TERMS):
                result_verified = True
                return {
                    "verified": True,
                    "platform_content_id": "",
                    "platform_url": DOUYIN_MANAGE_URL,
                    "published_at": "",
                    "last_error": "",
                }

            return {
                "verified": False,
                "status": "failed_retryable",
                "platform_content_id": "",
                "platform_url": "",
                "published_at": "",
                "last_error": "Douyin publish result could not be verified from creator center",
            }
        finally:
            if not result_verified:
                try:
                    debug_text = await self._extract_body_text(verify_page)
                    log.warning(f"⚠️ 抖音验证失败，页面文本片段: {debug_text[:300]}")
                except Exception:
                    pass
            await verify_page.close()

    # ── Main Publish Flow ──────────────────────────────────────────────────────

    async def _publish_async(self, payload: dict) -> dict:
        playwright = context = None
        try:
            playwright, context, page = await self._open_page(DOUYIN_UPLOAD_URL)
            await page.wait_for_timeout(5000)

            # 1. Upload video
            video_path = payload["video_path"]
            log.info(f"📤 正在上传抖音视频: {os.path.basename(video_path)}")
            if not await self._upload_video_semantic(page, video_path):
                return {
                    "verified": False,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": "Douyin video upload failed",
                }

            # 2. Wait for upload to complete and form to appear
            if not await self._wait_for_upload_complete(
                page, timeout_ms=POST_UPLOAD_FORM_TIMEOUT_MS
            ):
                body_text = await self._extract_body_text(page)
                block_error = detect_manual_block_error(body_text)
                if block_error:
                    return {
                        "verified": False,
                        "manual_required": True,
                        "platform_content_id": "",
                        "platform_url": "",
                        "published_at": "",
                        "last_error": block_error,
                    }
                return {
                    "verified": False,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": "Douyin video upload timed out or form did not appear",
                }

            # 3. Fill title and description
            title = payload.get("title", "")
            description = payload.get("description", "")
            if not await self._fill_title_and_description_semantic(page, title, description):
                return {
                    "verified": False,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": "Douyin title/description fill failed",
                }

            # 4. Set cover
            cover_path = payload.get("cover_path", "")
            if cover_path:
                await self._set_cover_semantic(page, cover_path)

            # 5. Apply option controls from YAML config (for collection, visibility etc.)
            option_controls = self.selectors.get("option_controls", {})
            if option_controls:
                try:
                    await self._apply_option_controls(page, payload, option_controls)
                except Exception as exc:
                    log.warning(f"⚠️ 抖音选项控制失败: {exc}")

            # 6. Wait for any upload processing to complete
            body_text = await self._wait_for_text_to_clear(
                page,
                UPLOAD_BLOCKING_TERMS,
                timeout_ms=POST_UPLOAD_FORM_TIMEOUT_MS,
                poll_ms=POST_UPLOAD_FORM_POLL_MS,
            )
            if any(term in body_text for term in UPLOAD_BLOCKING_TERMS):
                return {
                    "verified": False,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": "Douyin publish is still processing uploaded media",
                }

            # 7. Check for manual blocking before publish
            publish_body = await self._extract_body_text(page)
            block_error = detect_manual_block_error(publish_body)
            if block_error:
                return {
                    "verified": False,
                    "manual_required": True,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": block_error,
                }

            # 8. Preview pause (if BIYOU_PREVIEW is set)
            if not await self._check_preview_pause(page, "douyin"):
                return {
                    "verified": False,
                    "status": "preview",
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": "preview mode — publish skipped",
                }

            # 9. Click publish
            if not await self._click_publish_semantic(page):
                return {
                    "verified": False,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": "Douyin publish button not found or not clickable",
                }

            # 10. Post-publish check for blocking
            await page.wait_for_timeout(5000)
            post_body = await self._extract_body_text(page)
            block_error = detect_manual_block_error(post_body)
            if block_error:
                return {
                    "verified": False,
                    "manual_required": True,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": block_error,
                }

            # 11. Verify
            return await self._verify_publish_result(context.browser_context, payload)

        finally:
            if context is not None:
                close_context = getattr(context, "close", None)
                if callable(close_context):
                    maybe_context_close = close_context()
                    if asyncio.iscoroutine(maybe_context_close):
                        await maybe_context_close
            if playwright is not None:
                await playwright.stop()

    # ── Sync Entry Point ──────────────────────────────────────────────────────

    def publish(self, job: PublishJob) -> dict:
        self.validate(job)
        payload = normalize_douyin_publish_payload(job)

        duration_error = build_douyin_duration_limit_error(job)
        if duration_error:
            return {
                "platform": self.platform_name,
                "status": "failed_manual",
                "platform_content_id": "",
                "platform_url": "",
                "native_schedule": False,
                "scheduled_by": "biyou",
                "scheduled_for": "",
                "published_at": "",
                "last_error": duration_error,
                "extra": payload,
            }

        outcome = self.run_async(self._publish_async(payload))
        platform_content_id = str(outcome.get("platform_content_id", "")).strip()
        platform_url = build_platform_url(
            platform_content_id,
            outcome.get("platform_url", ""),
        )
        last_error = outcome.get("last_error", "")
        manual_required = bool(outcome.get("manual_required"))
        verified = bool(outcome.get("verified") or platform_content_id)

        if verified and not platform_content_id:
            # Row-verified without content ID is a weak signal
            row_verified = bool(platform_url and platform_url != DOUYIN_MANAGE_URL)
            if not row_verified:
                verified = False
                last_error = last_error or "Douyin publish result could not be verified from creator center"

        if not verified and not last_error:
            last_error = "Douyin publish result could not be verified from creator center"

        status = str(outcome.get("status", "") or "").strip()
        if not verified:
            if manual_required or status == "failed_manual":
                status = "failed_manual"
            else:
                status = status or "failed_retryable"
        else:
            status = "published" if not last_error else (status or "failed_manual")

        return {
            "platform": self.platform_name,
            "status": status,
            "platform_content_id": platform_content_id,
            "platform_url": platform_url,
            "native_schedule": False,
            "scheduled_by": "biyou",
            "scheduled_for": "",
            "published_at": outcome.get("published_at", ""),
            "last_error": last_error,
            "extra": payload,
        }

    def schedule(self, job: PublishJob, publish_at: str) -> dict:
        self.validate(job)
        payload = normalize_douyin_publish_payload(job)
        duration_error = build_douyin_duration_limit_error(job)
        if duration_error:
            return {
                "platform": self.platform_name,
                "status": "failed_manual",
                "platform_content_id": "",
                "platform_url": "",
                "native_schedule": False,
                "scheduled_by": "biyou",
                "scheduled_for": publish_at,
                "published_at": "",
                "last_error": duration_error,
                "extra": payload,
            }
        return {
            "platform": self.platform_name,
            "status": "scheduled",
            "platform_content_id": "",
            "platform_url": "",
            "native_schedule": False,
            "scheduled_by": "biyou",
            "scheduled_for": publish_at,
            "published_at": "",
            "last_error": "",
            "extra": payload,
        }

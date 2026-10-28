"""
WeChat Channels (微信视频号) publishing adapter using semantic Playwright locators
inspired by social-auto-upload (https://github.com/dreammis/social-auto-upload, ~10k⭐).

Instead of YAML-configured CSS selectors (which break when websites update),
this adapter uses Playwright's native locator API (get_by_text, get_by_role,
xpath following-sibling, etc.) which is more resilient to DOM changes.

Reuses BiYou's browser management (CDP connection to existing Chrome profile).
"""
import asyncio
import os

from core.publishing.adapters.automation_base import AutomationAdapterBase
from core.publishing.adapters.base import BasePublishingAdapter
from core.publishing.adapters.wechat_channels import (
    WECHAT_SHORT_TITLE_LIMIT,
    WECHAT_SHORT_TITLE_MIN,
    WECHAT_TITLE_VALIDATION_ERRORS,
    WECHAT_MANUAL_PERMISSION_TERMS,
    build_wechat_short_title,
    build_wechat_verify_terms,
    extract_wechat_manual_error,
    extract_wechat_validation_error,
    extract_wechat_post_list_snapshot,
    format_wechat_publish_time,
    normalize_wechat_publish_payload,
)
from core.publishing.models import AdapterCapabilities, PublishJob
from core.utils.logger import log

# ── Constants ──────────────────────────────────────────────────────────────────
WECHAT_UPLOAD_URL = "https://channels.weixin.qq.com/platform/post/create"
WECHAT_VERIFY_URL = "https://channels.weixin.qq.com/platform/post/list"
POST_UPLOAD_FORM_TIMEOUT_MS = 600_000  # 10 min
POST_UPLOAD_FORM_POLL_MS = 1_000
VERIFY_TIMEOUT_MS = 180_000  # 3 min
VERIFY_POLL_MS = 5_000

UPLOAD_BLOCKING_TERMS = ["上传中", "处理中"]


# ── Adapter ────────────────────────────────────────────────────────────────────
class WechatChannelsSAUAdapter(AutomationAdapterBase, BasePublishingAdapter):
    """
    WeChat Channels adapter using semantic Playwright locators (SAU-style).

    Key differences from WechatChannelsAdapter:
    - Uses get_by_text(), get_by_role(), xpath following-sibling instead of
      fragile CSS selectors from YAML config.
    - Uses expect_file_chooser() for robust video upload.
    - Handles WeChat iframe resolution only as fallback (SAU works at page level).
    - More resilient to website UI changes.
    """

    platform_name = "wechat_channels"
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
        if not job.video_path or not job.title:
            raise ValueError("WeChat Channels publish requires video_path and title")

    # ── Semantic Locator Helpers ───────────────────────────────────────────────

    async def _upload_video_semantic(self, page, video_path: str) -> bool:
        """Upload video using file_chooser pattern (SAU's approach)."""
        # Primary: scoped video file input
        try:
            upload_input = page.locator("input[type='file'][accept*='video']")
            if await upload_input.count() > 0:
                await upload_input.first.set_input_files(video_path)
                log.info(f"📤 视频已通过video-accept输入上传: {os.path.basename(video_path)}")
                return True
        except Exception:
            pass

        # Fallback: file_chooser triggered by upload button/container
        try:
            upload_btn = page.locator(
                "button:has-text('上传视频'), "
                "div:has-text('上传视频'), "
                "div.upload-btn, "
                "div.form-item-upload"
            )
            if await upload_btn.count() > 0:
                async with page.expect_file_chooser(timeout=30_000) as fc_info:
                    await upload_btn.first.click()
                file_chooser = await fc_info.value
                await file_chooser.set_files(video_path)
                log.info(f"📤 视频已通过文件选择器上传: {os.path.basename(video_path)}")
                return True
        except Exception:
            pass

        # Last fallback: any file input (CDP approach)
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
                log.warning("⚠️ 视频号视频上传失败")
                return False

            # Check for manual permission errors
            if any(term in body for term in WECHAT_MANUAL_PERMISSION_TERMS):
                log.warning("⚠️ 视频号权限不足")
                return False

            # Check if still uploading
            if "上传中" not in body and "处理中" not in body:
                # Check that the title/description form has appeared
                try:
                    title_input = page.locator("div.input-editor")
                    publish_btn = page.locator("div.form-btns button:has-text('发表')")
                    if await title_input.count() > 0 or await publish_btn.count() > 0:
                        log.info("✅ 视频号视频上传完成，表单已就绪")
                        return True
                except Exception:
                    pass
                log.info("✅ 视频号视频上传完成")
                return True

            await page.wait_for_timeout(poll_ms)
            elapsed += poll_ms
        log.error("❌ 视频号视频上传超时")
        return False

    async def _fill_title_and_description_semantic(self, page, title: str, description: str) -> bool:
        """
        Fill title and description using SAU's approach:
        Locate 'div.input-editor' → type content with hashtags.
        WeChat uses a single editor for title + description + hashtags.
        Also fills the short title field separately.
        """
        # ── Main title/description editor ──────────────────────────────────────
        try:
            # SAU's approach: use div.input-editor for the combined title+desc
            editor = page.locator("div.input-editor")
            if await editor.count() > 0:
                await editor.first.click()
                await page.wait_for_timeout(300)
                # Clear existing content
                await page.keyboard.press("Control+KeyA")
                await page.keyboard.press("Delete")
                # Type title
                await page.keyboard.type(title, delay=30)
                # Press Enter to move to description
                await page.keyboard.press("Enter")
                await page.keyboard.type(description, delay=20)
                log.info(f"✅ 视频号标题和描述已填写 ({len(title)} + {len(description)} 字)")
                # Short title + YAML fallback are supplementary; return True here
                # to avoid falling through to the error return.
                await self._fill_short_title_and_hashtags(page, title)
                await self._try_yaml_title_fallback(page, title, description)
                return True
        except Exception as exc:
            log.warning(f"⚠️ 视频号input-editor填写失败: {exc}")
            # Fallback: contenteditable
            try:
                editable = page.locator("[contenteditable='true']")
                if await editable.count() > 0:
                    await editable.first.click()
                    await page.wait_for_timeout(300)
                    await page.keyboard.press("Control+KeyA")
                    await page.keyboard.press("Delete")
                    await page.keyboard.type(f"{title}\n{description}", delay=20)
                    log.info("✅ 视频号标题和描述已填写(contenteditable fallback)")
                    await self._fill_short_title_and_hashtags(page, title)
                    await self._try_yaml_title_fallback(page, title, description)
                    return True
            except Exception as exc2:
                log.warning(f"⚠️ 视频号contenteditable填写也失败: {exc2}")

        # All fill attempts failed
        log.error("❌ 视频号标题/描述填写失败")
        return False

    async def _fill_short_title_and_hashtags(self, page, title: str) -> None:
        """Fill short title and position cursor for hashtags (supplementary)."""
        # ── Short title field (SAU's approach) ────────────────────────────────
        try:
            short_title_label = page.get_by_text("短标题", exact=True)
            if await short_title_label.count() > 0:
                short_title_input = short_title_label.locator(
                    "xpath=following-sibling::div//input[@type='text']"
                )
                if await short_title_input.count() > 0:
                    short_title = build_wechat_short_title(title)
                    await short_title_input.first.fill(short_title)
                    log.info(f"✅ 视频号短标题已填写: {short_title}")
        except Exception as exc:
            log.warning(f"⚠️ 视频号短标题填写失败: {exc}")

        # ── Add hashtags (SAU's approach: type #tag + space) ──────────────────
        try:
            editor = page.locator("div.input-editor")
            if await editor.count() > 0:
                await editor.first.click()
                await page.keyboard.press("End")
                # Tags are already embedded in description by normalize_payload
        except Exception:
            pass

    async def _try_yaml_title_fallback(self, page, title: str, description: str) -> None:
        """Try YAML-configured selectors as supplementary fallback."""
        try:
            locators = self.selectors.get("locators", {})
            title_selectors = locators.get("title", [])
            desc_selectors = locators.get("description", [])
            if title_selectors:
                await self._fill_first(page, title_selectors, title)
            if desc_selectors:
                await self._fill_first(page, desc_selectors, description)
            if title_selectors or desc_selectors:
                log.info("✅ 视频号标题/描述已补充填写(YAML fallback)")
        except Exception as exc:
            log.warning(f"⚠️ 视频号YAML方式填写失败: {exc}")

    async def _set_cover_semantic(self, page, cover_path: str) -> bool:
        """
        Set cover thumbnail — multi-strategy approach:
        1. SAU flow: click "更换封面"/"添加封面" → upload image → confirm
        2. Direct file input
        3. YAML-configured selectors fallback
        """
        if not cover_path or not os.path.exists(cover_path):
            log.warning(f"⚠️ 封面文件无效: {cover_path}")
            return False

        # ── Strategy 1: SAU cover flow ────────────────────────────────────────
        try:
            # Find cover trigger button/area
            cover_trigger = None
            for label in ["更换封面", "添加封面", "设置封面", "选择封面"]:
                trigger = page.get_by_text(label, exact=True)
                if await trigger.count() > 0:
                    cover_trigger = trigger.first
                    break

            if cover_trigger is None:
                # Try via xpath from "封面" label
                cover_label = page.get_by_text("封面", exact=True)
                if await cover_label.count() > 0:
                    cover_trigger = cover_label.first.locator(
                        "xpath=following-sibling::div"
                    )

            if cover_trigger is not None:
                await cover_trigger.click()
                await page.wait_for_timeout(2000)

                # Look for "上传图片" / "本地上传" option
                for upload_label in ["上传图片", "本地上传"]:
                    upload_btn = page.get_by_text(upload_label, exact=True)
                    if await upload_btn.count() > 0:
                        await upload_btn.click()
                        await page.wait_for_timeout(1000)
                        break

                # Upload file
                img_input = page.locator("input[type='file'][accept*='image']")
                if await img_input.count() > 0:
                    await img_input.first.set_input_files(cover_path)
                    log.info("📤 封面文件已上传到视频号")
                    await page.wait_for_timeout(3000)

                    # Confirm cover
                    for btn_text in ["完成", "确定", "确认"]:
                        confirm_btn = page.get_by_role("button", name=btn_text)
                        if await confirm_btn.count() > 0:
                            await confirm_btn.click()
                            log.info(f"✅ 视频号封面确认成功({btn_text})")
                            await page.wait_for_timeout(2000)
                            return True

                    # Fallback: js click confirm
                    clicked = await self._js_click_text(page, ["完成", "确定", "确认"])
                    if clicked:
                        log.info("✅ 视频号封面确认成功(js)")
                        return True

                # Dismiss any lingering modal
                await self._dismiss_lingering_modal(page)
        except Exception as exc:
            log.warning(f"⚠️ 视频号封面设置(SAU flow)失败: {exc}")
            await self._dismiss_lingering_modal(page)

        # ── Strategy 2: Direct hidden file input ───────────────────────────────
        try:
            img_input = page.locator("input[type='file'][accept*='image']")
            if await img_input.count() > 0:
                await img_input.first.set_input_files(cover_path)
                log.info("📤 封面文件已通过隐藏输入框上传")
                await page.wait_for_timeout(3000)
                # Try to confirm
                for btn_text in ["完成", "确定", "确认"]:
                    confirm_btn = page.get_by_role("button", name=btn_text)
                    if await confirm_btn.count() > 0:
                        await confirm_btn.click()
                        log.info(f"✅ 视频号封面确认成功(hidden-input, {btn_text})")
                        return True
                # File was accepted even if we can't confirm
                log.info("📤 封面文件已被服务器接受，等待渲染...")
                return True
        except Exception as exc:
            log.warning(f"⚠️ 视频号封面设置(隐藏输入框)失败: {exc}")

        # ── Strategy 3: YAML-configured selectors fallback ───────────────────
        try:
            locators = self.selectors.get("locators", {})
            result = await self._apply_cover_robust(page, locators, cover_path)
            if result:
                log.info("✅ 视频号封面设置成功(YAML fallback)")
                return True
        except Exception as exc:
            log.warning(f"⚠️ 视频号封面设置(YAML fallback)失败: {exc}")

        log.error("❌ 视频号封面设置失败: 所有策略均未成功")
        return False

    async def _dismiss_lingering_modal(self, page) -> None:
        """Dismiss any lingering WeChat modal that blocks interactions."""
        try:
            close_btn = page.locator(
                ".ant-modal-close, .el-dialog__close, "
                "button:has-text('关闭'), button:has-text('取消'), "
                "[class*='close']:visible"
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
        """Click publish button — primary: form-scoped selector, then text match."""
        # Primary: class-based selector (SAU's approach: div.form-btns button)
        try:
            btn = page.locator("div.form-btns button:has-text('发表')")
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_timeout(3000)
                log.info("✅ 视频号发布按钮已点击(form-btns)")
                return True
        except Exception:
            pass

        # Fallback: role-based selector
        try:
            publish_btn = page.get_by_role("button", name="发表", exact=True)
            if await publish_btn.count() > 0:
                await publish_btn.click()
                await page.wait_for_timeout(3000)
                log.info("✅ 视频号发布按钮已点击(role)")
                return True
        except Exception:
            pass

        # Fallback: any button with 发表 text
        try:
            btn = page.locator("button:has-text('发表')")
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_timeout(3000)
                log.info("✅ 视频号发布按钮已点击(text)")
                return True
        except Exception:
            pass

        # Last fallback: YAML selectors
        locators = self.selectors.get("locators", {})
        publish_selectors = locators.get("publish_button", [])
        if publish_selectors:
            try:
                await self._click_first(page, publish_selectors)
                log.info("✅ 视频号发布按钮已点击(YAML)")
                return True
            except Exception:
                pass

        return False

    # ── Verification ───────────────────────────────────────────────────────────

    async def _fetch_post_list_snapshot(
        self,
        context,
        expected_title: str = "",
        *,
        verify_page=None,
    ) -> dict:
        """Fetch WeChat Channels post list for verification."""
        owned_page = None
        if verify_page is None:
            verify_page = await context.new_page()
            owned_page = verify_page
        try:
            try:
                async with verify_page.expect_response(
                    lambda response: "/post/post_list" in response.url,
                    timeout=5000,
                ) as response_info:
                    await verify_page.goto(WECHAT_VERIFY_URL, wait_until="domcontentloaded")
                response = await response_info.value
                payload = await response.json()
            except Exception:
                try:
                    await verify_page.goto(WECHAT_VERIFY_URL, wait_until="domcontentloaded")
                except Exception:
                    pass
                return {
                    "count": 0,
                    "object_ids": [],
                    "titles": [],
                    "newest_object_id": "",
                    "newest_create_time": "",
                    "matched_object_id": "",
                    "matched_create_time": "",
                    "platform_url": WECHAT_VERIFY_URL,
                }
            snapshot = extract_wechat_post_list_snapshot(payload, expected_title)
            snapshot["platform_url"] = WECHAT_VERIFY_URL
            return snapshot
        finally:
            if owned_page is not None:
                await verify_page.close()

    async def _verify_publish_result(self, context, baseline_snapshot: dict, expected_title: str = "") -> dict:
        """Verify publish result by checking the WeChat Channels post list."""
        baseline_ids = set(baseline_snapshot.get("object_ids") or [])
        elapsed = 0
        verify_page = await context.new_page()
        result_verified = False
        try:
            while elapsed <= VERIFY_TIMEOUT_MS:
                snapshot = await self._fetch_post_list_snapshot(
                    context,
                    expected_title,
                    verify_page=verify_page,
                )
                matched_object_id = str(snapshot.get("matched_object_id", "")).strip()
                if matched_object_id and matched_object_id not in baseline_ids:
                    result_verified = True
                    return {
                        "verified": True,
                        "verification_source": "post_list_response",
                        "platform_content_id": matched_object_id,
                        "platform_url": snapshot.get("platform_url", WECHAT_VERIFY_URL),
                        "published_at": format_wechat_publish_time(
                            snapshot.get("matched_create_time", "") or snapshot.get("newest_create_time", "")
                        ),
                        "last_error": "",
                    }
                elapsed += VERIFY_POLL_MS
                await asyncio.sleep(VERIFY_POLL_MS / 1000)
            return {
                "verified": False,
                "platform_content_id": "",
                "platform_url": "",
                "published_at": "",
                "last_error": "WeChat Channels publish result could not be verified from video list",
            }
        finally:
            if not result_verified:
                try:
                    await self._log_verification_context(verify_page, "wechat_channels", expected_title)
                except Exception:
                    pass
            await verify_page.close()

    # ── Main Publish Flow ──────────────────────────────────────────────────────

    async def _publish_async(self, payload: dict) -> dict:
        playwright = context = None
        try:
            playwright, context, page = await self._open_page(WECHAT_UPLOAD_URL)
            await page.wait_for_timeout(5000)

            # Take baseline snapshot for verification
            baseline_snapshot = await self._fetch_post_list_snapshot(
                context.browser_context,
                payload["title"],
            )

            # 1. Upload video
            video_path = payload["video_path"]
            log.info(f"📤 正在上传视频号视频: {os.path.basename(video_path)}")
            if not await self._upload_video_semantic(page, video_path):
                return {
                    "verified": False,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": "WeChat Channels video upload failed",
                }

            # 2. Wait for upload to complete
            if not await self._wait_for_upload_complete(
                page, timeout_ms=POST_UPLOAD_FORM_TIMEOUT_MS
            ):
                body_text = await self._extract_body_text(page)
                manual_error = extract_wechat_manual_error(body_text)
                if manual_error:
                    return {
                        "verified": False,
                        "manual_required": True,
                        "platform_content_id": "",
                        "platform_url": "",
                        "published_at": "",
                        "last_error": manual_error,
                    }
                return {
                    "verified": False,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": "WeChat Channels video upload timed out or failed",
                }

            # 3. Set cover (before filling title to avoid validation errors)
            cover_path = payload.get("cover_path", "")
            if cover_path:
                await self._set_cover_semantic(page, cover_path)

            # 4. Fill title and description
            title = payload.get("title", "")
            description = payload.get("description", "")
            await self._fill_title_and_description_semantic(page, title, description)

            # 5. Apply option controls from YAML config (for collection, visibility etc.)
            option_controls = self.selectors.get("option_controls", {})
            if option_controls:
                try:
                    await self._apply_option_controls(page, payload, option_controls)
                except Exception as exc:
                    log.warning(f"⚠️ 视频号选项控制失败: {exc}")

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
                    "last_error": "WeChat Channels publish is still processing uploaded media",
                }

            # 7. Check for validation/manual errors
            validation_error = extract_wechat_validation_error(body_text)
            if validation_error:
                return {
                    "verified": False,
                    "status": "failed_manual",
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": f"WeChat Channels validation failed: {validation_error}",
                }
            manual_error = extract_wechat_manual_error(body_text)
            if manual_error:
                return {
                    "verified": False,
                    "status": "failed_manual",
                    "manual_required": True,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": manual_error,
                }

            # 8. Preview pause (if BIYOU_PREVIEW is set)
            if not await self._check_preview_pause(page, "wechat_channels"):
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
                    "last_error": "WeChat Channels publish button not found or not clickable",
                }

            # 10. Post-publish check
            await page.wait_for_timeout(5000)
            post_body = await self._extract_body_text(page)
            validation_error = extract_wechat_validation_error(post_body)
            if validation_error:
                return {
                    "verified": False,
                    "status": "failed_manual",
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": f"WeChat Channels validation failed: {validation_error}",
                }
            manual_error = extract_wechat_manual_error(post_body)
            if manual_error:
                return {
                    "verified": False,
                    "status": "failed_manual",
                    "manual_required": True,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": manual_error,
                }

            # 11. Verify
            return await self._verify_publish_result(
                context.browser_context,
                baseline_snapshot,
                payload["title"],
            )

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
        payload = normalize_wechat_publish_payload(job)

        outcome = self.run_async(self._publish_async(payload))
        last_error = outcome.get("last_error", "")
        if outcome.get("verified") is False and not last_error:
            last_error = "WeChat Channels publish result could not be verified from video list"

        status = str(outcome.get("status", "") or "").strip()
        if outcome.get("verified") is False:
            if outcome.get("manual_required") or status == "failed_manual":
                status = "failed_manual"
            else:
                status = status or "failed_retryable"
        else:
            status = "published" if not last_error else (status or "failed_manual")

        return {
            "platform": self.platform_name,
            "status": status,
            "platform_content_id": outcome.get("platform_content_id", ""),
            "platform_url": outcome.get("platform_url", ""),
            "native_schedule": False,
            "scheduled_by": "biyou",
            "scheduled_for": "",
            "published_at": outcome.get("published_at", ""),
            "last_error": last_error,
            "extra": payload,
        }

    def schedule(self, job: PublishJob, publish_at: str) -> dict:
        self.validate(job)
        payload = normalize_wechat_publish_payload(job)
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

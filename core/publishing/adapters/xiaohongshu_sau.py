"""
Xiaohongshu (小红书) publishing adapter using semantic Playwright locators
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
from core.publishing.adapters.xiaohongshu import (
    XIAOHONGSHU_TITLE_LIMIT,
    XIAOHONGSHU_COVER_PROCESSING_TERMS,
    MANUAL_LOGIN_TERMS,
    MANUAL_VERIFICATION_TERMS,
    PUBLISH_FAILURE_TERMS,
    MANAGER_SUCCESS_TERMS,
    NOTE_URL_RE,
    build_verify_terms,
    is_xiaohongshu_upload_blocking,
    is_xiaohongshu_cover_processing,
    is_xiaohongshu_cover_modal_open,
    extract_xiaohongshu_publish_response_outcome,
    normalize_xiaohongshu_publish_payload,
    _extract_xiaohongshu_manager_record,
)
from core.publishing.models import AdapterCapabilities, PublishJob
from core.utils.logger import log

# ── Constants ──────────────────────────────────────────────────────────────────
XHS_UPLOAD_URL = "https://creator.xiaohongshu.com/publish/publish"
XHS_VERIFY_URL = "https://creator.xiaohongshu.com/new/note-manager"
XHS_PUBLISH_RESPONSE_URL_HINTS = (
    "/web_api/sns/v2/note",
    "/api/sns/v2/note",
)
XHS_MANAGER_RESPONSE_URL_HINTS = (
    "/api/",
    "/apis/",
    "/web_api/",
    "/api/sns/",
    "/api/creator/",
)

POST_UPLOAD_FORM_TIMEOUT_MS = 600_000  # 10 min
POST_UPLOAD_FORM_POLL_MS = 1_000
VERIFY_TIMEOUT_MS = 180_000  # 3 min
VERIFY_POLL_MS = 5_000

UPLOAD_BLOCKING_TERMS = ["视频上传中", "上传中", "处理中"]


# ── Adapter ────────────────────────────────────────────────────────────────────
class XiaohongshuSAUAdapter(AutomationAdapterBase, BasePublishingAdapter):
    """
    Xiaohongshu adapter using semantic Playwright locators (SAU-style).

    Key differences from XiaohongshuAdapter:
    - Uses get_by_text(), get_by_role(), xpath following-sibling instead of
      fragile CSS selectors from YAML config.
    - Uses expect_file_chooser() for robust video upload.
    - Simplified cover flow with multiple fallback strategies.
    - More resilient to website UI changes.
    """

    platform_name = "xiaohongshu"
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
            raise ValueError("Xiaohongshu publish requires video_path and title")

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
            upload_btn = page.locator(
                "button:has-text('上传视频'), "
                "div:has-text('上传视频'), "
                "div.upload-btn, "
                "span:has-text('上传视频')"
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
            if any(term in body for term in PUBLISH_FAILURE_TERMS):
                # But "转码中，不影响发布" is not a real failure
                if "转码中，不影响发布" not in body:
                    log.warning("⚠️ 小红书视频上传失败")
                    return False

            # Check for manual blocking
            if any(term in body for term in MANUAL_LOGIN_TERMS):
                log.warning("⚠️ 小红书需要登录")
                return False
            if any(term in body for term in MANUAL_VERIFICATION_TERMS):
                log.warning("⚠️ 小红书需要安全验证")
                return False

            # Check if still uploading (XHS-specific check)
            if not is_xiaohongshu_upload_blocking(body):
                # Check that the title/description form has appeared
                try:
                    title_input = page.locator("input[placeholder*='标题'], input[placeholder*='填写标题']")
                    desc_editor = page.locator("[contenteditable='true']")
                    if await title_input.count() > 0 or await desc_editor.count() > 0:
                        log.info("✅ 小红书视频上传完成，表单已就绪")
                        return True
                except Exception:
                    pass
                log.info("✅ 小红书视频上传完成")
                return True

            await page.wait_for_timeout(poll_ms)
            elapsed += poll_ms
        log.error("❌ 小红书视频上传超时")
        return False

    async def _fill_title_and_description_semantic(self, page, title: str, body: str) -> bool:
        """
        Fill title and description using semantic locators.
        Xiaohongshu has a 20-char title limit and uses 'body' for description (with hashtags).
        """
        title = title[:XIAOHONGSHU_TITLE_LIMIT]

        # ── Primary: title input by placeholder ────────────────────────────────
        try:
            # XHS title input typically has placeholder like "填写标题" or "标题"
            title_input = page.locator(
                "input[placeholder*='标题'], "
                "input[placeholder*='填写标题'], "
                "input[maxlength='20']"
            )
            if await title_input.count() > 0:
                await title_input.first.fill(title)
                log.info(f"✅ 小红书标题已填写 ({len(title)} 字)")
            else:
                # Fallback: get_by_text → following-sibling → input
                title_label = page.get_by_text("标题", exact=True)
                if await title_label.count() > 0:
                    title_field = title_label.first.locator(
                        "xpath=following-sibling::div//input"
                    )
                    if await title_field.count() > 0:
                        await title_field.first.fill(title)
                        log.info(f"✅ 小红书标题已填写(label→input, {len(title)} 字)")
        except Exception as exc:
            log.warning(f"⚠️ 小红书标题填写(placeholder)失败: {exc}")

        # ── Primary: description body editor ───────────────────────────────────
        try:
            # XHS description is in a contenteditable div
            desc_editor = page.locator(
                "[contenteditable='true']"
            )
            if await desc_editor.count() > 0:
                await desc_editor.first.click()
                await page.wait_for_timeout(300)
                await page.keyboard.press("Control+KeyA")
                await page.keyboard.press("Delete")
                await page.keyboard.type(body, delay=20)
                log.info(f"✅ 小红书描述已填写 ({len(body)} 字)")
                return True
        except Exception as exc:
            log.warning(f"⚠️ 小红书描述填写(contenteditable)失败: {exc}")

        # ── Fallback: YAML-configured selectors ───────────────────────────────
        try:
            locators = self.selectors.get("locators", {})
            title_selectors = locators.get("title", [])
            desc_selectors = locators.get("description", [])
            if title_selectors:
                await self._fill_first(page, title_selectors, title)
            if desc_selectors:
                await self._fill_first(page, desc_selectors, body)
            if title_selectors or desc_selectors:
                log.info("✅ 小红书标题/描述已填写(YAML fallback)")
                return True
        except Exception as exc:
            log.warning(f"⚠️ 小红书YAML方式填写失败: {exc}")

        log.error("❌ 小红书标题/描述填写失败")
        return False

    async def _set_cover_semantic(self, page, cover_path: str) -> bool:
        """
        Set cover thumbnail — multi-strategy approach for XHS.

        XHS cover flow is unique:
          - After video upload, XHS auto-generates a cover from the video frame
          - The cover area shows "封面上传中" while processing
          - Custom cover upload via "自定义封面" button may only be available
            after the initial cover finishes processing
          - Recommended covers (from video frames) are always available

        Strategy order:
        1. Wait for initial cover processing to finish
        2. Try custom cover upload: click cover area → "自定义封面" → upload → confirm
        3. Try recommended cover selection
        4. Fall back to YAML selectors
        """
        if not cover_path or not os.path.exists(cover_path):
            log.warning(f"⚠️ 封面文件无效: {cover_path}")
            return False

        # ── Step 0: Wait for initial cover processing ──────────────────────
        # XHS auto-generates a cover from the video; we must wait for it
        # before we can interact with the cover editor
        log.info("⏳ 等待小红书初始封面处理完成...")
        cover_body = await self._wait_for_text_to_clear(
            page,
            XIAOHONGSHU_COVER_PROCESSING_TERMS,
            timeout_ms=60_000,
            poll_ms=1_000,
        )
        if is_xiaohongshu_cover_processing(cover_body):
            log.warning("⚠️ 小红书初始封面仍在处理中，尝试继续...")
        else:
            log.info("✅ 小红书初始封面处理完成")
        await page.wait_for_timeout(1000)

        # ── Strategy 1: Custom cover upload via editor ──────────────────────
        try:
            # Click the cover area to open the editor
            cover_trigger = None

            # Primary: class-based selector for the cover operator area
            cover_op = page.locator(
                ".operator.noCover.pointer, "
                ".operator.default.column.center, "
                ".cover-plugin-title"
            )
            if await cover_op.count() > 0:
                cover_trigger = cover_op.first
            else:
                # Fallback: text-based search
                for label in ["设置封面", "修改封面", "更换封面"]:
                    trigger = page.get_by_text(label, exact=True)
                    if await trigger.count() > 0:
                        cover_trigger = trigger.first
                        break

            if cover_trigger is not None:
                await cover_trigger.click()
                await page.wait_for_timeout(2000)

                # Click "自定义封面" button/tab inside the editor
                custom_cover_btn = None
                for btn_label in ["自定义封面", "上传封面", "本地上传"]:
                    btn = page.get_by_text(btn_label, exact=True)
                    if await btn.count() > 0:
                        custom_cover_btn = btn.first
                        break

                # Also try class-based selector
                if custom_cover_btn is None:
                    btn_cls = page.locator(
                        ".upload-cover, .cover-upload, "
                        "button:has-text('自定义封面'), "
                        "span:has-text('自定义封面')"
                    )
                    if await btn_cls.count() > 0:
                        custom_cover_btn = btn_cls.first

                if custom_cover_btn is not None:
                    await custom_cover_btn.click()
                    await page.wait_for_timeout(1500)

                    # Image file input should now be available
                    img_input = page.locator(
                        "input[type='file'][accept*='image'], "
                        "input[type='file'][accept*='png'], "
                        "input[type='file'][accept*='jpg'], "
                        ".upload-input[accept*='image']"
                    )
                    if await img_input.count() > 0:
                        await img_input.first.set_input_files(cover_path)
                        log.info("📤 封面文件已上传到小红书(editor)")

                        # Wait for cover processing
                        await self._wait_for_text_to_clear(
                            page,
                            XIAOHONGSHU_COVER_PROCESSING_TERMS,
                            timeout_ms=30_000,
                            poll_ms=500,
                        )

                        # Confirm cover
                        for btn_text in ["确定", "确认", "完成"]:
                            confirm_btn = page.get_by_role("button", name=btn_text)
                            if await confirm_btn.count() > 0:
                                try:
                                    await confirm_btn.first.wait_for(
                                        state="visible", timeout=5000
                                    )
                                    await confirm_btn.click()
                                    log.info(f"✅ 小红书封面确认成功({btn_text})")
                                    await page.wait_for_timeout(2000)
                                    post_body = await self._extract_body_text(page)
                                    if not is_xiaohongshu_cover_modal_open(post_body):
                                        return True
                                except Exception:
                                    pass

                        # Fallback: js click confirm
                        clicked = await self._js_click_text(
                            page, ["确定", "确认", "完成"]
                        )
                        if clicked:
                            await page.wait_for_timeout(2000)
                            post_body = await self._extract_body_text(page)
                            if not is_xiaohongshu_cover_modal_open(post_body):
                                log.info("✅ 小红书封面确认成功(js)")
                                return True

                # Dismiss any lingering editor/modal
                await self._dismiss_lingering_modal(page)
        except Exception as exc:
            log.warning(f"⚠️ 小红书封面设置(editor flow)失败: {exc}")
            await self._dismiss_lingering_modal(page)

        # ── Strategy 2: Direct hidden file input ───────────────────────────────
        try:
            img_input = page.locator(
                "input[type='file'][accept*='image'], "
                "input[type='file'][accept*='png'], "
                ".upload-input[accept*='image']"
            )
            if await img_input.count() > 0:
                await img_input.first.set_input_files(cover_path)
                log.info("📤 封面文件已通过隐藏输入框上传")
                await self._wait_for_text_to_clear(
                    page,
                    XIAOHONGSHU_COVER_PROCESSING_TERMS,
                    timeout_ms=30_000,
                    poll_ms=500,
                )
                for btn_text in ["确定", "确认", "完成"]:
                    confirm_btn = page.get_by_role("button", name=btn_text)
                    if await confirm_btn.count() > 0:
                        await confirm_btn.click()
                        log.info(f"✅ 小红书封面确认成功(hidden, {btn_text})")
                        return True
                log.info("📤 封面文件已被服务器接受，等待渲染...")
                return True
        except Exception as exc:
            log.warning(f"⚠️ 小红书封面设置(隐藏输入框)失败: {exc}")

        # ── Strategy 3: Recommended cover fallback ────────────────────────────
        try:
            # Try semantic locator for recommended cover
            recommend_area = page.locator(".recommend .defaults > .default")
            if await recommend_area.count() > 0:
                await recommend_area.nth(1).click()  # 2nd option is usually better
                log.info("📤 正在选择推荐封面...")
                await page.wait_for_timeout(2000)
                for btn_text in ["确定", "确认", "完成"]:
                    confirm_btn = page.get_by_role("button", name=btn_text)
                    if await confirm_btn.count() > 0:
                        await confirm_btn.click()
                        log.info("✅ 小红书推荐封面已选择(semantic)")
                        return True
            # Fallback: YAML recommended_cover selectors
            locators = self.selectors.get("locators", {})
            recommended_selectors = locators.get("recommended_cover", [])
            if recommended_selectors:
                await self._click_first_force(page, recommended_selectors, timeout_ms=5000)
                for btn_text in ["确定", "确认", "完成"]:
                    confirm_btn = page.get_by_role("button", name=btn_text)
                    if await confirm_btn.count() > 0:
                        await confirm_btn.click()
                        log.info("✅ 小红书推荐封面已选择(YAML)")
                        return True
        except Exception as exc:
            log.warning(f"⚠️ 小红书推荐封面选择失败: {exc}")

        # ── Strategy 4: YAML-configured selectors fallback ───────────────────
        try:
            locators = self.selectors.get("locators", {})
            result = await self._apply_cover_robust(page, locators, cover_path)
            if result:
                log.info("✅ 小红书封面设置成功(YAML fallback)")
                return True
        except Exception as exc:
            log.warning(f"⚠️ 小红书封面设置(YAML fallback)失败: {exc}")

        log.warning("⚠️ 小红书封面设置失败: 所有策略均未成功（视频将使用默认封面）")
        return False

    async def _dismiss_lingering_modal(self, page) -> None:
        """Dismiss any lingering Xiaohongshu modal that blocks interactions."""
        try:
            close_btn = page.locator(
                ".ant-modal-close, .el-dialog__close, "
                "button:has-text('关闭'), button:has-text('取消'), "
                "[class*='close']:visible, "
                "div:has-text('回到旧版本')"
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
        # Primary: class-based selector
        try:
            # XHS publish button is typically at the bottom of the form
            btn = page.locator(
                "button.publishBtn, "
                "button:has-text('发布'), "
                "div.publishBtn, "
                "div[class*='publish-btn']"
            )
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_timeout(3000)
                log.info("✅ 小红书发布按钮已点击(class)")
                return True
        except Exception:
            pass

        # Fallback: role-based selector
        try:
            publish_btn = page.get_by_role("button", name="发布", exact=True)
            if await publish_btn.count() > 0:
                await publish_btn.click()
                await page.wait_for_timeout(3000)
                log.info("✅ 小红书发布按钮已点击(role)")
                return True
        except Exception:
            pass

        # Fallback: any button with 发布 text
        try:
            btn = page.locator("button:has-text('发布')")
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_timeout(3000)
                log.info("✅ 小红书发布按钮已点击(text)")
                return True
        except Exception:
            pass

        # Last fallback: YAML selectors
        locators = self.selectors.get("locators", {})
        publish_selectors = locators.get("publish_button", [])
        if publish_selectors:
            try:
                await self._click_first(page, publish_selectors)
                log.info("✅ 小红书发布按钮已点击(YAML)")
                return True
            except Exception:
                pass

        return False

    # ── Publish Response Detection ─────────────────────────────────────────────

    def _is_xiaohongshu_publish_response(self, response) -> bool:
        url = str(getattr(response, "url", "") or "").lower()
        return "xiaohongshu.com" in url and any(
            hint in url for hint in XHS_PUBLISH_RESPONSE_URL_HINTS
        )

    def _is_xiaohongshu_manager_response(self, response) -> bool:
        url = str(getattr(response, "url", "") or "").lower()
        return "xiaohongshu.com" in url and any(
            hint in url for hint in XHS_MANAGER_RESPONSE_URL_HINTS
        )

    # ── Verification ───────────────────────────────────────────────────────────

    async def _extract_matching_note_link(self, target, title: str) -> tuple[str, str]:
        """Extract note link matching title from the page."""
        links = await target.evaluate(
            """() => {
                return Array.from(document.querySelectorAll('a[href]'))
                    .map((anchor) => {
                        const containerText =
                            anchor.closest('article, section, li, tr, div')?.innerText || '';
                        return {
                            href: anchor.href || '',
                            text: (anchor.innerText || anchor.textContent || containerText || '').trim(),
                        };
                    })
                    .filter(
                        (item) =>
                            item.href &&
                            (item.href.includes('/explore/') || item.href.includes('/discovery/item/'))
                    );
            }"""
        )
        for link in links or []:
            href = str((link or {}).get("href", "")).strip()
            match = NOTE_URL_RE.search(href)
            if not match:
                continue
            note_id = match.group(1)
            link_text = str((link or {}).get("text", ""))
            if any(term in link_text for term in build_verify_terms(title)):
                return note_id, href
        return "", ""

    async def _verify_publish_result(self, context, payload: dict) -> dict:
        """Verify publish result by checking the Xiaohongshu note manager."""
        verify_page = await context.new_page()
        result_verified = False
        try:
            elapsed = 0
            while True:
                # Try API response
                try:
                    async with verify_page.expect_response(
                        lambda response: self._is_xiaohongshu_manager_response(response),
                        timeout=5000,
                    ) as response_info:
                        await verify_page.goto(XHS_VERIFY_URL, wait_until="domcontentloaded")
                    response = await response_info.value
                    manager_record = _extract_xiaohongshu_manager_record(
                        await response.json(),
                        payload["title"],
                    )
                    if manager_record.get("platform_content_id"):
                        result_verified = True
                        return {
                            "verified": True,
                            "verification_source": "note_manager_response",
                            "platform_content_id": manager_record.get("platform_content_id", ""),
                            "platform_url": manager_record.get("platform_url", ""),
                            "published_at": "",
                            "last_error": "",
                        }
                    if manager_record.get("manager_success"):
                        result_verified = True
                        return {
                            "verified": True,
                            "verification_source": "note_manager_row",
                            "platform_content_id": "",
                            "platform_url": manager_record.get("platform_url", XHS_VERIFY_URL),
                            "published_at": "",
                            "last_error": "",
                        }
                except Exception:
                    try:
                        await verify_page.goto(XHS_VERIFY_URL, wait_until="domcontentloaded")
                    except Exception:
                        pass

                # Try link extraction
                note_id, note_url = await self._extract_matching_note_link(verify_page, payload["title"])
                if note_id:
                    result_verified = True
                    return {
                        "verified": True,
                        "verification_source": "note_manager_link",
                        "platform_content_id": note_id,
                        "platform_url": note_url,
                        "published_at": "",
                        "last_error": "",
                    }

                # Check for manual blocking
                body_text = await self._extract_body_text(verify_page)
                if any(term in body_text for term in MANUAL_LOGIN_TERMS):
                    return {
                        "verified": False,
                        "manual_required": True,
                        "platform_content_id": "",
                        "platform_url": "",
                        "published_at": "",
                        "last_error": "Xiaohongshu publish requires login in creator center",
                    }

                # Text-based success check
                if any(term in body_text for term in MANAGER_SUCCESS_TERMS):
                    terms = build_verify_terms(payload["title"])
                    if any(t in body_text for t in terms):
                        result_verified = True
                        return {
                            "verified": True,
                            "verification_source": "note_manager_text",
                            "platform_content_id": "",
                            "platform_url": XHS_VERIFY_URL,
                            "published_at": "",
                            "last_error": "",
                        }

                if elapsed >= VERIFY_TIMEOUT_MS:
                    break
                await verify_page.wait_for_timeout(VERIFY_POLL_MS)
                elapsed += VERIFY_POLL_MS

            return {
                "verified": False,
                "status": "failed_retryable",
                "platform_content_id": "",
                "platform_url": "",
                "published_at": "",
                "last_error": "Xiaohongshu publish result could not be verified from note manager",
            }
        finally:
            if not result_verified:
                try:
                    await self._log_verification_context(verify_page, "xiaohongshu", payload.get("title", ""))
                except Exception:
                    pass
            await verify_page.close()

    # ── Main Publish Flow ──────────────────────────────────────────────────────

    async def _publish_async(self, payload: dict) -> dict:
        playwright = context = None
        try:
            playwright, context, page = await self._open_page(XHS_UPLOAD_URL)
            await page.wait_for_timeout(5000)

            # 1. Upload video
            video_path = payload["video_path"]
            log.info(f"📤 正在上传小红书视频: {os.path.basename(video_path)}")
            if not await self._upload_video_semantic(page, video_path):
                return {
                    "verified": False,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": "Xiaohongshu video upload failed",
                }

            # 2. Wait for upload to complete and form to appear
            if not await self._wait_for_upload_complete(
                page, timeout_ms=POST_UPLOAD_FORM_TIMEOUT_MS
            ):
                body_text = await self._extract_body_text(page)
                if any(term in body_text for term in MANUAL_LOGIN_TERMS):
                    return {
                        "verified": False,
                        "manual_required": True,
                        "platform_content_id": "",
                        "platform_url": "",
                        "published_at": "",
                        "last_error": "Xiaohongshu publish requires login in creator center",
                    }
                return {
                    "verified": False,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": "Xiaohongshu video upload timed out or form did not appear",
                }

            # 3. Fill title and description
            title = payload.get("title", "")
            body = payload.get("body", payload.get("description", ""))
            if not await self._fill_title_and_description_semantic(page, title, body):
                return {
                    "verified": False,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": "Xiaohongshu title/description fill failed",
                }

            # 4. Apply option controls from YAML config
            option_controls = self.selectors.get("option_controls", {})
            if option_controls:
                try:
                    await self._apply_option_controls(page, payload, option_controls)
                except Exception as exc:
                    log.warning(f"⚠️ 小红书选项控制失败: {exc}")

            # 5. Set cover
            cover_path = payload.get("cover_path", "")
            if cover_path:
                await self._set_cover_semantic(page, cover_path)

            # 6. Wait for any upload processing to complete
            body_text = await self._extract_body_text(page)
            if is_xiaohongshu_upload_blocking(body_text):
                body_text = await self._wait_for_text_to_clear(
                    page,
                    UPLOAD_BLOCKING_TERMS,
                    timeout_ms=POST_UPLOAD_FORM_TIMEOUT_MS,
                    poll_ms=POST_UPLOAD_FORM_POLL_MS,
                )
            if is_xiaohongshu_upload_blocking(body_text):
                return {
                    "verified": False,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": "Xiaohongshu publish is still processing upload/transcode",
                }

            # 7. Check for manual blocking before publish
            if any(term in body_text for term in MANUAL_VERIFICATION_TERMS):
                return {
                    "verified": False,
                    "manual_required": True,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": "Xiaohongshu publish requires manual security verification",
                }

            # 8. Preview pause (if BIYOU_PREVIEW is set)
            if not await self._check_preview_pause(page, "xiaohongshu"):
                return {
                    "verified": False,
                    "status": "preview",
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": "preview mode — publish skipped",
                }

            # 9. Try to capture publish response via API interception
            publish_response_outcome = None
            try:
                async with page.expect_response(
                    lambda response: self._is_xiaohongshu_publish_response(response),
                    timeout=15_000,
                ) as response_info:
                    if not await self._click_publish_semantic(page):
                        return {
                            "verified": False,
                            "platform_content_id": "",
                            "platform_url": "",
                            "published_at": "",
                            "last_error": "Xiaohongshu publish button not found or not clickable",
                        }
                response = await response_info.value
                publish_payload = await response.json()
                publish_response_outcome = extract_xiaohongshu_publish_response_outcome(publish_payload)
            except Exception:
                # If API interception fails, just click publish normally
                if not await self._click_publish_semantic(page):
                    return {
                        "verified": False,
                        "platform_content_id": "",
                        "platform_url": "",
                        "published_at": "",
                        "last_error": "Xiaohongshu publish button not found or not clickable",
                    }

            # 10. Check publish response outcome
            if publish_response_outcome is not None:
                return publish_response_outcome

            # 11. Post-publish check
            await page.wait_for_timeout(5000)
            post_body = await self._extract_body_text(page)
            if any(term in post_body for term in PUBLISH_FAILURE_TERMS):
                return {
                    "verified": False,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": "Xiaohongshu publish failed in creator center",
                }

            # 12. Verify via note manager
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
        payload = normalize_xiaohongshu_publish_payload(job)

        outcome = self.run_async(self._publish_async(payload))
        last_error = outcome.get("last_error", "")
        platform_content_id = str(outcome.get("platform_content_id", "")).strip()
        platform_url = str(outcome.get("platform_url", "")).strip()
        verified = outcome.get("verified") is True or (
            not last_error and (platform_content_id or platform_url)
        )
        manual_required = outcome.get("manual_required") is True
        verification_state = outcome.get("verification_state", "")
        verification_source = outcome.get("verification_source", "")

        if not verified and not last_error:
            if manual_required:
                last_error = "manual intervention required"
            else:
                last_error = "Xiaohongshu publish result could not be verified from note manager"

        if verified:
            status = "published"
            verification_state = verification_state or "verified"
        elif manual_required:
            status = "failed_manual"
            verification_state = verification_state or "manual"
        else:
            status = outcome.get("status", "failed_retryable") or "failed_retryable"
            verification_state = verification_state or "unknown"

        extra = dict(payload)
        extra["verification_state"] = verification_state
        if verification_source:
            extra["verification_source"] = verification_source

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
            "extra": extra,
        }

    def schedule(self, job: PublishJob, publish_at: str) -> dict:
        self.validate(job)
        payload = normalize_xiaohongshu_publish_payload(job)
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

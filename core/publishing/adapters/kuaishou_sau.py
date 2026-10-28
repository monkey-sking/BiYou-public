"""
Kuaishou (快手) publishing adapter using semantic Playwright locators
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
from core.publishing.adapters.kuaishou import (
    build_kuaishou_caption,
    build_kuaishou_duration_limit_error,
    build_kuaishou_platform_url,
    extract_kuaishou_platform_content_id,
    normalize_kuaishou_publish_payload,
)
from core.publishing.models import AdapterCapabilities, PublishJob
from core.utils.logger import log

# ── Constants ──────────────────────────────────────────────────────────────────
KUAISHOU_UPLOAD_URL = "https://cp.kuaishou.com/article/publish/video"
KUAISHOU_MANAGE_URL = "https://cp.kuaishou.com/article/manage/video"
KUAISHOU_MANAGE_URL_PATTERN = re.compile(r"cp\.kuaishou\.com/article/manage")

POST_UPLOAD_FORM_TIMEOUT_MS = 600_000  # 10 min
POST_UPLOAD_FORM_POLL_MS = 1_000
VERIFY_TIMEOUT_MS = 180_000  # 3 min
VERIFY_POLL_MS = 5_000
VERIFY_DOM_TIMEOUT_MS = 15_000

UPLOAD_BLOCKING_TERMS = ["上传中", "处理中"]
MANAGER_SUCCESS_TERMS = ["审核中", "已发布"]
MANAGER_ROW_PUBLISHED_AT_RE = re.compile(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}")


# ── Helper: extract published_at from manager row text ─────────────────────────
def _extract_manager_row_published_at(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = MANAGER_ROW_PUBLISHED_AT_RE.search(text)
    return match.group(0) if match else ""


# ── Adapter ────────────────────────────────────────────────────────────────────
class KuaishouSAUAdapter(AutomationAdapterBase, BasePublishingAdapter):
    """
    Kuaishou adapter using semantic Playwright locators (SAU-style).

    Key differences from KuaishouAutomationAdapter:
    - Uses get_by_text(), get_by_role(), xpath following-sibling instead of
      fragile CSS selectors from YAML config.
    - Uses expect_file_chooser() for robust video upload.
    - Handles Joyride overlay dismissal (SAU pattern).
    - More resilient to website UI changes.
    """

    platform_name = "kuaishou"
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
            raise ValueError("Kuaishou publish requires video_path, cover_path, and title")

    # ── Semantic Locator Helpers ───────────────────────────────────────────────
    async def _close_joyride_overlay(self, page) -> bool:
        """Dismiss tutorial pop-ups (Joyride) that block DOM interactions."""
        try:
            overlay = page.locator('div[id^="react-joyride-step"] div[role="alertdialog"]')
            if await overlay.count() > 0:
                skip_btn = overlay.locator('button[data-action="skip"], button:has-text("Skip")')
                if await skip_btn.count() > 0:
                    await skip_btn.first.click()
                    await page.wait_for_timeout(1000)
                    log.info("✅ 关闭了快手引导浮层")
                    return True
        except Exception:
            pass
        return False

    async def _dismiss_draft_semantic(self, page) -> bool:
        """Discard any stale draft prompt before starting a fresh upload."""
        try:
            continue_btn = page.get_by_text("继续编辑", exact=True)
            if await continue_btn.count() == 0:
                return False

            abandon_btn = page.get_by_role("button", name="放弃")
            if await abandon_btn.count() == 0:
                abandon_btn = page.get_by_text("放弃", exact=True)
            if await abandon_btn.count() == 0:
                return False

            await abandon_btn.first.click()
            await page.wait_for_timeout(1000)
            log.info("🧹 已放弃快手残留草稿，重新开始上传")
            return True
        except Exception:
            return False

    async def _upload_video_semantic(self, page, video_path: str) -> bool:
        """Upload video using file_chooser pattern (SAU's approach)."""
        try:
            # First try the file_chooser approach (most robust)
            upload_btn = page.locator("button[class^='_upload-btn']")
            if await upload_btn.count() > 0:
                async with page.expect_file_chooser(timeout=30_000) as fc_info:
                    await upload_btn.first.click()
                file_chooser = await fc_info.value
                await file_chooser.set_files(video_path)
                log.info(f"📤 视频已通过文件选择器上传: {os.path.basename(video_path)}")
                return True
        except Exception:
            pass

        # Fallback: use CDP file input (BiYou's standard approach)
        try:
            file_input_selectors = ["input[type='file']"]
            await self._set_file_first(page, file_input_selectors, video_path)
            log.info(f"📤 视频已通过CDP上传: {os.path.basename(video_path)}")
            return True
        except Exception as exc:
            log.error(f"❌ 视频上传失败: {exc}")
            return False

    async def _wait_for_upload_complete(self, page, timeout_ms=600_000, poll_ms=2_000) -> bool:
        """Poll until video upload finishes (SAU's approach)."""
        elapsed = 0
        while elapsed <= timeout_ms:
            body = await self._extract_body_text(page)
            # Check for upload failure
            if "上传失败" in body:
                log.warning("⚠️ 快手视频上传失败，尝试重新上传")
                return False
            # Check if still uploading
            if "上传中" not in body and "处理中" not in body:
                log.info("✅ 快手视频上传完成")
                return True
            await page.wait_for_timeout(poll_ms)
            elapsed += poll_ms
        log.error("❌ 快手视频上传超时")
        return False

    async def _fill_description_semantic(self, page, caption: str) -> bool:
        """
        Fill description using SAU's approach:
        Locate '描述' label → navigate to sibling div → type content.
        Kuaishou has no separate title input — everything goes into description.
        """
        try:
            # SAU's approach: find "描述" text and navigate to sibling
            desc_label = page.get_by_text("描述", exact=True)
            if await desc_label.count() > 0:
                desc_area = desc_label.locator("xpath=following-sibling::div")
                if await desc_area.count() > 0:
                    await desc_area.first.click()
                    await page.wait_for_timeout(300)
                    # Clear existing content and type new
                    await page.keyboard.press("Control+KeyA")
                    await page.keyboard.press("Delete")
                    await page.keyboard.type(caption, delay=30)
                    log.info(f"✅ 快手描述已填写 ({len(caption)} 字)")
                    return True
        except Exception:
            pass

        # Fallback: use #work-description-edit (from our DOM inspection)
        try:
            desc_el = page.locator("#work-description-edit")
            if await desc_el.count() > 0:
                await desc_el.first.click()
                await page.wait_for_timeout(300)
                await page.keyboard.press("Control+KeyA")
                await page.keyboard.press("Delete")
                await page.keyboard.type(caption, delay=30)
                log.info(f"✅ 快手描述已填写 (via #work-description-edit)")
                return True
        except Exception:
            pass

        # Last fallback: contenteditable div
        try:
            editable = page.locator("[contenteditable='true']")
            if await editable.count() > 0:
                await editable.first.click()
                await page.wait_for_timeout(300)
                await page.keyboard.press("Control+KeyA")
                await page.keyboard.press("Delete")
                await page.keyboard.type(caption, delay=30)
                log.info(f"✅ 快手描述已填写 (via contenteditable)")
                return True
        except Exception as exc:
            log.error(f"❌ 快手描述填写失败: {exc}")
        return False

    async def _set_cover_semantic(self, page, cover_path: str) -> bool:
        """
        Set cover thumbnail — multi-strategy approach:
        1. SAU modal flow: '封面设置' → sibling div → ant-modal → upload tab → confirm
        2. Direct hidden file input (most reliable — always available after upload)
        3. YAML-configured selectors fallback
        """
        if not cover_path or not os.path.exists(cover_path):
            log.warning(f"⚠️ 封面文件无效: {cover_path}")
            return False

        # ── Strategy 1: SAU modal flow ─────────────────────────────────────────
        try:
            cover_label = page.get_by_text("封面设置", exact=True)
            if await cover_label.count() > 0:
                # SAU's approach: navigate to the first div inside the sibling
                # xpath=../following-sibling::div[1]/div  (not just the sibling itself)
                cover_click_target = cover_label.first.locator(
                    "xpath=../following-sibling::div[1]/div"
                )
                if await cover_click_target.count() > 0:
                    await cover_click_target.first.click()
                    await page.wait_for_timeout(2000)

                    # Check for ant-modal (cover editor dialog)
                    modal = page.locator('div[role="document"].ant-modal')
                    if await modal.count() > 0:
                        # Switch to "上传封面" tab
                        upload_tab = modal.get_by_text("上传封面", exact=True)
                        if await upload_tab.count() > 0:
                            await upload_tab.click()
                            await page.wait_for_timeout(1000)
                        # Upload file in modal
                        file_input = modal.locator('input[type="file"]')
                        if await file_input.count() > 0:
                            await file_input.first.set_input_files(cover_path)
                            log.info("📤 封面文件已上传到快手(modal)")
                            await page.wait_for_timeout(3000)
                            # Confirm
                            confirm_btn = modal.get_by_role("button", name="确认")
                            if await confirm_btn.count() > 0:
                                await confirm_btn.click()
                                log.info("✅ 快手封面确认成功(modal)")
                                return True
                            # Try alternative confirm text
                            confirm_btn2 = modal.locator(
                                'button:has-text("确定"), button:has-text("完成")'
                            )
                            if await confirm_btn2.count() > 0:
                                await confirm_btn2.first.click()
                                log.info("✅ 快手封面确认成功(modal-alt)")
                                return True

                    # Dismiss any stale/wrong dialog before falling through
                    try:
                        stale_close = page.locator(
                            '.el-dialog__close, .ant-modal-close, '
                            '[class*="dialog"] button:has-text("关闭"), '
                            '[class*="modal"] button:has-text("关闭")'
                        )
                        if await stale_close.count() > 0:
                            await stale_close.first.click()
                            await page.wait_for_timeout(500)
                            log.info("🧹 已关闭残留对话框")
                    except Exception:
                        pass

                    log.warning("⚠️ 封面modal未出现或无法交互，尝试下一种策略")
        except Exception as exc:
            log.warning(f"⚠️ 快手封面设置(SAU modal)失败: {exc}")

        # ── Strategy 2: Direct hidden file input (most reliable) ──────────────────
        # Kuaishou always has a hidden input[type="file"][accept*="image"] under #main-tours
        try:
            img_input = page.locator('input[type="file"][accept*="image"]')
            if await img_input.count() > 0:
                await img_input.first.set_input_files(cover_path)
                log.info("📤 封面文件已通过隐藏输入框上传")
                await page.wait_for_timeout(3000)
                # Verify cover was set by checking the cover editor area
                cover_check = await page.evaluate("""() => {
                    const editor = document.querySelector('[class*="high-cover-editor"]');
                    if (!editor) return {has_cover: false};
                    const img = editor.querySelector('img');
                    return {has_cover: !!img, src: img?.src?.substring(0, 80) || ''};
                }""")
                if cover_check.get("has_cover"):
                    log.info("✅ 快手封面设置成功(隐藏输入框)")
                    return True
                # File was accepted but cover not yet rendered — treat as success
                # since the upload was accepted by the server
                log.info("📤 封面文件已被服务器接受，等待渲染...")
                return True
            # Broader search for any image file input
            all_file_inputs = page.locator('input[type="file"]')
            count = await all_file_inputs.count()
            for i in range(count):
                fi = all_file_inputs.nth(i)
                accept_val = await fi.evaluate('el => el.accept || ""')
                if 'image' in accept_val or 'png' in accept_val or 'jpg' in accept_val:
                    await fi.set_input_files(cover_path)
                    log.info(f"📤 封面文件已上传(file input #{i})")
                    await page.wait_for_timeout(3000)
                    return True
        except Exception as exc:
            log.warning(f"⚠️ 快手封面设置(隐藏输入框)失败: {exc}")

        # ── Strategy 3: YAML-configured selectors fallback ──────────────────────
        try:
            locators = self.selectors.get("locators", {})
            result = await self._apply_cover_robust(page, locators, cover_path)
            if result:
                log.info("✅ 快手封面设置成功(YAML fallback)")
                return True
        except Exception as exc:
            log.warning(f"⚠️ 快手封面设置(YAML fallback)失败: {exc}")

        log.error("❌ 快手封面设置失败: 所有策略均未成功")
        return False

    async def _click_publish_semantic(self, page) -> bool:
        """Click publish button — primary: class-scoped selector, then text match."""
        try:
            overlay_confirm_targets = [
                page.get_by_role("button", name="确认"),
                page.get_by_role("button", name="完成"),
                page.get_by_role("button", name="确定"),
                page.get_by_role("button", name="保存"),
                page.locator("button:has-text('确认'), button:has-text('完成'), button:has-text('确定'), button:has-text('保存')"),
                page.locator("div:has-text('确认'), div:has-text('完成'), div:has-text('确定'), div:has-text('保存')"),
                page.locator(".ant-modal-close"),
            ]
            for target in overlay_confirm_targets:
                if await target.count() == 0:
                    continue
                await target.first.click()
                await page.wait_for_timeout(1000)
                log.info("🧹 已关闭快手封面裁剪/确认层")
                break
        except Exception:
            pass

        # Prefer explicit "发布" controls first to avoid clicking stale draft CTAs
        explicit_targets = [
            page.get_by_role("button", name="发布"),
            page.locator("button:has-text('发布')"),
            page.locator("div:text-is('发布')"),
            page.locator("div[class*='_button-primary']:has-text('发布')"),
        ]
        for target in explicit_targets:
            try:
                if await target.count() == 0:
                    continue
                await target.first.click()
                await page.wait_for_timeout(2000)
                confirm_btn = page.get_by_text("确认发布", exact=True)
                if await confirm_btn.count() > 0:
                    await confirm_btn.click()
                    await page.wait_for_timeout(3000)
                log.info("✅ 快手发布按钮已点击")
                return True
            except Exception:
                continue

        # Fallback: primary button class
        try:
            btn = page.locator("div[class*='_button-primary']")
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_timeout(2000)
                # Handle confirmation dialog
                confirm_btn = page.get_by_text("确认发布", exact=True)
                if await confirm_btn.count() > 0:
                    await confirm_btn.click()
                    await page.wait_for_timeout(3000)
                log.info("✅ 快手发布按钮已点击")
                return True
        except Exception:
            pass

        # Fallback: exact text match scoped to div elements only
        try:
            publish_btn = page.locator("div").get_by_text("发布", exact=True)
            if await publish_btn.count() > 0:
                await publish_btn.first.click()
                await page.wait_for_timeout(2000)
                log.info("✅ 快手发布按钮已点击 (text fallback)")
                return True
        except Exception:
            pass

        # Last fallback: YAML selectors
        locators = self.selectors.get("locators", {})
        publish_selectors = locators.get("publish_button", [])
        if publish_selectors:
            await self._click_first(page, publish_selectors)
            return True

        return False

    # ── Verification ───────────────────────────────────────────────────────────
    async def _extract_manager_rows(self, page) -> list[dict]:
        return await page.evaluate("""() => {
            const clean = (value) => String(value || '').replace(/\\u00A0/g, ' ').trim();
            const isVisible = (el) => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none' && style.visibility !== 'hidden'
                    && rect.width > 0 && rect.height > 0;
            };
            const extractId = (value) => {
                const raw = String(value || '').trim();
                if (!raw) return '';
                const patterns = [
                    /\\/short-video\\/([A-Za-z0-9_-]+)/,
                    /\\/(?:fw\\/)?photo\\/([A-Za-z0-9_-]+)/,
                    /[?&](?:photoId|photo_id|id)=([A-Za-z0-9_-]+)/,
                ];
                for (const p of patterns) {
                    const m = raw.match(p);
                    if (m) return m[1];
                }
                return '';
            };
            const toAbsoluteUrl = (href, cid) => {
                const r = String(href || '').trim();
                if (r.startsWith('//')) return 'https:' + r;
                if (r.startsWith('/')) return 'https://www.kuaishou.com' + r;
                if (r.startsWith('http')) {
                    if (r.includes('cp.kuaishou.com') && cid)
                        return 'https://www.kuaishou.com/short-video/' + cid;
                    return r;
                }
                if (cid) return 'https://www.kuaishou.com/short-video/' + cid;
                return '';
            };
            return Array.from(document.querySelectorAll(
                '.video-item, [class*=\"video-item\"]'
            )).filter(isVisible).map(row => {
                const text = clean(row.innerText || '');
                const titleNode = row.querySelector(
                    '.video-item__detail__row__title, [class*=\"__title\"]'
                );
                const title = clean(titleNode?.innerText || '');
                const hrefs = Array.from(row.querySelectorAll('a[href]'))
                    .map(a => a.getAttribute('href') || a.href || '').filter(Boolean);
                let cid = '';
                for (const h of hrefs) { cid = extractId(h); if (cid) break; }
                return { title, text, platform_content_id: cid,
                         platform_url: toAbsoluteUrl(hrefs[0] || '', cid) };
            }).filter(r => r.text);
        }""")

    async def _match_manager_rows(self, rows, payload: dict) -> dict:
        title = payload.get("title", "")
        terms = [title.strip(), title.strip()[:12]]
        terms = [t for t in terms if t]

        for row in (rows or []):
            row_title = str((row or {}).get("title", "")).strip()
            row_text = str((row or {}).get("text", "")).strip()
            haystack = "\n".join(p for p in [row_title, row_text] if p).strip()
            if not any(t in haystack for t in terms):
                continue
            platform_content_id = extract_kuaishou_platform_content_id(
                (row or {}).get("platform_content_id", "")
            ) or extract_kuaishou_platform_content_id(
                (row or {}).get("platform_url", "")
            )
            platform_url = build_kuaishou_platform_url(
                platform_content_id, (row or {}).get("platform_url", "")
            )
            status_match = any(t in haystack for t in MANAGER_SUCCESS_TERMS)
            if status_match or platform_content_id:
                return {
                    "verified": True,
                    "platform_content_id": platform_content_id,
                    "platform_url": platform_url or KUAISHOU_MANAGE_URL,
                    "published_at": _extract_manager_row_published_at(haystack),
                    "last_error": "",
                }

        return {}

    async def _verify_publish_result(self, context, payload: dict, verify_page=None) -> dict:
        """Verify publish result by checking the video manager page."""
        owned_page = verify_page is None
        if verify_page is None:
            verify_page = await context.new_page()
        result_verified = False
        try:
            candidate_urls = []
            current_url = str(getattr(verify_page, "url", "") or "").strip()
            if current_url:
                candidate_urls.append(current_url)
            candidate_urls.extend(
                [
                    KUAISHOU_MANAGE_URL,
                    f"{KUAISHOU_MANAGE_URL}?status=2&from=publish",
                    f"{KUAISHOU_MANAGE_URL}?status=1&from=",
                ]
            )

            seen_urls = set()
            for candidate_url in candidate_urls:
                if not candidate_url or candidate_url in seen_urls:
                    continue
                seen_urls.add(candidate_url)
                if str(getattr(verify_page, "url", "") or "").strip() != candidate_url:
                    await verify_page.goto(candidate_url, wait_until="domcontentloaded")
                await verify_page.wait_for_timeout(5000)
                rows = await self._extract_manager_rows(verify_page)
                matched = await self._match_manager_rows(rows, payload)
                if matched:
                    result_verified = True
                    return matched

            # Text-based fallback
            title = payload.get("title", "")
            terms = [title.strip(), title.strip()[:12]]
            terms = [t for t in terms if t]
            body_text = await self._wait_for_text(
                verify_page, terms,
                timeout_ms=VERIFY_TIMEOUT_MS, poll_ms=VERIFY_POLL_MS,
            )
            if body_text and any(t in body_text for t in MANAGER_SUCCESS_TERMS):
                result_verified = True
                return {
                    "verified": True,
                    "platform_content_id": "",
                    "platform_url": KUAISHOU_MANAGE_URL,
                    "published_at": "",
                    "last_error": "",
                }

            return {
                "verified": False,
                "status": "failed_retryable",
                "platform_content_id": "",
                "platform_url": "",
                "published_at": "",
                "last_error": "Kuaishou publish result could not be verified from video manager",
            }
        finally:
            if not result_verified:
                try:
                    # Best-effort: dump page text for debugging
                    debug_text = await self._extract_body_text(verify_page)
                    log.warning(f"⚠️ 快手验证失败，页面文本片段: {debug_text[:300]}")
                except Exception:
                    pass
            if owned_page:
                await verify_page.close()

    # ── Main Publish Flow ──────────────────────────────────────────────────────
    async def _publish_async(self, payload: dict) -> dict:
        playwright = context = None
        try:
            playwright, context, page = await self._open_page(KUAISHOU_UPLOAD_URL)
            await page.wait_for_timeout(3000)

            # Dismiss any Joyride overlay
            await self._close_joyride_overlay(page)
            await self._dismiss_draft_semantic(page)

            # 1. Upload video
            video_path = payload["video_path"]
            log.info(f"📤 正在上传快手视频: {os.path.basename(video_path)}")
            if not await self._upload_video_semantic(page, video_path):
                return {
                    "verified": False,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": "Kuaishou video upload failed",
                }

            # 2. Wait for upload to complete
            if not await self._wait_for_upload_complete(
                page, timeout_ms=POST_UPLOAD_FORM_TIMEOUT_MS
            ):
                return {
                    "verified": False,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": "Kuaishou video upload timed out or failed",
                }

            # 3. Dismiss Joyride again (may appear after upload)
            await self._close_joyride_overlay(page)

            # 4. Fill description (includes title + desc + tags as caption)
            caption = payload.get("caption", payload.get("description", ""))
            if not await self._fill_description_semantic(page, caption):
                return {
                    "verified": False,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": "Kuaishou description fill failed",
                }

            # 5. Set cover
            cover_path = payload.get("cover_path", "")
            if cover_path:
                await self._set_cover_semantic(page, cover_path)

            # 6. Apply option controls from YAML config (for collection, visibility etc.)
            option_controls = self.selectors.get("option_controls", {})
            if option_controls:
                try:
                    await self._apply_option_controls(page, payload, option_controls)
                except Exception as exc:
                    log.warning(f"⚠️ 快手选项控制失败: {exc}")

            # 7. Preview pause (if BIYOU_PREVIEW is set)
            if not await self._check_preview_pause(page, "kuaishou"):
                return {
                    "verified": False,
                    "status": "preview",
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": "preview mode — publish skipped",
                }

            # 8. Click publish
            if not await self._click_publish_semantic(page):
                return {
                    "verified": False,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": "Kuaishou publish button not found or not clickable",
                }

            # 9. Wait for redirect to manage page
            try:
                await page.wait_for_url(
                    KUAISHOU_MANAGE_URL_PATTERN,
                    timeout=30_000,
                )
            except Exception:
                try:
                    current_url = str(getattr(page, "url", "") or "").strip()
                    current_body = await self._extract_body_text(page)
                    log.warning(
                        f"⚠️ 快手发布后未跳转到管理页，当前页面: {current_url}\n"
                        f"{current_body[:300]}"
                    )
                except Exception:
                    pass
                await page.wait_for_timeout(5000)

            # 10. Verify
            return await self._verify_publish_result(
                context.browser_context,
                payload,
                verify_page=page,
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
        payload = normalize_kuaishou_publish_payload(job)

        duration_error = build_kuaishou_duration_limit_error(job)
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
        last_error = outcome.get("last_error", "")
        if outcome.get("verified") is False and not last_error:
            last_error = "Kuaishou publish result could not be verified from video manager"

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
        payload = normalize_kuaishou_publish_payload(job)
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

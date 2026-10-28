import os
import asyncio
import re
from collections import deque
from typing import Optional

from core.publishing.adapters.automation_base import AutomationAdapterBase
from core.publishing.models import AdapterCapabilities, PublishJob
from core.utils.logger import log

POST_UPLOAD_FORM_TIMEOUT_MS = 600000  # 10 分钟，用于视频上传和转码
POST_UPLOAD_FORM_POLL_MS = 1000
VERIFY_TIMEOUT_MS = 180000  # 3 分钟，用于验证发布结果
VERIFY_POLL_MS = 5000
VERIFY_URL = "https://creator.xiaohongshu.com/new/note-manager"
UPLOAD_BLOCKING_TERMS = ["视频上传中"]
XIAOHONGSHU_COVER_PROCESSING_TERMS = ["封面上传中", "视频加载中"]
XIAOHONGSHU_COVER_MODAL_TERMS = ["获取封面建议", "上传图片"]
XIAOHONGSHU_COVER_FALLBACK_TERMS = ["回到旧版本", "旧版本"]
XIAOHONGSHU_TITLE_LIMIT = 20
XIAOHONGSHU_TITLE_MIN = 6
MANUAL_LOGIN_TERMS = ["请先登录", "登录后继续", "扫码登录", "手机号登录", "手机登录", "重新登录"]
MANUAL_VERIFICATION_TERMS = ["安全验证", "身份验证", "真人验证", "验证码", "异常访问", "风险提示"]
PUBLISH_FAILURE_TERMS = ["发布失败", "保存失败", "网络异常", "请求失败", "上传失败"]
NOTE_URL_RE = re.compile(r"/(?:explore|discovery/item)/([A-Za-z0-9_-]+)", re.IGNORECASE)
UPLOAD_PROGRESS_RE = re.compile(r"上传中\s*\d+%")
MANAGER_SUCCESS_TERMS = ["审核中", "已发布"]
XIAOHONGSHU_NOTE_ID_KEYS = ("noteId", "note_id", "itemId", "item_id", "noteid")
XIAOHONGSHU_TEXT_KEYS = (
    "title",
    "noteTitle",
    "displayTitle",
    "shortTitle",
    "desc",
    "description",
    "content",
    "text",
    "name",
)
XIAOHONGSHU_URL_KEYS = ("url", "link", "href", "jumpUrl", "noteUrl", "shareLink", "path")
XIAOHONGSHU_MANAGER_RESPONSE_URL_HINTS = (
    "/api/",
    "/apis/",
    "/web_api/",
    "/api/sns/",
    "/api/creator/",
)
XIAOHONGSHU_PUBLISH_RESPONSE_URL_HINTS = (
    "/web_api/sns/v2/note",
    "/api/sns/v2/note",
)


def _iter_xiaohongshu_strings(value):
    if isinstance(value, str):
        text = value.strip()
        if text:
            yield text
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_xiaohongshu_strings(item)
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_xiaohongshu_strings(item)


def _extract_xiaohongshu_note_refs(value):
    refs = []
    for text in _iter_xiaohongshu_strings(value):
        match = NOTE_URL_RE.search(text)
        if not match:
            continue
        note_id = match.group(1)
        refs.append((note_id, text))
    return refs


def _extract_xiaohongshu_manager_record(payload: dict, expected_title: str = "") -> dict:
    verify_terms = build_verify_terms(expected_title)
    queue = deque([payload])
    while queue:
        current = queue.popleft()
        if isinstance(current, list):
            queue.extend(current)
            continue
        if not isinstance(current, dict):
            continue

        title_texts = []
        for key in XIAOHONGSHU_TEXT_KEYS:
            if key not in current:
                continue
            for text in _iter_xiaohongshu_strings(current.get(key)):
                if text not in title_texts:
                    title_texts.append(text)

        status_texts = []
        for text in _iter_xiaohongshu_strings(current):
            if text not in status_texts:
                status_texts.append(text)

        note_refs = []
        for key in XIAOHONGSHU_URL_KEYS:
            if key not in current:
                continue
            for ref in _extract_xiaohongshu_note_refs(current.get(key)):
                if ref not in note_refs:
                    note_refs.append(ref)
        if not note_refs:
            for ref in _extract_xiaohongshu_note_refs(current):
                if ref not in note_refs:
                    note_refs.append(ref)

        direct_note_id = ""
        for key in XIAOHONGSHU_NOTE_ID_KEYS:
            value = str(current.get(key, "")).strip()
            if value:
                direct_note_id = value
                break
        if direct_note_id and not any(note_id == direct_note_id for note_id, _ in note_refs):
            note_refs.append((direct_note_id, f"https://www.xiaohongshu.com/explore/{direct_note_id}"))

        title_matches = any(
            term in text for text in title_texts for term in verify_terms
        )
        manager_success = any(
            term in text for text in status_texts for term in MANAGER_SUCCESS_TERMS
        )
        if title_matches and note_refs:
            note_id, note_url = note_refs[0]
            return {
                "platform_content_id": note_id,
                "platform_url": note_url,
                "matched_title": title_texts[0] if title_texts else "",
                "manager_success": manager_success,
            }
        if title_matches and manager_success:
            return {
                "platform_content_id": "",
                "platform_url": VERIFY_URL,
                "matched_title": title_texts[0] if title_texts else "",
                "manager_success": True,
            }

        queue.extend(value for value in current.values() if isinstance(value, (dict, list)))
    return {}


def _build_xiaohongshu_title(title: str, fallback_title: str = "") -> str:
    candidate = str(title or "").strip()[:XIAOHONGSHU_TITLE_LIMIT]
    if len(candidate) >= XIAOHONGSHU_TITLE_MIN:
        return candidate

    fallback = str(fallback_title or "").strip()[:XIAOHONGSHU_TITLE_LIMIT]
    if len(fallback) >= XIAOHONGSHU_TITLE_MIN:
        return fallback

    return fallback or candidate


def normalize_xiaohongshu_publish_payload(job: PublishJob) -> dict:
    payload = job.platform_payloads.get("xiaohongshu", {})
    extra_fields = {
        key: value
        for key, value in payload.items()
        if key not in {"title", "description", "tags", "video_path", "cover_path", "body"}
    }
    title_text = str(payload.get("title", job.title)).strip()
    description_text = str(payload.get("description", job.description)).strip()
    tags = payload.get("tags", job.tags)
    cover_path = str(payload.get("cover_path", job.cover_path)).strip()
    video_path = str(payload.get("video_path", job.video_path)).strip()
    hashtags = " ".join(f"#{tag}" for tag in tags[:5])
    body = "\n".join(part for part in [description_text, hashtags.strip()] if part).strip()
    title = _build_xiaohongshu_title(title_text, fallback_title=job.title)
    normalized = {
        "title": title,
        "body": body,
        "cover_path": cover_path,
        "video_path": video_path,
    }
    normalized.update(extra_fields)
    return normalized


def build_verify_terms(title: str) -> list[str]:
    raw_terms = [title.strip(), title.strip()[:12]]
    terms = []
    for term in raw_terms:
        if term and term not in terms:
            terms.append(term)
    return terms


def is_xiaohongshu_upload_blocking(body_text: str) -> bool:
    text = str(body_text or "")
    if "转码中，不影响发布" in text:
        return False
    if UPLOAD_PROGRESS_RE.search(text):
        return True
    if "取消上传" in text:
        return True
    if "剩余时间" in text:
        return True
    if "当前速度" in text:
        return True
    return False


def is_xiaohongshu_cover_processing(body_text: str) -> bool:
    text = str(body_text or "")
    return any(term in text for term in XIAOHONGSHU_COVER_PROCESSING_TERMS)


def is_xiaohongshu_cover_modal_open(body_text: str) -> bool:
    text = str(body_text or "")
    return "上传图片" in text and (
        "获取封面建议" in text or ("设置封面" in text and "取消" in text and "确定" in text)
    )


def extract_xiaohongshu_publish_response_outcome(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}

    success = payload.get("success")
    result_code = payload.get("result", payload.get("code", ""))
    error_message = (
        str(payload.get("msg", "")).strip()
        or str(payload.get("message", "")).strip()
        or str(payload.get("errmsg", "")).strip()
        or str(payload.get("error_msg", "")).strip()
    )
    need_retry = payload.get("need_retry")
    can_show_err = payload.get("can_show_err_msg_to_user")

    if success is True and not error_message:
        return {}
    if not error_message and success is not False and str(result_code).strip() in {"", "0"}:
        return {}

    error_parts = []
    if error_message:
        error_parts.append(error_message)
    result_text = str(result_code).strip()
    if result_text and result_text != "0":
        error_parts.append(f"code={result_text}")
    last_error = "Xiaohongshu publish rejected"
    if error_parts:
        last_error = f"{last_error}: {', '.join(error_parts)}"

    manual_required = need_retry is False or can_show_err is True
    status = "failed_manual" if manual_required else "failed_retryable"
    outcome = {
        "verified": False,
        "status": status,
        "verification_state": "failed",
        "last_error": last_error,
    }
    if manual_required:
        outcome["manual_required"] = True
    return outcome


class XiaohongshuAdapter(AutomationAdapterBase):
    platform_name = "xiaohongshu"
    capabilities = AdapterCapabilities(
        supports_native_schedule=False,
        supports_cover=True,
        supports_tags=False,
        supports_description=True,
    )

    def validate(self, job: PublishJob) -> None:
        if not job.video_path or not job.title:
            raise ValueError("Xiaohongshu publish requires video_path and title")

    def _build_outcome(
        self,
        *,
        verified: bool,
        status: str,
        verification_state: str,
        platform_content_id: str = "",
        platform_url: str = "",
        published_at: str = "",
        last_error: str = "",
        manual_required: bool = False,
        verification_source: str = "",
    ) -> dict:
        outcome = {
            "verified": verified,
            "status": status,
            "verification_state": verification_state,
            "platform_content_id": platform_content_id,
            "platform_url": platform_url,
            "published_at": published_at,
            "last_error": last_error,
        }
        if manual_required:
            outcome["manual_required"] = True
        if verification_source:
            outcome["verification_source"] = verification_source
        return outcome

    def _get_page_url(self, target) -> str:
        url = getattr(target, "url", "")
        if not isinstance(url, str):
            url = ""
        return str(url or "").strip()

    def _extract_note_ref(self, value: str) -> tuple[str, str]:
        text = str(value or "").strip()
        if not text:
            return "", ""
        match = NOTE_URL_RE.search(text)
        if not match:
            return "", ""
        return match.group(1), text

    def _matches_title(self, title: str, text: str) -> bool:
        haystack = str(text or "").strip()
        if not haystack:
            return False
        return any(term in haystack for term in build_verify_terms(title))

    def _detect_manual_reason(self, body_text: str, page_url: str) -> str:
        url = str(page_url or "").lower()
        if "/login" in url:
            return "Xiaohongshu publish requires login in creator center"

        text = str(body_text or "")
        if any(term in text for term in MANUAL_LOGIN_TERMS):
            return "Xiaohongshu publish requires login in creator center"
        if any(term in text for term in MANUAL_VERIFICATION_TERMS):
            return "Xiaohongshu publish requires manual security verification"
        return ""

    def _detect_publish_failure(self, body_text: str) -> str:
        text = str(body_text or "")
        if any(term in text for term in PUBLISH_FAILURE_TERMS):
            return "Xiaohongshu publish failed in creator center"
        return ""

    def _build_cover_result(
        self,
        *,
        requested: bool,
        selection_state: str = "",
        confirmation_state: str = "",
        apply_method: str = "",
        last_error: str = "",
    ) -> dict:
        return {
            "cover_requested": requested,
            "cover_selection_state": selection_state or ("not_requested" if not requested else "skipped"),
            "cover_confirmation_state": confirmation_state or ("not_requested" if not requested else "unconfirmed"),
            "cover_apply_method": apply_method,
            "cover_last_error": last_error,
        }

    def _merge_cover_result(self, outcome: dict, cover_result: dict) -> dict:
        merged = dict(outcome or {})
        normalized_cover = cover_result
        if isinstance(cover_result, str):
            if cover_result == "recommended":
                normalized_cover = self._build_cover_result(
                    requested=True,
                    selection_state="selected",
                    confirmation_state="confirmed",
                    apply_method="recommended_cover",
                )
            elif cover_result == "uploaded":
                normalized_cover = self._build_cover_result(
                    requested=True,
                    selection_state="selected",
                    confirmation_state="unconfirmed",
                    apply_method="uploaded_image",
                )
            else:
                normalized_cover = self._build_cover_result(requested=bool(cover_result))
        if isinstance(normalized_cover, dict):
            merged.update(normalized_cover)
        return merged

    def _is_xiaohongshu_manager_response(self, response) -> bool:
        url = str(getattr(response, "url", "") or "").lower()
        return "xiaohongshu.com" in url and any(
            hint in url for hint in XIAOHONGSHU_MANAGER_RESPONSE_URL_HINTS
        )

    def _is_xiaohongshu_publish_response(self, response) -> bool:
        url = str(getattr(response, "url", "") or "").lower()
        return "xiaohongshu.com" in url and any(
            hint in url for hint in XIAOHONGSHU_PUBLISH_RESPONSE_URL_HINTS
        )

    async def _capture_publish_response_outcome(self, page) -> Optional[dict]:
        try:
            async with page.expect_response(
                lambda response: self._is_xiaohongshu_publish_response(response),
                timeout=8000,
            ) as response_info:
                await self._click_first_force(
                    page,
                    self.selectors.get("locators", {}).get("publish_button", []),
                    timeout_ms=30000,
                    poll_ms=500,
                )
                response = await response_info.value
                payload = await response.json()
        except Exception:
            return None

        outcome = extract_xiaohongshu_publish_response_outcome(payload)
        if not outcome:
            return None
        outcome["verification_source"] = "publish_response"
        return outcome

    async def _fetch_note_manager_record(self, page, payload: dict) -> dict:
        try:
            async with page.expect_response(
                lambda response: self._is_xiaohongshu_manager_response(response),
                timeout=5000,
            ) as response_info:
                await page.goto(VERIFY_URL, wait_until="domcontentloaded")
                response = await response_info.value
                return _extract_xiaohongshu_manager_record(
                    await response.json(),
                    payload["title"],
                )
        except Exception:
            try:
                await page.goto(VERIFY_URL, wait_until="domcontentloaded")
            except Exception:
                pass
            return {}

    async def _extract_matching_note_link(self, target, title: str) -> tuple[str, str]:
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
            note_id, note_url = self._extract_note_ref(href)
            if not note_id:
                continue
            if self._matches_title(title, (link or {}).get("text", "")):
                return note_id, note_url
        return "", ""

    async def _inspect_post_publish_page(self, page, payload: dict) -> Optional[dict]:
        body_text = await self._extract_body_text(page)
        page_url = self._get_page_url(page)

        manual_reason = self._detect_manual_reason(body_text, page_url)
        if manual_reason:
            return self._build_outcome(
                verified=False,
                status="failed_manual",
                verification_state="manual",
                last_error=manual_reason,
                manual_required=True,
                verification_source="publish_page",
            )

        publish_failure = self._detect_publish_failure(body_text)
        if publish_failure:
            return self._build_outcome(
                verified=False,
                status="failed_retryable",
                verification_state="failed",
                last_error=publish_failure,
                verification_source="publish_page",
            )

        note_id, note_url = self._extract_note_ref(page_url)
        if note_id:
            return self._build_outcome(
                verified=True,
                status="published",
                verification_state="verified",
                platform_content_id=note_id,
                platform_url=note_url,
                verification_source="publish_page_url",
            )

        note_id, note_url = await self._extract_matching_note_link(page, payload["title"])
        if note_id:
            return self._build_outcome(
                verified=True,
                status="published",
                verification_state="verified",
                platform_content_id=note_id,
                platform_url=note_url,
                verification_source="publish_page_link",
            )
        return None

    async def _verify_publish_result(self, context, payload: dict) -> dict:
        verify_page = await context.new_page()
        result_verified = False
        try:
            elapsed = 0
            while True:
                manager_record = await self._fetch_note_manager_record(verify_page, payload)
                if manager_record.get("platform_content_id"):
                    result_verified = True
                    return self._build_outcome(
                        verified=True,
                        status="published",
                        verification_state="verified",
                        platform_content_id=manager_record.get("platform_content_id", ""),
                        platform_url=manager_record.get("platform_url", ""),
                        verification_source="note_manager_response",
                    )
                if manager_record.get("manager_success"):
                    result_verified = True
                    return self._build_outcome(
                        verified=True,
                        status="published",
                        verification_state="verified",
                        platform_content_id="",
                        platform_url=manager_record.get("platform_url", VERIFY_URL),
                        verification_source="note_manager_response_row",
                    )

                body_text = await self._extract_body_text(verify_page)
                manual_reason = self._detect_manual_reason(body_text, self._get_page_url(verify_page))
                if manual_reason:
                    return self._build_outcome(
                        verified=False,
                        status="failed_manual",
                        verification_state="manual",
                        last_error=manual_reason,
                        manual_required=True,
                        verification_source="note_manager",
                    )

                note_id, note_url = await self._extract_matching_note_link(
                    verify_page, payload["title"]
                )
                if note_id:
                    result_verified = True
                    return self._build_outcome(
                        verified=True,
                        status="published",
                        verification_state="verified",
                        platform_content_id=note_id,
                        platform_url=note_url,
                        verification_source="note_manager_link",
                    )

                if self._matches_title(payload["title"], body_text) and any(
                    term in body_text for term in MANAGER_SUCCESS_TERMS
                ):
                    result_verified = True
                    return self._build_outcome(
                        verified=True,
                        status="published",
                        verification_state="verified",
                        platform_content_id="",
                        platform_url=VERIFY_URL,
                        verification_source="note_manager_row",
                    )

                if elapsed >= VERIFY_TIMEOUT_MS:
                    break
                await verify_page.wait_for_timeout(VERIFY_POLL_MS)
                elapsed += VERIFY_POLL_MS

            return self._build_outcome(
                verified=False,
                status="failed_retryable",
                verification_state="unknown",
                last_error="Xiaohongshu publish result could not be verified from note manager",
                verification_source="note_manager",
            )
        finally:
            if not result_verified:
                try:
                    await self._log_verification_context(verify_page, "xiaohongshu", payload.get("title", ""))
                except Exception:
                    pass
            await verify_page.close()

    async def _wait_for_upload_ready(self, page) -> str:
        elapsed = 0
        body_text = ""
        while elapsed <= POST_UPLOAD_FORM_TIMEOUT_MS:
            body_text = await self._extract_body_text(page)
            if not is_xiaohongshu_upload_blocking(body_text):
                return body_text
            await page.wait_for_timeout(POST_UPLOAD_FORM_POLL_MS)
            elapsed += POST_UPLOAD_FORM_POLL_MS
        return body_text

    async def _apply_cover(self, page, payload: dict, locators: dict) -> dict:
        cover_path = str(payload.get("cover_path", "")).strip()
        if not cover_path:
            return self._build_cover_result(requested=False)

        upload_selectors = locators.get("cover_upload", [])
        if cover_path and upload_selectors:
            try:
                log.info("📸 正在设置小红书封面...")
                activate_selectors = locators.get("cover_activate", [])
                if activate_selectors:
                    await self._click_first(page, activate_selectors, timeout_ms=5000)
                    await page.wait_for_timeout(2000)

                await self._set_file_first(page, upload_selectors, cover_path)
                log.info("⏳ 封面已选择，等待渲染并确认...")
                cover_body = await self._wait_for_text_to_clear(
                    page,
                    XIAOHONGSHU_COVER_PROCESSING_TERMS,
                    timeout_ms=30000,
                    poll_ms=500,
                )

                if is_xiaohongshu_cover_processing(cover_body):
                    fallback_clicked = await self._js_click_text(
                        page,
                        XIAOHONGSHU_COVER_FALLBACK_TERMS,
                    )
                    if fallback_clicked:
                        await page.wait_for_timeout(2000)
                        await self._set_file_first(page, upload_selectors, cover_path)
                        cover_body = await self._wait_for_text_to_clear(
                            page,
                            XIAOHONGSHU_COVER_PROCESSING_TERMS,
                            timeout_ms=30000,
                            poll_ms=500,
                        )

                if is_xiaohongshu_cover_processing(cover_body):
                    raise RuntimeError(
                        "Xiaohongshu cover editor is still processing uploaded cover"
                    )

                confirm_selectors = locators.get("cover_confirm", [])
                confirmed = False
                if confirm_selectors:
                    try:
                        await self._click_first_force(
                            page,
                            confirm_selectors,
                            timeout_ms=3000,
                            poll_ms=250,
                        )
                        confirmed = True
                    except RuntimeError:
                        confirmed = False
                if not confirmed:
                    clicked = await self._js_click_text(page, ["确定", "确认", "完成"])
                    if not clicked:
                        raise RuntimeError("Unable to confirm Xiaohongshu uploaded cover")

                cover_body = await self._wait_for_text_to_clear(
                    page,
                    XIAOHONGSHU_COVER_MODAL_TERMS,
                    timeout_ms=5000,
                    poll_ms=250,
                )
                if is_xiaohongshu_cover_modal_open(cover_body):
                    raise RuntimeError("Xiaohongshu cover editor did not close after confirm")

                log.info("✅ 小红书封面确认成功")
                return self._build_cover_result(
                    requested=True,
                    selection_state="selected",
                    confirmation_state="confirmed",
                    apply_method="uploaded_image",
                )
            except Exception as exc:
                log.warning(f"⚠️ Xiaohongshu specific cover apply failed: {exc}")
                upload_error = str(exc)
            else:
                upload_error = ""
        else:
            upload_error = ""

        recommended_selectors = locators.get("recommended_cover", [])
        if recommended_selectors:
            try:
                await self._click_first_force(
                    page,
                    recommended_selectors,
                    timeout_ms=5000,
                    poll_ms=250,
                )
                applied_terms = locators.get("recommended_cover_applied_text", [])
                if applied_terms:
                    await self._wait_for_text(
                        page,
                        applied_terms,
                        timeout_ms=5000,
                        poll_ms=250,
                    )
                confirm_selectors = locators.get("cover_confirm", [])
                confirmed = False
                if confirm_selectors:
                    try:
                        await self._click_first_force(
                            page,
                            confirm_selectors,
                            timeout_ms=3000,
                            poll_ms=250,
                        )
                        confirmed = True
                    except RuntimeError:
                        confirmed = False
                if not confirmed:
                    clicked = await self._js_click_text(page, ["确定", "确认", "完成"])
                    if not clicked:
                        raise RuntimeError("Unable to confirm Xiaohongshu recommended cover")
                cover_body = await self._wait_for_text_to_clear(
                    page,
                    XIAOHONGSHU_COVER_MODAL_TERMS,
                    timeout_ms=5000,
                    poll_ms=250,
                )
                if is_xiaohongshu_cover_modal_open(cover_body):
                    raise RuntimeError(
                        "Xiaohongshu cover editor did not close after recommended cover selection"
                    )
                return self._build_cover_result(
                    requested=True,
                    selection_state="selected",
                    confirmation_state="confirmed",
                    apply_method="recommended_cover",
                )
            except RuntimeError as exc:
                log.warning(f"⚠️ Xiaohongshu recommended cover selection skipped: {exc}")

        return self._build_cover_result(
            requested=True,
            selection_state="failed",
            confirmation_state="failed",
            last_error=upload_error,
        )

    async def _publish_async(self, payload: dict) -> dict:
        publish_url = self.selectors.get("publish_url", "")
        locators = self.selectors.get("locators", {})
        playwright = context = None
        try:
            playwright, context, page = await self._open_page(publish_url)
            video_path = payload["video_path"]
            log.info(f"📤 正在上传小红书视频: {os.path.basename(video_path)}")
            await page.wait_for_timeout(3000)
            await self._set_file_first(page, locators.get("video_upload", []), video_path)

            await self._wait_for_any_selector(
                page,
                locators.get("title", []) + locators.get("description", []),
                timeout_ms=POST_UPLOAD_FORM_TIMEOUT_MS,
                poll_ms=POST_UPLOAD_FORM_POLL_MS,
            )

            await self._fill_first(page, locators.get("title", []), payload["title"])
            await self._fill_first(page, locators.get("description", []), payload["body"])
            await self._apply_option_controls(
                page,
                payload,
                self.selectors.get("option_controls", {}),
            )

            cover_result = await self._apply_cover(page, payload, locators)

            body_text = await self._wait_for_upload_ready(page)
            if is_xiaohongshu_upload_blocking(body_text):
                return self._merge_cover_result({
                    "verified": False,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": "Xiaohongshu publish is still processing upload/transcode",
                }, cover_result)
            if not await self._check_preview_pause(page, "xiaohongshu"):
                return self._merge_cover_result({"verified": False, "status": "preview", "platform_content_id": "", "platform_url": "", "published_at": "", "last_error": "preview mode — publish skipped"}, cover_result)
            publish_response_outcome = await self._capture_publish_response_outcome(page)
            if publish_response_outcome is not None:
                return self._merge_cover_result(publish_response_outcome, cover_result)
            await page.wait_for_timeout(5000)
            post_publish_outcome = await self._inspect_post_publish_page(page, payload)
            current_url = self._get_page_url(page).rstrip("/")
            expected_publish_url = str(publish_url or "").rstrip("/")
            if (
                post_publish_outcome is None
                and expected_publish_url
                and current_url == expected_publish_url
            ):
                await self._click_first_force(
                    page,
                    locators.get("publish_button", []),
                    timeout_ms=10000,
                    poll_ms=250,
                )
                await page.wait_for_timeout(5000)
                post_publish_outcome = await self._inspect_post_publish_page(page, payload)
            if post_publish_outcome is not None:
                return self._merge_cover_result(post_publish_outcome, cover_result)
            return self._merge_cover_result(
                await self._verify_publish_result(context.browser_context, payload),
                cover_result,
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

    def publish(self, job: PublishJob) -> dict:
        self.validate(job)
        payload = normalize_xiaohongshu_publish_payload(job)
        outcome = self.run_async(self._publish_async(payload))
        last_error = outcome.get("last_error", "")
        resolved_content_id = str(outcome.get("platform_content_id", "")).strip()
        resolved_platform_url = str(outcome.get("platform_url", "")).strip()
        verified = outcome.get("verified") is True or (
            not last_error and (resolved_content_id or resolved_platform_url)
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
        for key in (
            "cover_requested",
            "cover_selection_state",
            "cover_confirmation_state",
            "cover_apply_method",
            "cover_last_error",
        ):
            if key in outcome:
                extra[key] = outcome.get(key)

        return {
            "platform": self.platform_name,
            "status": status,
            "platform_content_id": resolved_content_id,
            "platform_url": resolved_platform_url,
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

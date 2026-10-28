from core.utils.logger import log
import os
import asyncio
import re
from collections import deque
from datetime import datetime

from core.publishing.adapters.automation_base import AutomationAdapterBase
from core.publishing.models import AdapterCapabilities, PublishJob
from core.utils.logger import log

POST_UPLOAD_FORM_TIMEOUT_MS = 600000  # 10 分钟，用于视频上传和转码
POST_UPLOAD_FORM_POLL_MS = 1000
VERIFY_TIMEOUT_MS = 180000  # 3 分钟，用于验证发布结果
VERIFY_POLL_MS = 5000
VERIFY_URL = "https://channels.weixin.qq.com/platform/post/list"
UPLOAD_BLOCKING_TERMS = ["上传中", "处理中"]
WECHAT_SHORT_TITLE_LIMIT = 16
WECHAT_SHORT_TITLE_MIN = 5
WECHAT_TITLE_VALIDATION_ERRORS = ["标题包含特殊字符", "请填写短标题", "标题字数"]
WECHAT_MANUAL_PERMISSION_TERMS = ["你还不能发表视频", "当前登录账号不是视频号", "管理员或运营者"]
WECHAT_MANUAL_ADMIN_VERIFICATION_TERMS = ["管理员本人验证", "扫码验证"]
WECHAT_MANUAL_LOGIN_TERMS = ["请先登录", "登录后继续", "扫码登录"]
WECHAT_MANUAL_SECURITY_TERMS = ["安全验证", "风险提示", "异常访问"]


def sanitize_wechat_short_title(title: str) -> str:
    sanitized = (
        title.replace(",", " ")
        .replace("，", " ")
        .replace("、", " ")
        .replace("；", " ")
        .replace(";", " ")
        .replace("。", " ")
        .replace("！", " ")
        .replace("!", " ")
        .replace("（", " ")
        .replace("）", " ")
        .replace("(", " ")
        .replace(")", " ")
    )
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    return sanitized[:WECHAT_SHORT_TITLE_LIMIT].strip()


def build_wechat_short_title(title: str, fallback_title: str = "") -> str:
    candidate = sanitize_wechat_short_title(title)
    if len(candidate) >= WECHAT_SHORT_TITLE_MIN:
        return candidate

    fallback = sanitize_wechat_short_title(fallback_title)
    if len(fallback) >= WECHAT_SHORT_TITLE_MIN:
        return fallback

    return fallback or candidate


def build_wechat_verify_terms(title: str) -> list[str]:
    candidate = sanitize_wechat_short_title(title)
    if not candidate:
        return []
    terms = [candidate]
    for length in (max(8, len(candidate) - 1), max(8, len(candidate) - 2)):
        prefix = candidate[:length].strip()
        if prefix and prefix not in terms:
            terms.append(prefix)
    return terms


def normalize_wechat_publish_payload(job: PublishJob) -> dict:
    payload = job.platform_payloads.get("wechat_channels", {})
    extra_fields = {
        key: value
        for key, value in payload.items()
        if key not in {"title", "description", "tags", "video_path", "cover_path"}
    }
    title = str(payload.get("title", job.title)).strip()
    description_text = str(payload.get("description", job.description)).strip()
    tags = payload.get("tags", job.tags)
    video_path = str(payload.get("video_path", job.video_path)).strip()
    cover_path = str(payload.get("cover_path", job.cover_path)).strip()
    hashtags = " ".join(f"#{tag}" for tag in tags[:5])
    description_parts = [description_text, hashtags.strip()]
    description = "\n".join(part for part in description_parts if part).strip()
    normalized = {
        "title": build_wechat_short_title(title, fallback_title=job.title),
        "description": description,
        "video_path": video_path,
        "cover_path": cover_path,
    }
    normalized.update(extra_fields)
    return normalized


def format_wechat_publish_time(raw_timestamp) -> str:
    if not raw_timestamp:
        return ""
    try:
        return datetime.fromtimestamp(int(raw_timestamp)).astimezone().isoformat()
    except Exception:
        return ""


def _extract_wechat_object_id(item: dict) -> str:
    if not isinstance(item, dict):
        return ""
    return (
        str(item.get("objectId", "")).strip()
        or str(item.get("postId", "")).strip()
        or str(item.get("exportId", "")).strip()
    )


def _iter_wechat_text_values(value):
    if isinstance(value, str):
        text = value.strip()
        if text:
            yield text
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_wechat_text_values(item)
        return
    if isinstance(value, dict):
        for key in ("shortTitle", "title", "desc", "description", "objectDesc", "content"):
            if key in value:
                yield from _iter_wechat_text_values(value.get(key))


def _extract_wechat_title_candidates(item: dict) -> list[str]:
    if not isinstance(item, dict):
        return []
    candidates = []
    for key in ("shortTitle", "title", "desc", "description", "objectDesc", "content"):
        for text in _iter_wechat_text_values(item.get(key)):
            if text and text not in candidates:
                candidates.append(text)
    return candidates


def extract_wechat_post_list_snapshot(payload: dict, expected_title: str = "") -> dict:
    queue = deque([payload])
    items = []
    while queue:
        current = queue.popleft()
        if isinstance(current, dict):
            if _extract_wechat_object_id(current):
                items.append(current)
            for value in current.values():
                if isinstance(value, (dict, list)):
                    queue.append(value)
        elif isinstance(current, list):
            for value in current:
                if isinstance(value, (dict, list)):
                    queue.append(value)

    verify_terms = build_wechat_verify_terms(expected_title)
    object_ids = []
    titles = []
    matched_object_id = ""
    matched_create_time = ""
    newest_item = items[0] if items else {}
    if not isinstance(newest_item, dict):
        newest_item = {}

    for item in items:
        object_id = _extract_wechat_object_id(item)
        if object_id and object_id not in object_ids:
            object_ids.append(object_id)
        title_candidates = _extract_wechat_title_candidates(item)
        for title_text in title_candidates:
            if title_text not in titles:
                titles.append(title_text)
        if (
            not matched_object_id
            and object_id
            and title_candidates
            and any(
                term in title_text
                for title_text in title_candidates
                for term in verify_terms
            )
        ):
            matched_object_id = object_id
            matched_create_time = item.get("createTime", "") or item.get("create_time", "")

    return {
        "count": len(items),
        "object_ids": object_ids,
        "titles": titles,
        "newest_object_id": _extract_wechat_object_id(newest_item),
        "newest_create_time": newest_item.get("createTime", "") or newest_item.get("create_time", ""),
        "matched_object_id": matched_object_id,
        "matched_create_time": matched_create_time,
    }


def extract_wechat_validation_error(body_text: str) -> str:
    for term in WECHAT_TITLE_VALIDATION_ERRORS:
        if term in body_text:
            return term
    return ""


def extract_wechat_manual_error(body_text: str) -> str:
    text = str(body_text or "")
    if any(term in text for term in WECHAT_MANUAL_PERMISSION_TERMS):
        return "WeChat Channels publish requires manager/operator permission for the target channel"
    if any(term in text for term in WECHAT_MANUAL_ADMIN_VERIFICATION_TERMS):
        return "WeChat Channels publish requires manual admin QR verification"
    if any(term in text for term in WECHAT_MANUAL_LOGIN_TERMS):
        return "WeChat Channels publish requires login in creator center"
    if any(term in text for term in WECHAT_MANUAL_SECURITY_TERMS):
        return "WeChat Channels publish requires manual security verification"
    return ""


class WechatChannelsAdapter(AutomationAdapterBase):
    platform_name = "wechat_channels"
    capabilities = AdapterCapabilities(
        supports_native_schedule=False,
        supports_cover=True,
        supports_tags=False,
        supports_description=True,
    )

    def validate(self, job: PublishJob) -> None:
        if not job.video_path or not job.title:
            raise ValueError("WeChat Channels publish requires video_path and title")

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
        merged.update(cover_result or {})
        return merged

    async def _inspect_page_outcome(self, page) -> dict | None:
        page_url = str(getattr(page, "url", "") or "").strip().lower()
        body_text = await self._extract_body_text(page)

        manual_error = extract_wechat_manual_error(body_text)
        if not manual_error and "/login" in page_url:
            manual_error = "WeChat Channels publish requires login in creator center"
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

        return None

    async def _resolve_frame_or_page_outcome(self, page, frame_name, *, timeout_ms=10000, poll_ms=250):
        try:
            return await self._resolve_frame(page, frame_name, timeout_ms=timeout_ms, poll_ms=poll_ms)
        except RuntimeError as exc:
            if "Unable to locate frame:" not in str(exc):
                raise
            page_outcome = await self._inspect_page_outcome(page)
            if page_outcome is not None:
                return page_outcome
            raise

    async def _fetch_post_list_snapshot(
        self,
        context,
        frame_name: str,
        expected_title: str = "",
        *,
        verify_page=None,
    ) -> dict:
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
                    await verify_page.goto(VERIFY_URL, wait_until="domcontentloaded")
                response = await response_info.value
                payload = await response.json()
            except Exception:
                try:
                    await verify_page.goto(VERIFY_URL, wait_until="domcontentloaded")
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
                    "platform_url": VERIFY_URL,
                }
            snapshot = extract_wechat_post_list_snapshot(payload, expected_title)
            snapshot["platform_url"] = VERIFY_URL
            return snapshot
        finally:
            if owned_page is not None:
                await verify_page.close()

    async def _verify_publish_result(self, context, baseline_snapshot: dict, expected_title: str = "") -> dict:
        baseline_ids = set(baseline_snapshot.get("object_ids") or [])
        elapsed = 0
        verify_page = await context.new_page()
        result_verified = False
        try:
            while elapsed <= VERIFY_TIMEOUT_MS:
                snapshot = await self._fetch_post_list_snapshot(
                    context,
                    "",
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
                        "platform_url": snapshot.get("platform_url", VERIFY_URL),
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

    async def _publish_async(self, payload: dict) -> dict:
        publish_url = self.selectors.get("publish_url", "")
        frame_name = str(self.selectors.get("frame_name", "")).strip()
        locators = self.selectors.get("locators", {})
        playwright = context = None
        try:
            playwright, context, page = await self._open_page(publish_url)
            baseline_snapshot = await self._fetch_post_list_snapshot(
                context.browser_context,
                frame_name,
                payload["title"],
            )
            cover_path = str(payload.get("cover_path", "")).strip()
            cover_result = self._build_cover_result(
                requested=bool(cover_path),
            )

            log.info(f"📤 正在上传视频号视频: {os.path.basename(payload['video_path'])}")
            await page.wait_for_timeout(3000)

            target = await self._resolve_frame_or_page_outcome(
                page,
                frame_name,
                timeout_ms=10000,
                poll_ms=250,
            )
            if isinstance(target, dict):
                return self._merge_cover_result(target, cover_result)
            await self._set_file_first(target, locators.get("video_upload", []), payload["video_path"])
            target = await self._resolve_frame_or_page_outcome(
                page,
                frame_name,
                timeout_ms=10000,
                poll_ms=250,
            )
            if isinstance(target, dict):
                return self._merge_cover_result(target, cover_result)

            await self._wait_for_any_selector(
                target,
                locators.get("title", []) + locators.get("description", []),
                timeout_ms=POST_UPLOAD_FORM_TIMEOUT_MS,
                poll_ms=POST_UPLOAD_FORM_POLL_MS,
            )
            target = await self._resolve_frame_or_page_outcome(
                page,
                frame_name,
                timeout_ms=10000,
                poll_ms=250,
            )
            if isinstance(target, dict):
                return self._merge_cover_result(target, cover_result)

            # 2. 精细化封面处理
            if cover_path and os.path.exists(cover_path):
                try:
                    log.info("📸 正在设置视频号封面...")
                    # 点击“更换封面”或“添加封面”
                    await self._click_first(target, locators.get("cover_activate", []), timeout_ms=5000)
                    await page.wait_for_timeout(2000)
                    # 重新解析 Frame
                    target = await self._resolve_frame_or_page_outcome(page, frame_name, timeout_ms=10000)
                    if isinstance(target, dict):
                        return self._merge_cover_result(target, cover_result)

                    # 某些版本需要点击“上传图片”按钮才显示 input
                    await self._js_click_text(target, ["上传图片", "本地上传"])
                    await page.wait_for_timeout(1000)

                    # 上传文件
                    await self._set_file_first(target, locators.get("cover_upload", []), cover_path)
                    log.info("⏳ 封面已选择，等待渲染并确认...")
                    await page.wait_for_timeout(5000)

                    # 点击确认按钮 (通常叫 “完成”)
                    clicked = await self._js_click_text(target, ["完成", "确定", "确认"])
                    if not clicked:
                        await self._click_first(target, locators.get("cover_confirm", []), timeout_ms=3000)

                    confirmation_state = "unconfirmed"
                    confirmed_terms = locators.get("cover_confirmed_text", [])
                    if confirmed_terms:
                        await self._wait_for_text(
                            target,
                            confirmed_terms,
                            timeout_ms=5000,
                            poll_ms=250,
                        )
                        confirmation_state = "confirmed"

                    await page.wait_for_timeout(2000)
                    log.info("✅ 视频号封面确认成功")
                    cover_result = self._build_cover_result(
                        requested=True,
                        selection_state="selected",
                        confirmation_state=confirmation_state,
                        apply_method="uploaded_image",
                    )
                    target = await self._resolve_frame_or_page_outcome(page, frame_name, timeout_ms=10000)
                    if isinstance(target, dict):
                        return self._merge_cover_result(target, cover_result)
                except Exception as exc:
                    log.warning(f"⚠️ WeChat Channels specific cover apply failed: {exc}")
                    cover_result = self._build_cover_result(
                        requested=True,
                        selection_state="failed",
                        confirmation_state="failed",
                        apply_method="uploaded_image",
                        last_error=str(exc),
                    )
            elif cover_path:
                cover_result = self._build_cover_result(
                    requested=True,
                    selection_state="failed",
                    confirmation_state="failed",
                    apply_method="uploaded_image",
                    last_error=f"WeChat Channels cover path not found: {cover_path}",
                )

            await self._fill_first(target, locators.get("title", []), payload["title"])
            await self._fill_first(target, locators.get("description", []), payload["description"])
            target = await self._resolve_frame_or_page_outcome(
                page,
                frame_name,
                timeout_ms=10000,
                poll_ms=250,
            )
            if isinstance(target, dict):
                return self._merge_cover_result(target, cover_result)
            await self._apply_option_controls(
                target,
                payload,
                self.selectors.get("option_controls", {}),
            )
            body_text = await self._wait_for_text_to_clear(
                target,
                UPLOAD_BLOCKING_TERMS,
                timeout_ms=POST_UPLOAD_FORM_TIMEOUT_MS,
                poll_ms=POST_UPLOAD_FORM_POLL_MS,
            )
            if any(term in body_text for term in UPLOAD_BLOCKING_TERMS):
                return self._merge_cover_result({
                    "verified": False,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": "WeChat Channels publish is still processing uploaded media",
                }, cover_result)
            validation_error = extract_wechat_validation_error(body_text)
            if validation_error:
                return self._merge_cover_result({
                    "verified": False,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": f"WeChat Channels validation failed: {validation_error}",
                }, cover_result)
            manual_error = extract_wechat_manual_error(body_text)
            if manual_error:
                return self._merge_cover_result({
                    "verified": False,
                    "status": "failed_manual",
                    "manual_required": True,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": manual_error,
                }, cover_result)
            target = await self._resolve_frame_or_page_outcome(
                page,
                frame_name,
                timeout_ms=10000,
                poll_ms=250,
            )
            if isinstance(target, dict):
                return self._merge_cover_result(target, cover_result)
            if not await self._check_preview_pause(page, "wechat_channels"):
                return self._merge_cover_result({"verified": False, "status": "preview", "platform_content_id": "", "platform_url": "", "published_at": "", "last_error": "preview mode — publish skipped"}, cover_result)
            await self._click_first(target, locators.get("publish_button", []))
            await page.wait_for_timeout(5000)
            target = await self._resolve_frame_or_page_outcome(
                page,
                frame_name,
                timeout_ms=10000,
                poll_ms=250,
            )
            if isinstance(target, dict):
                return self._merge_cover_result(target, cover_result)
            post_click_body = await self._extract_body_text(target)
            validation_error = extract_wechat_validation_error(post_click_body)
            if validation_error:
                return self._merge_cover_result({
                    "verified": False,
                    "status": "failed_manual",
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": f"WeChat Channels validation failed: {validation_error}",
                }, cover_result)
            manual_error = extract_wechat_manual_error(post_click_body)
            if manual_error:
                return self._merge_cover_result({
                    "verified": False,
                    "status": "failed_manual",
                    "manual_required": True,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": manual_error,
                }, cover_result)
            return self._merge_cover_result(
                await self._verify_publish_result(
                    context.browser_context,
                    baseline_snapshot,
                    payload["title"],
                ),
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
        extra = dict(payload)
        for key in (
            "verification_source",
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
            "platform_content_id": outcome.get("platform_content_id", ""),
            "platform_url": outcome.get("platform_url", ""),
            "native_schedule": False,
            "scheduled_by": "biyou",
            "scheduled_for": "",
            "published_at": outcome.get("published_at", ""),
            "last_error": last_error,
            "extra": extra,
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

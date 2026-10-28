import asyncio
import re

from core.publishing.adapters.automation_base import AutomationAdapterBase
from core.publishing.adapters.base import BasePublishingAdapter
from core.publishing.adapters.douyin import resolve_video_duration_seconds
from core.publishing.models import AdapterCapabilities, PublishJob
from core.services.kuaishou_api import KuaishouApiClient
from core.utils.logger import log

POST_UPLOAD_FORM_TIMEOUT_MS = 600000  # 10 分钟，用于视频上传和转码
POST_UPLOAD_FORM_POLL_MS = 1000
VERIFY_TIMEOUT_MS = 180000  # 3 分钟，用于验证发布结果
VERIFY_POLL_MS = 5000
VERIFY_URL = "https://cp.kuaishou.com/article/manage/video"
VERIFY_DOM_TIMEOUT_MS = 15000
UPLOAD_BLOCKING_TERMS = ["上传中", "处理中"]
VALIDATION_ERROR_TERMS = ["超过字数限制"]
MANAGER_SUCCESS_TERMS = ["审核中", "已发布"]
KUAISHOU_DESCRIPTION_LIMIT = 460
KUAISHOU_TAG_LIMIT = 4
KUAISHOU_MAX_DURATION_SECONDS = 3600
MANAGER_ROW_PUBLISHED_AT_RE = re.compile(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}")
KUAISHOU_CONTENT_ID_PATTERNS = (
    r"/short-video/([A-Za-z0-9_-]+)",
    r"/(?:fw/)?photo/([A-Za-z0-9_-]+)",
    r"[?&](?:photoId|photo_id|id)=([A-Za-z0-9_-]+)",
)
KUAISHOU_BARE_CONTENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,}$")


def _strip_markdown_links(text: str) -> str:
    return re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)


def _strip_raw_urls(text: str) -> str:
    return re.sub(r"https?://\S+", "", text)


def _strip_trailing_hashtag_block(text: str) -> str:
    return re.sub(r"(?:\n|\s)*(?:#\S+\s*)+$", "", text).strip()


def _collapse_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _normalize_kuaishou_description(description: str) -> str:
    cleaned = description.strip()
    cleaned = _strip_markdown_links(cleaned)
    cleaned = _strip_raw_urls(cleaned)
    cleaned = _strip_trailing_hashtag_block(cleaned)
    cleaned = _collapse_blank_lines(cleaned)
    return cleaned


def build_kuaishou_caption(title: str, description: str, tags: list[str]) -> str:
    hashtags = " ".join(f"#{tag}" for tag in tags[:KUAISHOU_TAG_LIMIT])
    parts = [
        title.strip(),
        _normalize_kuaishou_description(description),
        hashtags.strip(),
    ]
    return "\n".join(part for part in parts if part).strip()[:KUAISHOU_DESCRIPTION_LIMIT]


def build_verify_terms(title: str) -> list[str]:
    raw_terms = [title.strip(), title.strip()[:12]]
    terms = []
    for term in raw_terms:
        if term and term not in terms:
            terms.append(term)
    return terms


def _format_duration(seconds: int) -> str:
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def build_kuaishou_duration_limit_error(job: PublishJob) -> str:
    duration_seconds = resolve_video_duration_seconds(job, platform="kuaishou")
    if duration_seconds is None or duration_seconds <= KUAISHOU_MAX_DURATION_SECONDS:
        return ""
    return (
        "Kuaishou creator center accepts videos up to 60:00; "
        f"current video is {_format_duration(duration_seconds)} ({duration_seconds}s)"
    )


def _extract_manager_row_published_at(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = MANAGER_ROW_PUBLISHED_AT_RE.search(text)
    if not match:
        return ""
    return match.group(0)


def extract_kuaishou_platform_content_id(value: str) -> str:
    text = str(value or "").strip()
    for pattern in KUAISHOU_CONTENT_ID_PATTERNS:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    if KUAISHOU_BARE_CONTENT_ID_RE.fullmatch(text):
        return text
    return ""


def build_kuaishou_platform_url(platform_content_id: str, raw_url: str = "") -> str:
    href = str(raw_url or "").strip()
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"https://www.kuaishou.com{href}"

    if href.startswith("http://") or href.startswith("https://"):
        if "cp.kuaishou.com" in href and platform_content_id:
            return f"https://www.kuaishou.com/short-video/{platform_content_id}"
        return href

    if platform_content_id:
        return f"https://www.kuaishou.com/short-video/{platform_content_id}"
    return ""


class KuaishouAdapter(BasePublishingAdapter):
    platform_name = "kuaishou"
    stability_tier = "stable"
    execution_mode = "api"
    capabilities = AdapterCapabilities(
        supports_native_schedule=False,
        supports_cover=True,
        supports_tags=False,
        supports_description=True,
    )

    def __init__(self, client: KuaishouApiClient):
        self.client = client

    def validate(self, job: PublishJob) -> None:
        if not job.video_path or not job.cover_path:
            raise ValueError("Kuaishou publish requires video_path and cover_path")

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
        upload_data = self.client.start_upload()
        created = self.client.publish_photo(
            caption=payload["caption"],
            video_path=payload["video_path"],
            cover_path=payload["cover_path"],
            upload_token=upload_data["upload_token"],
        )
        return {
            "platform": self.platform_name,
            "status": "published",
            "platform_content_id": str(created.get("photo_id", "")),
            "platform_url": "",
            "native_schedule": False,
            "scheduled_by": "biyou",
            "scheduled_for": "",
            "published_at": "",
            "last_error": "",
            "extra": {"upload_token": upload_data["upload_token"]},
        }

    def schedule(self, job: PublishJob, publish_at: str) -> dict:
        raise NotImplementedError(
            "Kuaishou uses BiYou-managed scheduling in phase 1"
        )


def normalize_kuaishou_publish_payload(job: PublishJob) -> dict:
    payload = job.platform_payloads.get("kuaishou", {})
    extra_fields = {
        key: value
        for key, value in payload.items()
        if key not in {"title", "description", "tags", "video_path", "cover_path", "caption"}
    }
    title = str(payload.get("title", job.title)).strip()
    description = str(payload.get("description", job.description)).strip()
    tags = payload.get("tags", job.tags)
    video_path = str(payload.get("video_path", job.video_path)).strip()
    cover_path = str(payload.get("cover_path", job.cover_path)).strip()
    caption = build_kuaishou_caption(title, description, tags)
    normalized = {
        "title": title,
        "description": caption,
        "caption": caption,
        "video_path": video_path,
        "cover_path": cover_path,
    }
    normalized.update(extra_fields)
    return normalized


class KuaishouAutomationAdapter(AutomationAdapterBase):
    platform_name = "kuaishou"
    stability_tier = "beta"
    capabilities = AdapterCapabilities(
        supports_native_schedule=False,
        supports_cover=True,
        supports_tags=False,
        supports_description=True,
    )

    def validate(self, job: PublishJob) -> None:
        if not job.video_path or not job.cover_path or not job.title:
            raise ValueError(
                "Kuaishou publish requires video_path, cover_path, and title"
            )

    def _iter_targets(self, page) -> list:
        targets = [page]
        for frame in getattr(page, "frames", []) or []:
            if frame is not None and frame not in targets:
                targets.append(frame)
        return targets

    async def _wait_for_any_selector_across_targets(
        self,
        page,
        selectors,
        timeout_ms=15000,
        poll_ms=250,
    ):
        last_error = None
        for target in self._iter_targets(page):
            try:
                matched_selector = await self._wait_for_any_selector(
                    target,
                    selectors,
                    timeout_ms=timeout_ms,
                    poll_ms=poll_ms,
                )
                return target, matched_selector
            except RuntimeError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Unable to locate any selector from: {selectors}")

    async def _fill_first_across_targets(self, page, selectors, value):
        last_error = None
        for target in self._iter_targets(page):
            try:
                await self._fill_first(target, selectors, value)
                return target
            except RuntimeError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Unable to locate fill target from selectors: {selectors}")

    async def _click_first_across_targets(self, page, selectors):
        last_error = None
        for target in self._iter_targets(page):
            try:
                await self._click_first(target, selectors)
                return target
            except RuntimeError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Unable to click any selector from: {selectors}")

    async def _apply_option_controls_across_targets(
        self,
        page,
        payload,
        option_controls,
        active_target=None,
    ):
        active_target = active_target or page
        if not isinstance(option_controls, dict) or not option_controls:
            return active_target

        for field_name, control in option_controls.items():
            if not isinstance(control, dict):
                continue
            if field_name not in payload:
                continue
            value = payload.get(field_name)
            if value in (None, "", []):
                continue

            last_error = None
            candidate_targets = []
            if active_target is not None:
                candidate_targets.append(active_target)
            for target in self._iter_targets(page):
                if target not in candidate_targets:
                    candidate_targets.append(target)

            for target in candidate_targets:
                try:
                    await self._apply_option_control(target, field_name, value, control)
                    active_target = target
                    last_error = None
                    break
                except RuntimeError as exc:
                    last_error = exc

            if last_error is not None:
                required = control.get("required", True)
                if required is False:
                    log.warning(f"⚠️ Optional control '{field_name}' skipped: {last_error}")
                else:
                    raise last_error

        return active_target

    async def _verify_publish_result(self, context, payload: dict) -> dict:
        verify_page = await context.new_page()
        result_verified = False
        try:
            await verify_page.goto(VERIFY_URL, wait_until="domcontentloaded")
            manager_row = await self._wait_for_matching_manager_row(
                verify_page,
                payload["title"],
                timeout_ms=VERIFY_DOM_TIMEOUT_MS,
                poll_ms=VERIFY_POLL_MS,
            )
            if manager_row:
                result_verified = True
                return {
                    "verified": True,
                    "platform_content_id": manager_row.get("platform_content_id", ""),
                    "platform_url": manager_row.get("platform_url", "") or VERIFY_URL,
                    "published_at": manager_row.get("published_at", ""),
                    "last_error": "",
                }
            body_text = await self._wait_for_text(
                verify_page,
                build_verify_terms(payload["title"]),
                timeout_ms=VERIFY_TIMEOUT_MS,
                poll_ms=VERIFY_POLL_MS,
            )
            if body_text and any(term in body_text for term in MANAGER_SUCCESS_TERMS):
                result_verified = True
                return {
                    "verified": True,
                    "platform_content_id": "",
                    "platform_url": VERIFY_URL,
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
                    await self._log_verification_context(verify_page, "kuaishou", payload.get("title", ""))
                except Exception:
                    pass
            await verify_page.close()

    async def _wait_for_matching_manager_row(
        self,
        target,
        title: str,
        timeout_ms=VERIFY_DOM_TIMEOUT_MS,
        poll_ms=VERIFY_POLL_MS,
    ) -> dict:
        elapsed = 0
        while elapsed <= timeout_ms:
            row = await self._extract_matching_manager_row(target, title)
            if row:
                return row
            await target.wait_for_timeout(poll_ms)
            elapsed += poll_ms
        return {}

    async def _extract_matching_manager_row(self, target, title: str) -> dict:
        rows = await target.evaluate(
            """() => {
                const clean = (value) => String(value || '').replace(/\\u00A0/g, ' ').trim();
                const isVisible = (el) => {
                    if (!el) {
                        return false;
                    }
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                };

                const statusPattern = /(审核中|已发布|待发布|未通过)/;
                const extractId = (value) => {
                    const raw = String(value || '').trim();
                    if (!raw) {
                        return '';
                    }
                    const patterns = [
                        /\\/short-video\\/([A-Za-z0-9_-]+)/,
                        /\\/(?:fw\\/)?photo\\/([A-Za-z0-9_-]+)/,
                        /[?&](?:photoId|photo_id|id)=([A-Za-z0-9_-]+)/,
                    ];
                    for (const pattern of patterns) {
                        const match = raw.match(pattern);
                        if (match) {
                            return match[1];
                        }
                    }
                    return '';
                };
                const toAbsoluteUrl = (href, platformContentId) => {
                    const raw = String(href || '').trim();
                    if (raw.startsWith('//')) {
                        return `https:${raw}`;
                    }
                    if (raw.startsWith('/')) {
                        return `https://www.kuaishou.com${raw}`;
                    }
                    if (raw.startsWith('http://') || raw.startsWith('https://')) {
                        if (raw.includes('cp.kuaishou.com') && platformContentId) {
                            return `https://www.kuaishou.com/short-video/${platformContentId}`;
                        }
                        return raw;
                    }
                    if (platformContentId) {
                        return `https://www.kuaishou.com/short-video/${platformContentId}`;
                    }
                    return '';
                };
                return Array.from(document.querySelectorAll('.video-item, [class*="video-item"]'))
                    .filter((row) => isVisible(row))
                    .map((row) => {
                        const text = clean(row.innerText || row.textContent || '');
                        const titleNode =
                            row.querySelector('.video-item__detail__row__title') ||
                            row.querySelector('[class*="__title"]');
                        const title = clean(titleNode?.innerText || titleNode?.textContent || '');
                        const statusNode = Array.from(row.querySelectorAll('*')).find((el) => {
                            if (!isVisible(el)) {
                                return false;
                            }
                            return statusPattern.test(clean(el.innerText || el.textContent || ''));
                        });
                        const status = clean(statusNode?.innerText || statusNode?.textContent || '');
                        const hrefValues = Array.from(row.querySelectorAll('a[href]'))
                            .map((link) => link.getAttribute('href') || link.href || '')
                            .filter(Boolean);
                        const attributeNames = [
                            'data-photo-id',
                            'data-photoid',
                            'data-video-id',
                            'data-work-id',
                            'data-id',
                        ];
                        let platformContentId = '';
                        for (const href of hrefValues) {
                            platformContentId = extractId(href);
                            if (platformContentId) {
                                break;
                            }
                        }
                        if (!platformContentId) {
                            for (const attrName of attributeNames) {
                                const attrValue = row.getAttribute(attrName) || titleNode?.getAttribute?.(attrName) || '';
                                platformContentId = extractId(attrValue) || clean(attrValue);
                                if (platformContentId) {
                                    break;
                                }
                            }
                        }
                        const rawUrl = hrefValues[0] || '';
                        return {
                            title,
                            status,
                            text,
                            platform_content_id: platformContentId,
                            platform_url: toAbsoluteUrl(rawUrl, platformContentId),
                        };
                    })
                    .filter((row) => row.text);
            }"""
        )
        terms = build_verify_terms(title)
        for row in rows or []:
            title_text = str((row or {}).get("title", "")).strip()
            status_text = str((row or {}).get("status", "")).strip()
            row_text = str((row or {}).get("text", "")).strip()
            platform_content_id = extract_kuaishou_platform_content_id(
                (row or {}).get("platform_content_id", "")
            ) or extract_kuaishou_platform_content_id((row or {}).get("platform_url", ""))
            platform_url = build_kuaishou_platform_url(
                platform_content_id,
                (row or {}).get("platform_url", ""),
            )
            haystack = "\n".join(part for part in [title_text, row_text] if part).strip()
            if not any(term in haystack for term in terms):
                continue
            status_haystack = "\n".join(part for part in [status_text, row_text] if part).strip()
            if not any(term in status_haystack for term in MANAGER_SUCCESS_TERMS):
                continue
            return {
                "title": title_text,
                "status": status_text,
                "text": row_text,
                "published_at": _extract_manager_row_published_at(status_haystack),
                "platform_content_id": platform_content_id,
                "platform_url": platform_url,
            }
        return {}

    async def _publish_async(self, payload: dict) -> dict:
        publish_url = self.selectors.get("publish_url", "")
        locators = self.selectors.get("locators", {})
        playwright = context = None
        try:
            playwright, context, page = await self._open_page(publish_url)
            dismiss_draft_selectors = locators.get("dismiss_draft", [])
            if dismiss_draft_selectors:
                try:
                    await self._click_first(
                        page,
                        dismiss_draft_selectors,
                        timeout_ms=5000,
                        poll_ms=250,
                    )
                    await page.wait_for_timeout(1000)
                except RuntimeError:
                    pass

            upload_entry_selectors = locators.get("upload_entry", [])
            if upload_entry_selectors:
                try:
                    await self._click_first(
                        page,
                        upload_entry_selectors,
                        timeout_ms=5000,
                        poll_ms=250,
                    )
                except RuntimeError:
                    pass

            title_selectors = locators.get("title", [])
            description_selectors = locators.get("description", [])
            cover_selectors = locators.get("cover_upload", [])
            form_ready_selectors = (
                locators.get("form_ready", [])
                or (title_selectors + description_selectors)
                or locators.get("publish_button", [])
            )
            description_activate_selectors = locators.get("description_activate", [])
            await self._set_file_first(
                page,
                locators.get("video_upload", []),
                payload["video_path"],
            )
            active_target, _ = await self._wait_for_any_selector_across_targets(
                page,
                form_ready_selectors,
                timeout_ms=POST_UPLOAD_FORM_TIMEOUT_MS,
                poll_ms=POST_UPLOAD_FORM_POLL_MS,
            )
            if cover_selectors:
                try:
                    cover_activate_selectors = locators.get("cover_activate", [])
                    cover_target = active_target
                    if cover_activate_selectors:
                        try:
                            cover_target = await self._click_first_across_targets(
                                page,
                                cover_activate_selectors,
                            )
                            await cover_target.wait_for_timeout(1000)
                        except RuntimeError:
                            cover_target = active_target
                    await self._set_file_first(
                        cover_target,
                        cover_selectors,
                        payload["cover_path"],
                    )
                except RuntimeError as exc:
                    log.warning(f"⚠️ Kuaishou cover upload skipped: {exc}")
            if title_selectors:
                active_target = await self._fill_first_across_targets(
                    page,
                    title_selectors,
                    payload["title"],
                )
            if description_activate_selectors:
                try:
                    active_target = await self._click_first_across_targets(
                        page,
                        description_activate_selectors,
                    )
                    await active_target.wait_for_timeout(500)
                except RuntimeError:
                    pass
            active_target = await self._fill_first_across_targets(
                page,
                description_selectors,
                payload["caption"],
            )
            active_target = await self._apply_option_controls_across_targets(
                page,
                payload,
                self.selectors.get("option_controls", {}),
                active_target=active_target,
            )
            body_text = await self._wait_for_text_to_clear(
                active_target,
                UPLOAD_BLOCKING_TERMS,
                timeout_ms=POST_UPLOAD_FORM_TIMEOUT_MS,
                poll_ms=POST_UPLOAD_FORM_POLL_MS,
            )
            if any(term in body_text for term in UPLOAD_BLOCKING_TERMS):
                return {
                    "verified": False,
                    "status": "failed_retryable",
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": "Kuaishou publish is still processing uploaded media",
                }
            if any(term in body_text for term in VALIDATION_ERROR_TERMS):
                return {
                    "verified": False,
                    "status": "failed_manual",
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": "Kuaishou publish blocked by page validation",
                }
            if not await self._check_preview_pause(page, "kuaishou"):
                return {"verified": False, "status": "preview", "platform_content_id": "", "platform_url": "", "published_at": "", "last_error": "preview mode — publish skipped"}
            await self._click_first_across_targets(page, locators.get("publish_button", []))
            await page.wait_for_timeout(5000)
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
        if outcome.get("manual_required"):
            last_error = last_error or "manual intervention required"
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

from core.utils.logger import log
import os
import asyncio
import json
import re
import subprocess
from collections import deque
from datetime import datetime

from core.publishing.adapters.automation_base import AutomationAdapterBase
from core.publishing.adapters.base import BasePublishingAdapter
from core.publishing.models import AdapterCapabilities, PublishJob
from core.services.douyin_api import DouyinApiClient
from core.utils.logger import log

POST_UPLOAD_FORM_TIMEOUT_MS = 600000  # 10 分钟，用于视频上传和转码
POST_UPLOAD_FORM_POLL_MS = 1000
VERIFY_TIMEOUT_MS = 180000  # 3 分钟，用于验证发布结果
VERIFY_POLL_MS = 5000
VERIFY_URL = "https://creator.douyin.com/creator-micro/content/manage"
DOUYIN_MAX_DURATION_SECONDS = 3600
SMS_VERIFICATION_TERMS = ["短信验证码", "选择其他验证方式", "获取验证码", "接收短信验证码"]
MANUAL_BLOCKING_TERMS = [
    "安全验证",
    "身份验证",
    "扫码登录",
    "登录失效",
    "验证后继续",
    "验证账号",
]
UPLOAD_BLOCKING_TERMS = ["上传中", "处理中"]
DOUYIN_TITLE_LIMIT = 30
DOUYIN_DESCRIPTION_LIMIT = 1000
DOUYIN_MANAGER_SUCCESS_TERMS = ("审核中", "已发布")
PLATFORM_CONTENT_ID_PATTERNS = [
    r"/video/(\d+)",
    r"[?&](?:item_id|modal_id|group_id)=(\d+)",
    r"\b(\d{10,25})\b",
]


def build_douyin_text(title: str, description: str, tags: list[str]) -> str:
    hashtags = " ".join(f"#{tag}" for tag in tags[:5])
    lines = [title.strip(), description.strip(), hashtags.strip()]
    return "\n".join(line for line in lines if line).strip()


def normalize_douyin_publish_payload(job: PublishJob) -> dict:
    payload = job.platform_payloads.get("douyin", {})
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
    description = "\n".join(
        part for part in [description_text, hashtags.strip()] if part
    ).strip()
    normalized = {
        "title": title[:DOUYIN_TITLE_LIMIT],
        "description": description[:DOUYIN_DESCRIPTION_LIMIT],
        "video_path": video_path,
        "cover_path": cover_path,
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


def _coerce_duration_seconds(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return seconds


def _format_duration(seconds: int) -> str:
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _read_sidecar_duration_seconds(video_path: str) -> int | None:
    if not video_path:
        return None
    base, _ = os.path.splitext(video_path)
    info_path = f"{base}.info.json"
    if not os.path.exists(info_path):
        return None
    try:
        with open(info_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return None
    return _coerce_duration_seconds(payload.get("duration"))


def _probe_video_duration_seconds(video_path: str) -> int | None:
    if not video_path or not os.path.exists(video_path):
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                video_path,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return _coerce_duration_seconds(result.stdout.strip())


def resolve_video_duration_seconds(job: PublishJob, platform: str = "") -> int | None:
    platform_key = str(platform or "").strip()
    platform_video_path = str(
        (job.platform_payloads.get(platform_key, {}) or {}).get("video_path", "")
    ).strip()
    if platform_video_path:
        duration_seconds = _read_sidecar_duration_seconds(platform_video_path)
        if duration_seconds is not None:
            return duration_seconds
        duration_seconds = _probe_video_duration_seconds(platform_video_path)
        if duration_seconds is not None:
            return duration_seconds

    duration_seconds = _coerce_duration_seconds((job.metadata or {}).get("duration"))
    if duration_seconds is not None:
        return duration_seconds

    duration_seconds = _read_sidecar_duration_seconds(job.video_path)
    if duration_seconds is not None:
        return duration_seconds
    return _probe_video_duration_seconds(job.video_path)


def build_douyin_duration_limit_error(job: PublishJob) -> str:
    duration_seconds = resolve_video_duration_seconds(job, platform="douyin")
    if duration_seconds is None or duration_seconds <= DOUYIN_MAX_DURATION_SECONDS:
        return ""
    return (
        "Douyin creator center accepts videos up to 60:00; "
        f"current video is {_format_duration(duration_seconds)} ({duration_seconds}s)"
    )


def extract_platform_content_id(value: str) -> str:
    text = str(value or "").strip()
    for pattern in PLATFORM_CONTENT_ID_PATTERNS:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def build_platform_url(platform_content_id: str, raw_url: str = "") -> str:
    href = str(raw_url or "").strip()
    if href.startswith("//"):
        return f"https:{href}"
    if href.startswith("/"):
        return f"https://www.douyin.com{href}"
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if platform_content_id:
        return f"https://www.douyin.com/video/{platform_content_id}"
    return ""


def detect_manual_block_error(body_text: str) -> str:
    text = str(body_text or "").strip()
    if not text:
        return ""
    if any(term in text for term in SMS_VERIFICATION_TERMS):
        return "Douyin publish requires SMS verification in creator center"
    if any(term in text for term in MANUAL_BLOCKING_TERMS):
        return "Douyin publish requires manual confirmation in creator center"
    return ""


def format_douyin_published_at(raw_timestamp) -> str:
    if not raw_timestamp:
        return ""
    try:
        return datetime.fromtimestamp(int(raw_timestamp)).astimezone().isoformat()
    except Exception:
        return ""


def extract_work_list_record(payload: dict, title: str) -> dict:
    terms = build_verify_terms(title)
    queue = deque([payload])
    candidates = []
    while queue:
        current = queue.popleft()
        if isinstance(current, dict):
            values = list(current.values())
            if any(key in current for key in ("aweme_id", "group_id", "item_id", "desc", "title")):
                candidates.append(current)
            for value in values:
                if isinstance(value, (dict, list)):
                    queue.append(value)
        elif isinstance(current, list):
            for value in current:
                if isinstance(value, (dict, list)):
                    queue.append(value)

    for item in candidates:
        if not isinstance(item, dict):
            continue
        desc = str(item.get("desc", "")).strip()
        title_text = str(item.get("title", "")).strip()
        haystack = "\n".join(part for part in [desc, title_text] if part).strip()
        if not any(term in haystack for term in terms):
            continue
        platform_content_id = str(item.get("aweme_id", "")).strip() or str(
            item.get("group_id", "")
        ).strip() or str(
            item.get("item_id", "")
        ).strip()
        if not platform_content_id:
            continue
        return {
            "platform_content_id": platform_content_id,
            "platform_url": build_platform_url(platform_content_id, ""),
            "published_at": format_douyin_published_at(item.get("create_time")),
        }
    return {}


class DouyinAdapter(BasePublishingAdapter):
    platform_name = "douyin"
    stability_tier = "stable"
    execution_mode = "api"
    capabilities = AdapterCapabilities(
        supports_native_schedule=False,
        supports_cover=False,
        supports_tags=False,
        supports_description=True,
    )

    def __init__(self, client: DouyinApiClient | None = None):
        self.client = client or DouyinApiClient(access_token="")

    def validate(self, job: PublishJob) -> None:
        if not job.video_path:
            raise ValueError("Douyin publish requires video_path")

    def publish(self, job: PublishJob) -> dict:
        duration_error = build_douyin_duration_limit_error(job)
        payload = job.platform_payloads.get("douyin", {})
        title = str(payload.get("title", job.title)).strip()
        description = str(payload.get("description", job.description)).strip()
        tags = payload.get("tags", job.tags)
        video_path = str(payload.get("video_path", job.video_path)).strip()
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
                "extra": {"video_path": video_path, "title": title},
            }
        uploaded = self.client.upload_video(video_path)
        created = self.client.create_video(
            video_id=uploaded["video_id"],
            text=build_douyin_text(title, description, tags),
        )
        return {
            "platform": self.platform_name,
            "status": "published",
            "platform_content_id": created["item_id"],
            "platform_url": "",
            "native_schedule": False,
            "scheduled_by": "biyou",
            "scheduled_for": "",
            "published_at": "",
            "last_error": "",
            "extra": {"video_id": uploaded["video_id"]},
        }

    def schedule(self, job: PublishJob, publish_at: str) -> dict:
        raise NotImplementedError("Douyin uses BiYou-managed scheduling in phase 1")


class DouyinAutomationAdapter(AutomationAdapterBase, BasePublishingAdapter):
    platform_name = "douyin"
    stability_tier = "beta"
    execution_mode = "automation"
    capabilities = AdapterCapabilities(
        supports_native_schedule=False,
        supports_cover=True,
        supports_tags=False,
        supports_description=True,
    )

    def validate(self, job: PublishJob) -> None:
        if not job.video_path or not job.cover_path or not job.title:
            raise ValueError("Douyin publish requires video_path, cover_path, and title")

    def _is_publish_modal_intercept_error(self, error: Exception) -> bool:
        message = str(error or "")
        if "intercepts pointer events" not in message:
            return False
        return (
            "semi-modal" in message
            or "dy-creator-content-modal" in message
            or "dy-creator-content-portal" in message
            or "role=\"modal\"" in message
            or "role='modal'" in message
        )

    async def _dismiss_publish_intercept_modal(self, page, locators: dict) -> bool:
        confirm_selectors = locators.get("publish_modal_confirm", [])
        close_selectors = locators.get("publish_modal_close", [])

        if confirm_selectors:
            try:
                await self._click_first(
                    page,
                    confirm_selectors,
                    timeout_ms=5000,
                    poll_ms=250,
                )
                return True
            except Exception:
                pass

        if close_selectors:
            try:
                await self._click_first(
                    page,
                    close_selectors,
                    timeout_ms=5000,
                    poll_ms=250,
                )
                return True
            except Exception:
                pass

        return False

    async def _click_publish_button(self, page, locators: dict) -> None:
        publish_selectors = locators.get("publish_button", [])
        try:
            await self._click_first(page, publish_selectors)
            return
        except Exception as exc:
            if not self._is_publish_modal_intercept_error(exc):
                raise

            resolved = await self._dismiss_publish_intercept_modal(page, locators)
            if not resolved:
                raise

            await page.wait_for_timeout(1000)
            await self._click_first(page, publish_selectors)

    async def _extract_backend_record(self, page, payload: dict) -> dict:
        result = await page.evaluate(
            """(expectedTerms) => {
                const terms = (expectedTerms || [])
                    .map((term) => String(term || "").trim())
                    .filter(Boolean);
                if (!terms.length) {
                    return {};
                }

                const normalize = (value) =>
                    String(value || "").replace(/\\s+/g, " ").trim();

                const extractId = (value) => {
                    const text = String(value || "");
                    const patterns = [
                        /\\/video\\/(\\d+)/,
                        /[?&](?:item_id|modal_id|group_id)=(\\d+)/,
                        /\\b(\\d{10,25})\\b/,
                    ];
                    for (const pattern of patterns) {
                        const match = text.match(pattern);
                        if (match) {
                            return match[1];
                        }
                    }
                    return "";
                };

                const toAbsoluteUrl = (href, platformContentId) => {
                    const raw = String(href || "").trim();
                    if (raw.startsWith("//")) {
                        return `https:${raw}`;
                    }
                    if (raw.startsWith("/")) {
                        return `https://www.douyin.com${raw}`;
                    }
                    if (raw.startsWith("http://") || raw.startsWith("https://")) {
                        return raw;
                    }
                    if (platformContentId) {
                        return `https://www.douyin.com/video/${platformContentId}`;
                    }
                    return "";
                };

                const getPublishedAt = (text) => {
                    const raw = normalize(text);
                    if (!raw) {
                        return "";
                    }
                    const patterns = [
                        /(20\\d{2}[-/.]\\d{1,2}[-/.]\\d{1,2}\\s+\\d{1,2}:\\d{2}(?::\\d{2})?)/,
                        /(\\d{1,2}[-/.]\\d{1,2}\\s+\\d{1,2}:\\d{2})/,
                    ];
                    for (const pattern of patterns) {
                        const match = raw.match(pattern);
                        if (match) {
                            return match[1];
                        }
                    }
                    return "";
                };

                const attributeNames = [
                    "data-row-key",
                    "data-item-id",
                    "data-aweme-id",
                    "data-id",
                    "data-video-id",
                ];

                const buildCandidate = (node) => {
                    if (!node) {
                        return null;
                    }

                    const row =
                        node.closest(
                            "[data-row-key], [role='row'], tr, li, article, section, [class*='row'], [class*='item'], [class*='card']"
                        ) || node;
                    const text = normalize(row.innerText || node.innerText || "");
                    const matchedTerm = terms.find((term) => text.includes(term));
                    if (!matchedTerm) {
                        return null;
                    }

                    const hrefValues = [];
                    if (typeof row.querySelectorAll === "function") {
                        for (const link of row.querySelectorAll("a[href]")) {
                            hrefValues.push(link.getAttribute("href") || link.href || "");
                        }
                    }
                    if (typeof node.getAttribute === "function") {
                        hrefValues.unshift(node.getAttribute("href") || "");
                    }

                    let platformContentId = "";
                    for (const href of hrefValues) {
                        platformContentId = extractId(href);
                        if (platformContentId) {
                            break;
                        }
                    }

                    if (!platformContentId) {
                        for (const attrName of attributeNames) {
                            const attrValue =
                                (typeof row.getAttribute === "function" && row.getAttribute(attrName)) ||
                                (typeof node.getAttribute === "function" && node.getAttribute(attrName)) ||
                                "";
                            platformContentId = extractId(attrValue);
                            if (platformContentId) {
                                break;
                            }
                        }
                    }

                    const rawUrl = hrefValues.find(Boolean) || "";
                    return {
                        matched_term: matchedTerm,
                        row_text: text,
                        platform_content_id: platformContentId,
                        platform_url: toAbsoluteUrl(rawUrl, platformContentId),
                        published_at: getPublishedAt(text),
                    };
                };

                const selectorGroups = [
                    "a[href*='/video/']",
                    "a[href*='item_id=']",
                    "a[href*='modal_id=']",
                    "[data-row-key]",
                    "[data-item-id]",
                    "[data-aweme-id]",
                    "[data-id]",
                    "[role='row']",
                    "tr",
                    "li",
                    "article",
                ];

                const candidates = [];
                const seen = new Set();
                for (const selector of selectorGroups) {
                    for (const node of document.querySelectorAll(selector)) {
                        if (seen.has(node)) {
                            continue;
                        }
                        seen.add(node);
                        const candidate = buildCandidate(node);
                        if (candidate && candidate.row_text) {
                            candidates.push(candidate);
                        }
                    }
                }

                candidates.sort((left, right) => {
                    const leftScore = (left.platform_content_id ? 2 : 0) + (left.platform_url ? 1 : 0);
                    const rightScore = (right.platform_content_id ? 2 : 0) + (right.platform_url ? 1 : 0);
                    return rightScore - leftScore;
                });

                return candidates[0] || {};
            }""",
            build_verify_terms(payload["title"]),
        )
        if not isinstance(result, dict):
            return {}

        platform_content_id = extract_platform_content_id(result.get("platform_content_id", ""))
        if not platform_content_id:
            platform_content_id = extract_platform_content_id(result.get("platform_url", ""))
        platform_url = build_platform_url(
            platform_content_id,
            result.get("platform_url", ""),
        )
        return {
            "platform_content_id": platform_content_id,
            "platform_url": platform_url,
            "published_at": str(result.get("published_at", "")).strip(),
            "matched_term": str(result.get("matched_term", "")).strip(),
            "row_text": str(result.get("row_text", "")).strip(),
        }

    async def _wait_for_backend_record(self, page, payload: dict, timeout_ms=30000, poll_ms=1000) -> dict:
        elapsed = 0
        while elapsed <= timeout_ms:
            record = await self._extract_backend_record(page, payload)
            if record.get("platform_content_id"):
                return record
            await page.wait_for_timeout(poll_ms)
            elapsed += poll_ms
        return {}

    async def _fetch_work_list_record(self, page, payload: dict) -> dict:
        try:
            async with page.expect_response(
                lambda response: "/work_list" in response.url
            ) as response_info:
                await page.goto(VERIFY_URL, wait_until="domcontentloaded")
            response = await response_info.value
            return extract_work_list_record(await response.json(), payload["title"])
        except Exception:
            return {}

    async def _poll_work_list_record(self, page, payload: dict, timeout_ms=30000, poll_ms=1000) -> dict:
        elapsed = 0
        while elapsed <= timeout_ms:
            record = await self._fetch_work_list_record(page, payload)
            if record.get("platform_content_id"):
                return record
            if elapsed >= timeout_ms:
                break
            await asyncio.sleep(poll_ms / 1000)
            elapsed += poll_ms
        return {}

    async def _verify_publish_result(self, context, payload: dict) -> dict:
        verify_page = await context.new_page()
        result_verified = False
        try:
            await verify_page.goto(VERIFY_URL, wait_until="domcontentloaded")
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
            work_list_record = await self._fetch_work_list_record(verify_page, payload)
            if work_list_record.get("platform_content_id"):
                result_verified = True
                return {
                    "verified": True,
                    "platform_content_id": work_list_record.get("platform_content_id", ""),
                    "platform_url": work_list_record.get("platform_url", ""),
                    "published_at": work_list_record.get("published_at", ""),
                    "last_error": "",
                }
            body_text = await self._wait_for_text(
                verify_page,
                build_verify_terms(payload["title"]),
                timeout_ms=VERIFY_TIMEOUT_MS,
                poll_ms=VERIFY_POLL_MS,
            )
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
            if body_text:
                creator_center_row_record = {}
                if "审核中" in body_text:
                    creator_center_row_record = await self._wait_for_backend_record(
                        verify_page,
                        payload,
                        timeout_ms=VERIFY_TIMEOUT_MS,
                        poll_ms=VERIFY_POLL_MS,
                    )
                    if creator_center_row_record.get("platform_content_id"):
                        return {
                            "verified": True,
                            "platform_content_id": creator_center_row_record.get("platform_content_id", ""),
                            "platform_url": creator_center_row_record.get("platform_url", ""),
                            "published_at": creator_center_row_record.get("published_at", ""),
                            "last_error": "",
                        }
                    if creator_center_row_record.get("matched_term") and any(
                        term in creator_center_row_record.get("row_text", "")
                        for term in DOUYIN_MANAGER_SUCCESS_TERMS
                    ):
                        return {
                            "verified": True,
                            "platform_content_id": "",
                            "platform_url": creator_center_row_record.get("platform_url", "") or VERIFY_URL,
                            "published_at": creator_center_row_record.get("published_at", ""),
                            "last_error": "",
                            "verification_source": "creator_center_row",
                        }
                work_list_record = await self._poll_work_list_record(
                    verify_page,
                    payload,
                    timeout_ms=VERIFY_TIMEOUT_MS,
                    poll_ms=VERIFY_POLL_MS,
                )
                if work_list_record.get("platform_content_id"):
                    return {
                        "verified": True,
                        "platform_content_id": work_list_record.get("platform_content_id", ""),
                        "platform_url": work_list_record.get("platform_url", ""),
                        "published_at": work_list_record.get("published_at", ""),
                        "last_error": "",
                    }
                record = await self._wait_for_backend_record(
                    verify_page,
                    payload,
                    timeout_ms=VERIFY_TIMEOUT_MS,
                    poll_ms=VERIFY_POLL_MS,
                )
                if record.get("platform_content_id"):
                    return {
                        "verified": True,
                        "platform_content_id": record.get("platform_content_id", ""),
                        "platform_url": record.get("platform_url", ""),
                        "published_at": record.get("published_at", ""),
                        "last_error": "",
                    }
                if record.get("matched_term") and any(
                    term in record.get("row_text", "") for term in DOUYIN_MANAGER_SUCCESS_TERMS
                ):
                    return {
                        "verified": True,
                        "platform_content_id": "",
                        "platform_url": record.get("platform_url", "") or VERIFY_URL,
                        "published_at": record.get("published_at", ""),
                        "last_error": "",
                        "verification_source": "creator_center_row",
                    }
                latest_body = await self._extract_body_text(verify_page)
                block_error = detect_manual_block_error(latest_body)
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
                    "last_error": "Douyin publish result could not be verified from creator center",
                }
            return {
                "verified": False,
                "platform_content_id": "",
                "platform_url": "",
                "published_at": "",
                "last_error": "Douyin publish result could not be verified from creator center",
            }
        finally:
            if not result_verified:
                try:
                    await self._log_verification_context(verify_page, "douyin", payload.get("title", ""))
                except Exception:
                    pass
            await verify_page.close()

    async def _publish_async(self, payload: dict) -> dict:
        publish_url = self.selectors.get("publish_url", "")
        locators = self.selectors.get("locators", {})
        playwright = context = None
        try:
            playwright, context, page = await self._open_page(publish_url)

            # 强化视频上传：使用触发模式
            video_path = payload["video_path"]
            log.info(f"📤 正在上传抖音视频: {os.path.basename(video_path)}")
            await page.wait_for_timeout(3000)

            await self._set_file_first(page, locators.get("video_upload", []), video_path)
            try:
                await self._wait_for_any_selector(
                    page,
                    locators.get("title", []) + locators.get("description", []),
                    timeout_ms=POST_UPLOAD_FORM_TIMEOUT_MS,
                    poll_ms=POST_UPLOAD_FORM_POLL_MS,
                )
            except RuntimeError:
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
                last_error = "Douyin upload form did not appear after file selection"
                if "视频时长60分钟以内" in body_text and "上传视频" in body_text:
                    last_error = (
                        "Douyin upload form did not appear after file selection; "
                        "page may still be on the upload gate or the video violates creator-center limits"
                    )
                return {
                    "verified": False,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": last_error,
                }
            # 1. 先填写标题和描述，增加页面稳定性
            await self._fill_first(page, locators.get("title", []), payload["title"])
            await self._fill_first(
                page,
                locators.get("description", []),
                payload["description"],
            )

            # 2. 精细化封面处理
            cover_path = payload.get("cover_path")
            if cover_path and os.path.exists(cover_path):
                try:
                    log.info("📸 正在设置抖音封面...")
                    # 点击“设置封面”
                    cover_activate_selectors = locators.get("cover_activate", [])
                    if cover_activate_selectors:
                        await self._click_first(page, cover_activate_selectors, timeout_ms=5000)
                        await page.wait_for_timeout(2000)

                    # 尝试点击“上传图片”标签
                    await self._js_click_text(page, ["上传图片"])
                    await page.wait_for_timeout(1000)

                    # 上传文件
                    await self._set_file_first(page, locators.get("cover_upload", []), cover_path)
                    log.info("⏳ 封面已选择，等待渲染并确认...")
                    await page.wait_for_timeout(5000) # 强制等待预览和按钮启用

                    # 使用暴力 JS 点击“确定”
                    clicked = await self._js_click_text(page, ["确定", "确认"])
                    if not clicked:
                        # 兜底：尝试常规点击
                        cover_confirm_selectors = locators.get("cover_confirm", [])
                        if cover_confirm_selectors:
                            await self._click_first(page, cover_confirm_selectors, timeout_ms=3000)

                    await page.wait_for_timeout(2000)
                    log.info("✅ 抖音封面确认成功")
                    # Dismiss any lingering cover preview modal
                    try:
                        modal_close = page.locator("div.dy-creator-content-modal-wrap button:has-text('关闭'), div.dy-creator-content-modal-wrap [class*='close'], div.semi-modal-wrap [class*='close']")
                        if await modal_close.count() > 0:
                            await modal_close.first.click(force=True)
                            await page.wait_for_timeout(1000)
                        else:
                            # Try clicking outside the modal or pressing Escape to dismiss
                            await page.keyboard.press("Escape")
                            await page.wait_for_timeout(1000)
                    except Exception:
                        try:
                            await page.keyboard.press("Escape")
                            await page.wait_for_timeout(1000)
                        except Exception:
                            pass
                except Exception as exc:
                    log.warning(f"⚠️ Douyin specific cover apply failed: {exc}")

            await self._apply_option_controls(
                page,
                payload,
                self.selectors.get("option_controls", {}),
            )
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
            if not await self._check_preview_pause(page, "douyin"):
                return {"verified": False, "status": "preview", "platform_content_id": "", "platform_url": "", "published_at": "", "last_error": "preview mode — publish skipped"}
            try:
                await self._click_publish_button(page, locators)
            except RuntimeError as exc:
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
                return {
                    "verified": False,
                    "platform_content_id": "",
                    "platform_url": "",
                    "published_at": "",
                    "last_error": str(exc),
                }
            await page.wait_for_timeout(5000)
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
        verification_source = str(outcome.get("verification_source", "")).strip()
        row_verified = verification_source == "creator_center_row" and bool(platform_url)
        verified = bool(outcome.get("verified") or platform_content_id or row_verified)
        if verified and not platform_content_id and not row_verified:
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

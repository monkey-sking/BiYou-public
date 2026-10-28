"""
Xiaohongshu API publishing adapter using XhsSkills (Spider_XHS) vendored API.

Dependency requirements:
 spider-xhs-core runtime (Python) -> pip install from XhsSkills vendor
 jsdom/crypto-js (Node) -> npm install from XhsSkills vendor
"""
import os
import sys
from pathlib import Path

# Set XhsSkills runtime root
SCRIPT_DIR = Path("${HOME}/XhsSkills/skills/xhs-apis/scripts")
RUNTIME_ROOT = SCRIPT_DIR / "runtime" / "spider_xhs_core"
ORIGINAL_CWD = Path.cwd()

# Replicate _configure_runtime() from xhs_api_tool.py
def _configure_runtime():
    node_modules = SCRIPT_DIR / "node_modules"
    existing_node_path = os.environ.get("NODE_PATH")
    if node_modules.exists():
        os.environ["NODE_PATH"] = str(node_modules)
        if existing_node_path:
            os.environ["NODE_PATH"] += os.pathsep + existing_node_path
    os.chdir(RUNTIME_ROOT)
    runtime_path = str(RUNTIME_ROOT)
    if runtime_path not in sys.path:
        sys.path.insert(0, runtime_path)

_configure_runtime()

import json
from typing import Optional

from core.publishing.adapters.base import BasePublishingAdapter
from core.publishing.models import AdapterCapabilities, PublishJob, PublishResult


class XiaohongshuApiAdapter(BasePublishingAdapter):
    platform_name = "xiaohongshu"
    stability_tier = "beta"
    execution_mode = "api"
    capabilities = AdapterCapabilities(
        supports_native_schedule=False,
        supports_cover=True,
        supports_tags=True,
        supports_description=True,
    )

    def __init__(self, cookies_str: str = ""):
        """Inject cookies_str required for creator.xiaohongshu.com API."""
        from apis.xhs_creator_apis import XHS_Creator_Apis  # vendored runtime
        from xhs_utils.cookie_util import trans_cookies

        self.xhs_creator = XHS_Creator_Apis()
        self.cookies_by_str = cookies_str.strip()
        if not self.cookies_by_str:
            raise ValueError("xiaohongshu.api mode requires non-empty cookies_str")
        self.cookies_dict = trans_cookies(self.cookies_by_str)

    def validate(self, job: PublishJob) -> None:
        """Enforce video_path, title, cover."""
        if not job.video_path:
            raise ValueError("xiaohongshu.api mode requires video_path")
        if not job.title or len(job.title) < 5 or len(job.title) > 50:
            raise ValueError("xiaohongshu.api mode requires title (5-50 chars)")
        if not job.cover_path:
            raise ValueError("xiaohongshu.api mode requires cover_path")

    def schedule(self, job: PublishJob, publish_at: str) -> PublishResult:
        """API mode does not support native scheduling."""
        return PublishResult(
            platform="xiaohongshu",
            status="failed_manual",
            platform_content_id="",
            platform_url="",
            native_schedule=False,
            scheduled_by="biyou",
            scheduled_for="",
            published_at="",
            last_error="xiaohongshu.api mode does not support native scheduling",
        )

    def list(self, status: str = None) -> list:
        """List published content (placeholder: not implemented in API)."""
        return []

    def _normalize_title(self, title: str) -> str:
        """Clamp title to XHS limits: 5-50 chars."""
        title = title.strip()
        if len(title) < 5:
            title = title.ljust(5, 'X')[:5]
        if len(title) > 50:
            title = title[:47] + '...'
        return title

    def _build_note_info(self, job: PublishJob) -> dict:
        """Translate PublishJob into post_note payload schema."""
        title = self._normalize_title(job.title)
        desc = str(job.description).strip() or title
        payload = job.platform_payloads.get("xiaohongshu", {})

        # Media paths (both are absolute or resolved in publish()]
        images = [job.cover_path]  # single cover -> single image
        video = job.video_path

        # Topic extraction
        topics = []
        if job.tags:
            for tag in job.tags[:3]:  # XHS limit: 3 topics
                if tag.startswith('#'):
                    tag = tag[1:]
                topics.append(tag.split()[0])  # sanitize: first word only

        location = payload.get("location", None)
        if isinstance(location, str) and not location.strip():
            location = None

        note_info = {
            "title": title,
            "desc": desc,
            "postTime": None,
            "location": location,
            "type": payload.get("post_privacy", 0),  # 0=public, 1=private
            "media_type": "video",
            "topics": topics,
            "images": images,
            "video": video,
        }
        return note_info

    def publish(self, job: PublishJob) -> PublishResult:
        """Core publish workflow: upload media, invoke API, parse response."""
        note_info = self._build_note_info(job)
        success, msg, response = self.xhs_creator.post_note(
            noteInfo=note_info,
            cookies_str=self.cookies_by_str,
        )

        if not success or not response:
            return PublishResult(
                platform="xiaohongshu",
                status="failed_retryable",
                platform_content_id="",
                platform_url="",
                native_schedule=False,
                scheduled_by="biyou",
                scheduled_for="",
                published_at="",
                last_error=f"xiaohongshu.api: {msg}"[:800],
            )

        try:
            note_id = (response.get("data", {}) or {}).get("note_id") or ""
            if note_id:
                note_url = f"https://www.xiaohongshu.com/explore/{note_id}"
            else:
                note_url = "https://creator.xiaohongshu.com/publish/manage"
            return PublishResult(
                platform="xiaohongshu",
                status="published",
                platform_content_id=note_id,
                platform_url=note_url,
                native_schedule=False,
                scheduled_by="biyou",
                scheduled_for="",
                published_at=response.get("now", ""),
                last_error="",
            )
        except Exception as e:
            return PublishResult(
                platform="xiaohongshu",
                status="failed_manual",
                platform_content_id="",
                platform_url=note_url if 'note_url' in locals() else "",
                native_schedule=False,
                scheduled_by="biyou",
                scheduled_for="",
                published_at="",
                last_error=f"xiaohongshu.api (post-publish parse): {str(e)[:500]}",
            )
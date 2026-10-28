# Multi-Platform Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a multi-platform publishing framework in BiYou that keeps existing Bilibili workflows working while adding stable adapters for `Bilibili`, `Douyin`, and `Kuaishou`, plus beta automation adapters for `WeChat Channels` and `Xiaohongshu`.

**Architecture:** Add a shared publishing core under `core/publishing/` that normalizes publish jobs, adapter capabilities, scheduling intent, and result persistence. Route `main.py` through the new publish manager when enabled, dual-write Bilibili results during migration, and isolate per-platform logic behind API or browser-automation adapters.

**Tech Stack:** Python 3, `unittest`, `requests`, existing `biliup`, existing browser runtime helpers in `core/utils/browser_config.py`, existing Playwright-based browser automation modules, YAML config, JSON metadata files

---

## File Structure

### Shared Publishing Core

- Create: `core/publishing/__init__.py`
- Create: `core/publishing/models.py`
- Create: `core/publishing/records.py`
- Create: `core/publishing/scheduler_state.py`
- Create: `core/publishing/manager.py`
- Create: `core/publishing/registry.py`
- Create: `core/publishing/adapters/__init__.py`
- Create: `core/publishing/adapters/base.py`

### Stable Adapters

- Create: `core/publishing/adapters/bilibili.py`
- Create: `core/publishing/adapters/douyin.py`
- Create: `core/publishing/adapters/kuaishou.py`
- Create: `core/services/douyin_api.py`
- Create: `core/services/kuaishou_api.py`

### Beta Automation Adapters

- Create: `core/publishing/adapters/automation_base.py`
- Create: `core/publishing/adapters/wechat_channels.py`
- Create: `core/publishing/adapters/xiaohongshu.py`
- Create: `config/publishing_automation.example.yaml`

### Integration and Migration

- Modify: `main.py`
- Modify: `core/modules/uploader.py`
- Modify: `core/modules/commenter.py`
- Modify: `core/modules/subtitle_sync.py`
- Modify: `config/config.example.yaml`
- Modify: `requirements.txt`
- Modify: `README.md`
- Modify: `README_zh.md`

### Tests

- Create: `tests/test_publishing_models.py`
- Create: `tests/test_publish_records.py`
- Create: `tests/test_publishing_scheduler.py`
- Create: `tests/test_publishing_manager.py`
- Create: `tests/test_bilibili_adapter.py`
- Create: `tests/test_publish_routing.py`
- Create: `tests/test_douyin_adapter.py`
- Create: `tests/test_kuaishou_adapter.py`
- Create: `tests/test_automation_adapter_base.py`
- Create: `tests/test_wechat_channels_adapter.py`
- Create: `tests/test_xiaohongshu_adapter.py`

## Implementation Notes

- Execute this plan in a dedicated worktree on branch `feature/multi-platform-upload`.
- Keep the current Bilibili path available until `main.py`, cleanup, commenter, and subtitle sync all read the new `publish_records` structure safely.
- Treat `Douyin` and `Kuaishou` schedule support as BiYou-managed first unless native schedule is explicitly verified during implementation.
- Treat `WeChat Channels` and `Xiaohongshu` adapters as `beta` in logs, docs, and result records.

### Task 1: Add Shared Publish Models and Adapter Contract

**Files:**
- Create: `core/publishing/__init__.py`
- Create: `core/publishing/models.py`
- Create: `core/publishing/adapters/__init__.py`
- Create: `core/publishing/adapters/base.py`
- Test: `tests/test_publishing_models.py`

- [ ] **Step 1: Write the failing test**

```python
import unittest

from core.publishing.models import (
    AdapterCapabilities,
    PublishJob,
    PublishResult,
    ScheduleIntent,
)


class TestPublishingModels(unittest.TestCase):
    def test_publish_job_defaults_to_publish_now(self):
        job = PublishJob(
            platform_targets=["bilibili"],
            video_path="/tmp/video.mp4",
            title="Morning Workout",
            description="A simple test upload",
            cover_path="/tmp/cover.jpg",
            tags=["fitness", "cardio"],
        )

        self.assertEqual(job.schedule_intent.mode, "publish_now")
        self.assertEqual(job.platform_payloads, {})

    def test_publish_result_keeps_schedule_metadata(self):
        result = PublishResult(
            platform="douyin",
            status="scheduled",
            platform_content_id="item_123",
            platform_url="https://example.com/item_123",
            native_schedule=False,
            scheduled_by="biyou",
            scheduled_for="2026-04-08T19:05:00+08:00",
            published_at="",
            last_error="",
        )

        self.assertFalse(result.native_schedule)
        self.assertEqual(result.scheduled_by, "biyou")

    def test_adapter_capabilities_expose_native_flags(self):
        caps = AdapterCapabilities(
            supports_native_schedule=False,
            supports_cover=True,
            supports_tags=False,
            supports_description=True,
        )

        self.assertFalse(caps.supports_native_schedule)
        self.assertTrue(caps.supports_cover)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_publishing_models -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.publishing'`

- [ ] **Step 3: Write minimal implementation**

```python
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScheduleIntent:
    mode: str = "publish_now"
    publish_at: str = ""


@dataclass
class PublishJob:
    platform_targets: list[str]
    video_path: str
    title: str
    description: str
    cover_path: str
    tags: list[str]
    schedule_intent: ScheduleIntent = field(default_factory=ScheduleIntent)
    metadata_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    platform_payloads: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class AdapterCapabilities:
    supports_native_schedule: bool
    supports_cover: bool
    supports_tags: bool
    supports_description: bool


@dataclass
class PublishResult:
    platform: str
    status: str
    platform_content_id: str
    platform_url: str
    native_schedule: bool
    scheduled_by: str
    scheduled_for: str
    published_at: str
    last_error: str
    extra: dict[str, Any] = field(default_factory=dict)
```

```python
from abc import ABC, abstractmethod

from core.publishing.models import AdapterCapabilities, PublishJob, PublishResult


class BasePublishingAdapter(ABC):
    platform_name: str = ""
    stability_tier: str = "stable"
    execution_mode: str = "api"
    capabilities = AdapterCapabilities(
        supports_native_schedule=False,
        supports_cover=True,
        supports_tags=True,
        supports_description=True,
    )

    @abstractmethod
    def validate(self, job: PublishJob) -> None:
        raise NotImplementedError

    @abstractmethod
    def publish(self, job: PublishJob) -> PublishResult:
        raise NotImplementedError

    @abstractmethod
    def schedule(self, job: PublishJob, publish_at: str) -> PublishResult:
        raise NotImplementedError
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_publishing_models -v`
Expected: PASS with 3 tests passing

- [ ] **Step 5: Commit**

```bash
git add tests/test_publishing_models.py core/publishing/__init__.py core/publishing/models.py core/publishing/adapters/__init__.py core/publishing/adapters/base.py
git commit -m "feat: add publishing models and adapter contract"
```

### Task 2: Add Publish Record Helpers and Bilibili Legacy Compatibility

**Files:**
- Create: `core/publishing/records.py`
- Test: `tests/test_publish_records.py`

- [ ] **Step 1: Write the failing test**

```python
import unittest

from core.publishing.models import PublishResult
from core.publishing.records import (
    get_bilibili_publish_ids,
    get_publish_record,
    update_publish_record,
)


class TestPublishRecords(unittest.TestCase):
    def test_update_publish_record_creates_publish_records_tree(self):
        metadata = {}
        result = PublishResult(
            platform="bilibili",
            status="published",
            platform_content_id="BV1xx",
            platform_url="https://www.bilibili.com/video/BV1xx",
            native_schedule=True,
            scheduled_by="platform",
            scheduled_for="",
            published_at="2026-04-08T19:05:00+08:00",
            last_error="",
            extra={"aid": 123456},
        )

        update_publish_record(metadata, result)

        self.assertEqual(metadata["publish_records"]["bilibili"]["platform_content_id"], "BV1xx")
        self.assertEqual(metadata["uploaded_bvid"], "BV1xx")
        self.assertEqual(metadata["uploaded_aid"], 123456)

    def test_get_bilibili_publish_ids_falls_back_to_legacy_fields(self):
        metadata = {"uploaded_bvid": "BV1legacy", "uploaded_aid": 42}

        bvid, aid = get_bilibili_publish_ids(metadata)

        self.assertEqual(bvid, "BV1legacy")
        self.assertEqual(aid, 42)

    def test_get_publish_record_returns_empty_dict_when_missing(self):
        self.assertEqual(get_publish_record({}, "douyin"), {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_publish_records -v`
Expected: FAIL because `core.publishing.records` does not exist yet

- [ ] **Step 3: Write minimal implementation**

```python
from core.publishing.models import PublishResult


def get_publish_record(metadata: dict, platform: str) -> dict:
    return (metadata.get("publish_records") or {}).get(platform, {})


def update_publish_record(metadata: dict, result: PublishResult) -> dict:
    publish_records = metadata.setdefault("publish_records", {})
    publish_records[result.platform] = {
        "status": result.status,
        "platform_content_id": result.platform_content_id,
        "platform_url": result.platform_url,
        "native_schedule": result.native_schedule,
        "scheduled_by": result.scheduled_by,
        "scheduled_for": result.scheduled_for,
        "published_at": result.published_at,
        "last_error": result.last_error,
        **result.extra,
    }

    if result.platform == "bilibili":
        metadata["uploaded_bvid"] = result.platform_content_id
        if "aid" in result.extra:
            metadata["uploaded_aid"] = result.extra["aid"]

    return publish_records[result.platform]


def get_bilibili_publish_ids(metadata: dict) -> tuple[str, int | None]:
    record = get_publish_record(metadata, "bilibili")
    bvid = record.get("platform_content_id") or metadata.get("uploaded_bvid", "")
    aid = record.get("aid") or metadata.get("uploaded_aid")
    return bvid, aid
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_publish_records -v`
Expected: PASS with 3 tests passing

- [ ] **Step 5: Commit**

```bash
git add tests/test_publish_records.py core/publishing/records.py
git commit -m "feat: add publish record persistence helpers"
```

### Task 3: Add BiYou-Managed Scheduler State for Non-Native Scheduling

**Files:**
- Create: `core/publishing/scheduler_state.py`
- Test: `tests/test_publishing_scheduler.py`

- [ ] **Step 1: Write the failing test**

```python
import unittest

from core.publishing.scheduler_state import (
    enqueue_managed_job,
    get_due_jobs,
)


class TestPublishingScheduler(unittest.TestCase):
    def test_enqueue_managed_job_keeps_unique_platform_key(self):
        state = {"jobs": []}

        enqueue_managed_job(
            state,
            metadata_path="/tmp/video.json",
            platform="kuaishou",
            publish_at="2026-04-08T19:05:00+08:00",
        )
        enqueue_managed_job(
            state,
            metadata_path="/tmp/video.json",
            platform="kuaishou",
            publish_at="2026-04-08T19:05:00+08:00",
        )

        self.assertEqual(len(state["jobs"]), 1)

    def test_get_due_jobs_returns_only_ready_items(self):
        state = {
            "jobs": [
                {"metadata_path": "/tmp/one.json", "platform": "douyin", "publish_at": "2026-04-08T19:05:00+08:00"},
                {"metadata_path": "/tmp/two.json", "platform": "douyin", "publish_at": "2026-04-09T19:05:00+08:00"},
            ]
        }

        due = get_due_jobs(state, now_iso="2026-04-08T19:06:00+08:00")

        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["metadata_path"], "/tmp/one.json")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_publishing_scheduler -v`
Expected: FAIL because `core.publishing.scheduler_state` does not exist yet

- [ ] **Step 3: Write minimal implementation**

```python
def enqueue_managed_job(state: dict, metadata_path: str, platform: str, publish_at: str) -> dict:
    jobs = state.setdefault("jobs", [])
    key = f"{metadata_path}:{platform}"
    for job in jobs:
        if job["job_key"] == key:
            job["publish_at"] = publish_at
            return job

    created = {
        "job_key": key,
        "metadata_path": metadata_path,
        "platform": platform,
        "publish_at": publish_at,
    }
    jobs.append(created)
    return created


def get_due_jobs(state: dict, now_iso: str) -> list[dict]:
    jobs = state.get("jobs", [])
    return [job for job in jobs if job["publish_at"] <= now_iso]


def remove_job(state: dict, metadata_path: str, platform: str) -> None:
    key = f"{metadata_path}:{platform}"
    state["jobs"] = [job for job in state.get("jobs", []) if job["job_key"] != key]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_publishing_scheduler -v`
Expected: PASS with 2 tests passing

- [ ] **Step 5: Commit**

```bash
git add tests/test_publishing_scheduler.py core/publishing/scheduler_state.py
git commit -m "feat: add managed publishing scheduler state"
```

### Task 4: Implement the Publish Manager and Adapter Registry

**Files:**
- Create: `core/publishing/registry.py`
- Create: `core/publishing/manager.py`
- Test: `tests/test_publishing_manager.py`

- [ ] **Step 1: Write the failing test**

```python
import unittest

from core.publishing.manager import PublishManager
from core.publishing.models import AdapterCapabilities, PublishJob


class NativeScheduleAdapter:
    platform_name = "bilibili"
    capabilities = AdapterCapabilities(True, True, True, True)

    def validate(self, job):
        return None

    def publish(self, job):
        raise AssertionError("publish should not be used")

    def schedule(self, job, publish_at):
        return {
            "platform": "bilibili",
            "status": "scheduled",
            "platform_content_id": "BV1xx",
            "platform_url": "https://example.com/BV1xx",
            "native_schedule": True,
            "scheduled_by": "platform",
            "scheduled_for": publish_at,
            "published_at": "",
            "last_error": "",
            "extra": {"aid": 1},
        }


class ManagedScheduleAdapter:
    platform_name = "kuaishou"
    capabilities = AdapterCapabilities(False, True, True, True)

    def validate(self, job):
        return None

    def publish(self, job):
        return {
            "platform": "kuaishou",
            "status": "published",
            "platform_content_id": "photo_1",
            "platform_url": "https://example.com/photo_1",
            "native_schedule": False,
            "scheduled_by": "biyou",
            "scheduled_for": "",
            "published_at": "2026-04-08T19:05:00+08:00",
            "last_error": "",
            "extra": {},
        }

    def schedule(self, job, publish_at):
        raise AssertionError("schedule should not be used")


class TestPublishingManager(unittest.TestCase):
    def test_native_schedule_uses_adapter_schedule(self):
        manager = PublishManager(
            adapters={"bilibili": NativeScheduleAdapter()},
            scheduler_state={"jobs": []},
        )
        metadata = {}
        job = PublishJob(
            platform_targets=["bilibili"],
            video_path="/tmp/video.mp4",
            title="T",
            description="D",
            cover_path="/tmp/cover.jpg",
            tags=["tag"],
            metadata=metadata,
            metadata_path="/tmp/video.json",
        )
        job.schedule_intent.mode = "publish_at"
        job.schedule_intent.publish_at = "2026-04-08T19:05:00+08:00"

        manager.execute(job)

        self.assertEqual(metadata["publish_records"]["bilibili"]["scheduled_by"], "platform")

    def test_non_native_schedule_enqueues_biyou_job(self):
        manager = PublishManager(
            adapters={"kuaishou": ManagedScheduleAdapter()},
            scheduler_state={"jobs": []},
        )
        metadata = {}
        job = PublishJob(
            platform_targets=["kuaishou"],
            video_path="/tmp/video.mp4",
            title="T",
            description="D",
            cover_path="/tmp/cover.jpg",
            tags=["tag"],
            metadata=metadata,
            metadata_path="/tmp/video.json",
        )
        job.schedule_intent.mode = "publish_at"
        job.schedule_intent.publish_at = "2026-04-08T19:05:00+08:00"

        manager.execute(job)

        self.assertEqual(metadata["publish_records"]["kuaishou"]["scheduled_by"], "biyou")
        self.assertEqual(len(manager.scheduler_state["jobs"]), 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_publishing_manager -v`
Expected: FAIL because `PublishManager` and registry helpers do not exist yet

- [ ] **Step 3: Write minimal implementation**

```python
from core.publishing.models import PublishResult
from core.publishing.records import update_publish_record
from core.publishing.scheduler_state import enqueue_managed_job


class PublishManager:
    def __init__(self, adapters: dict[str, object], scheduler_state: dict | None = None):
        self.adapters = adapters
        self.scheduler_state = scheduler_state or {"jobs": []}

    def execute(self, job):
        for platform in job.platform_targets:
            adapter = self.adapters[platform]
            adapter.validate(job)

            if job.schedule_intent.mode == "publish_at":
                publish_at = job.schedule_intent.publish_at
                if adapter.capabilities.supports_native_schedule:
                    raw_result = adapter.schedule(job, publish_at)
                else:
                    enqueue_managed_job(self.scheduler_state, job.metadata_path, platform, publish_at)
                    raw_result = {
                        "platform": platform,
                        "status": "scheduled",
                        "platform_content_id": "",
                        "platform_url": "",
                        "native_schedule": False,
                        "scheduled_by": "biyou",
                        "scheduled_for": publish_at,
                        "published_at": "",
                        "last_error": "",
                        "extra": {},
                    }
            else:
                raw_result = adapter.publish(job)

            result = PublishResult(**raw_result)
            update_publish_record(job.metadata, result)

        return job.metadata
```

```python
def build_registry(*adapters):
    return {adapter.platform_name: adapter for adapter in adapters}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_publishing_manager -v`
Expected: PASS with 2 tests passing

- [ ] **Step 5: Commit**

```bash
git add tests/test_publishing_manager.py core/publishing/registry.py core/publishing/manager.py
git commit -m "feat: add publish manager and adapter registry"
```

### Task 5: Add the Bilibili Adapter by Wrapping the Existing Uploader

**Files:**
- Create: `core/publishing/adapters/bilibili.py`
- Modify: `core/modules/uploader.py`
- Test: `tests/test_bilibili_adapter.py`

- [ ] **Step 1: Write the failing test**

```python
import unittest
from unittest.mock import patch

from core.publishing.adapters.bilibili import BilibiliAdapter
from core.publishing.models import PublishJob


class TestBilibiliAdapter(unittest.TestCase):
    def test_publish_wraps_existing_upload_result(self):
        job = PublishJob(
            platform_targets=["bilibili"],
            video_path="/tmp/video.mp4",
            title="Morning Workout",
            description="Desc",
            cover_path="/tmp/cover.jpg",
            tags=["fitness"],
            platform_payloads={"bilibili": {"cover_path_4_3": "/tmp/cover43.jpg"}},
        )

        with patch("core.publishing.adapters.bilibili.get_bilibili_credentials", return_value={"bili_jct": "csrf"}), \
             patch("core.publishing.adapters.bilibili.upload_video", return_value={"success": True, "bvid": "BV1xx", "aid": 888}):
            adapter = BilibiliAdapter()
            result = adapter.publish(job)

        self.assertEqual(result["platform"], "bilibili")
        self.assertEqual(result["platform_content_id"], "BV1xx")
        self.assertEqual(result["extra"]["aid"], 888)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_bilibili_adapter -v`
Expected: FAIL because `core.publishing.adapters.bilibili` does not exist yet

- [ ] **Step 3: Write minimal implementation**

```python
from core.modules.uploader import get_bilibili_credentials, upload_video
from core.publishing.adapters.base import BasePublishingAdapter
from core.publishing.models import AdapterCapabilities, PublishJob


class BilibiliAdapter(BasePublishingAdapter):
    platform_name = "bilibili"
    stability_tier = "stable"
    execution_mode = "api"
    capabilities = AdapterCapabilities(
        supports_native_schedule=True,
        supports_cover=True,
        supports_tags=True,
        supports_description=True,
    )

    def validate(self, job: PublishJob) -> None:
        if not job.video_path or not job.cover_path:
            raise ValueError("Bilibili publish requires video_path and cover_path")

    def _build_metadata(self, job: PublishJob) -> dict:
        overrides = job.platform_payloads.get("bilibili", {})
        return {
            "video_path": job.video_path,
            "title": job.title,
            "description": job.description,
            "tags": job.tags,
            "cover_path_16_9": job.cover_path,
            "cover_path_4_3": overrides.get("cover_path_4_3", job.cover_path),
            **overrides,
        }

    def publish(self, job: PublishJob) -> dict:
        credentials = get_bilibili_credentials()
        raw = upload_video(self._build_metadata(job), credentials, dry_run=False, interactive=False)
        return {
            "platform": "bilibili",
            "status": "published" if raw.get("success") else "failed_retryable",
            "platform_content_id": raw.get("bvid", ""),
            "platform_url": f"https://www.bilibili.com/video/{raw.get('bvid', '')}" if raw.get("bvid") else "",
            "native_schedule": True,
            "scheduled_by": "platform",
            "scheduled_for": "",
            "published_at": "",
            "last_error": "" if raw.get("success") else "Bilibili publish failed",
            "extra": {"aid": raw.get("aid")},
        }

    def schedule(self, job: PublishJob, publish_at: str) -> dict:
        from datetime import datetime

        credentials = get_bilibili_credentials()
        metadata = self._build_metadata(job)
        metadata["dtime"] = int(datetime.fromisoformat(publish_at).timestamp())
        raw = upload_video(metadata, credentials, dry_run=False, interactive=False)
        return {
            "platform": "bilibili",
            "status": "scheduled" if raw.get("success") else "failed_retryable",
            "platform_content_id": raw.get("bvid", ""),
            "platform_url": f"https://www.bilibili.com/video/{raw.get('bvid', '')}" if raw.get("bvid") else "",
            "native_schedule": True,
            "scheduled_by": "platform",
            "scheduled_for": publish_at,
            "published_at": "",
            "last_error": "" if raw.get("success") else "Bilibili schedule failed",
            "extra": {"aid": raw.get("aid")},
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_bilibili_adapter -v`
Expected: PASS with 1 test passing

- [ ] **Step 5: Commit**

```bash
git add tests/test_bilibili_adapter.py core/publishing/adapters/bilibili.py core/modules/uploader.py
git commit -m "feat: add bilibili publishing adapter"
```

### Task 6: Route `main.py` Through the Publish Manager and Update Bilibili Compatibility Readers

**Files:**
- Modify: `main.py`
- Modify: `core/modules/commenter.py`
- Modify: `core/modules/subtitle_sync.py`
- Test: `tests/test_publish_routing.py`

- [ ] **Step 1: Write the failing test**

```python
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import main


class TestPublishRouting(unittest.TestCase):
    def test_step_upload_uses_publish_manager_when_enabled(self):
        args = SimpleNamespace(skip_upload=False, dry_run=True, yes=True, season=None, auto_season=False)

        with patch("core.modules.fetcher.load_metadata", return_value={"title": "T", "description": "D", "tags": ["tag"], "video_path": "/tmp/video.mp4", "cover_path_16_9": "/tmp/cover.jpg"}), \
             patch("core.modules.uploader.validate_upload_assets", return_value=(True, [])), \
             patch("main.run_multi_platform_publish", return_value=True) as run_multi:
            main.CONFIG["publishing"] = {"enabled": True, "default_targets": ["bilibili"]}
            ok = main.step_upload(args, schedule_time=0, metadata_path="/tmp/video.json")

        self.assertTrue(ok)
        run_multi.assert_called_once()
```

```python
from core.publishing.records import get_bilibili_publish_ids


class TestPublishCompatibility(unittest.TestCase):
    def test_commenter_reads_publish_records_before_legacy_fields(self):
        metadata = {
            "publish_records": {
                "bilibili": {"platform_content_id": "BV1new", "aid": 777}
            },
            "uploaded_bvid": "BV1old",
            "uploaded_aid": 1,
        }

        bvid, aid = get_bilibili_publish_ids(metadata)

        self.assertEqual(bvid, "BV1new")
        self.assertEqual(aid, 777)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_publish_routing -v`
Expected: FAIL because `run_multi_platform_publish` and the compatibility reads do not exist yet

- [ ] **Step 3: Write minimal implementation**

```python
from core.publishing.manager import PublishManager
from core.publishing.models import PublishJob
from core.publishing.registry import build_registry
from core.publishing.adapters.bilibili import BilibiliAdapter


def run_multi_platform_publish(metadata: dict, metadata_path: str, schedule_time: int = 0) -> bool:
    manager = PublishManager(
        adapters=build_registry(BilibiliAdapter()),
        scheduler_state={"jobs": []},
    )
    job = PublishJob(
        platform_targets=CONFIG.get("publishing", {}).get("default_targets", ["bilibili"]),
        video_path=metadata["video_path"],
        title=metadata["title"],
        description=metadata["description"],
        cover_path=metadata.get("cover_path_16_9", ""),
        tags=metadata.get("tags", []),
        metadata=metadata,
        metadata_path=metadata_path,
        platform_payloads={"bilibili": {"cover_path_4_3": metadata.get("cover_path_4_3", "")}},
    )
    if schedule_time:
        job.schedule_intent.mode = "publish_at"
        job.schedule_intent.publish_at = datetime.fromtimestamp(schedule_time).astimezone().isoformat()

    manager.execute(job)
    with open(metadata_path, "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, ensure_ascii=False, indent=4)
    return True
```

```python
def _has_bilibili_publish(meta: dict) -> bool:
    bvid, _ = get_bilibili_publish_ids(meta)
    return bool(bvid)
```

Update these call sites:

```python
# main.step_upload
if CONFIG.get("publishing", {}).get("enabled"):
    success = run_multi_platform_publish(metadata, metadata_path, schedule_time=schedule_time)
else:
    success = run_upload(...)
```

```python
# main.step_cleanup
if video_path and _has_bilibili_publish(meta):
```

```python
# core/modules/commenter.py
bvid, aid = get_bilibili_publish_ids(metadata)
```

```python
# core/modules/subtitle_sync.py
bvid, aid = get_bilibili_publish_ids(data)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_publish_routing tests.test_commenter -v`
Expected: PASS with the new routing test and existing commenter tests passing

- [ ] **Step 5: Commit**

```bash
git add tests/test_publish_routing.py main.py core/modules/commenter.py core/modules/subtitle_sync.py
git commit -m "feat: route uploads through publish manager"
```

### Task 7: Add the Douyin API Client and Stable Adapter

**Files:**
- Create: `core/services/douyin_api.py`
- Create: `core/publishing/adapters/douyin.py`
- Test: `tests/test_douyin_adapter.py`

- [ ] **Step 1: Write the failing test**

```python
import unittest
from unittest.mock import Mock

from core.publishing.adapters.douyin import DouyinAdapter
from core.publishing.models import PublishJob


class TestDouyinAdapter(unittest.TestCase):
    def test_publish_maps_tags_into_text_and_returns_item_id(self):
        client = Mock()
        client.upload_video.return_value = {"video_id": "video_1"}
        client.create_video.return_value = {"item_id": "item_1"}

        adapter = DouyinAdapter(client=client)
        job = PublishJob(
            platform_targets=["douyin"],
            video_path="/tmp/video.mp4",
            title="Morning Workout",
            description="A short session",
            cover_path="/tmp/cover.jpg",
            tags=["fitness", "cardio"],
        )

        result = adapter.publish(job)

        self.assertEqual(result["platform_content_id"], "item_1")
        self.assertEqual(result["scheduled_by"], "biyou")
        self.assertIn("#fitness", client.create_video.call_args.kwargs["text"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_douyin_adapter -v`
Expected: FAIL because the Douyin client and adapter modules do not exist yet

- [ ] **Step 3: Write minimal implementation**

```python
import requests


class DouyinApiClient:
    API_ROOT = "https://open.douyin.com"

    def __init__(self, access_token: str, session: requests.Session | None = None):
        self.access_token = access_token
        self.session = session or requests.Session()

    def upload_video(self, video_path: str) -> dict:
        with open(video_path, "rb") as fh:
            response = self.session.post(
                f"{self.API_ROOT}/video/create/upload/",
                headers={"access-token": self.access_token},
                files={"video": fh},
                timeout=120,
            )
        response.raise_for_status()
        return response.json()["data"]["video"]

    def create_video(self, *, video_id: str, text: str) -> dict:
        response = self.session.post(
            f"{self.API_ROOT}/video/create/",
            headers={"access-token": self.access_token, "Content-Type": "application/json"},
            json={"video_id": video_id, "text": text},
            timeout=60,
        )
        response.raise_for_status()
        return response.json()["data"]
```

```python
from core.publishing.adapters.base import BasePublishingAdapter
from core.publishing.models import AdapterCapabilities, PublishJob
from core.services.douyin_api import DouyinApiClient


def build_douyin_text(title: str, description: str, tags: list[str]) -> str:
    hashtags = " ".join(f"#{tag}" for tag in tags[:5])
    lines = [title.strip(), description.strip(), hashtags.strip()]
    return "\n".join(line for line in lines if line).strip()


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

    def __init__(self, client: DouyinApiClient):
        self.client = client

    def validate(self, job: PublishJob) -> None:
        if not job.video_path:
            raise ValueError("Douyin publish requires video_path")

    def publish(self, job: PublishJob) -> dict:
        uploaded = self.client.upload_video(job.video_path)
        created = self.client.create_video(
            video_id=uploaded["video_id"],
            text=build_douyin_text(job.title, job.description, job.tags),
        )
        return {
            "platform": "douyin",
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_douyin_adapter -v`
Expected: PASS with 1 test passing

- [ ] **Step 5: Commit**

```bash
git add tests/test_douyin_adapter.py core/services/douyin_api.py core/publishing/adapters/douyin.py
git commit -m "feat: add douyin publishing adapter"
```

### Task 8: Add the Kuaishou API Client and Stable Adapter

**Files:**
- Create: `core/services/kuaishou_api.py`
- Create: `core/publishing/adapters/kuaishou.py`
- Test: `tests/test_kuaishou_adapter.py`

- [ ] **Step 1: Write the failing test**

```python
import unittest
from unittest.mock import Mock

from core.publishing.adapters.kuaishou import KuaishouAdapter
from core.publishing.models import PublishJob


class TestKuaishouAdapter(unittest.TestCase):
    def test_publish_uses_start_upload_then_publish(self):
        client = Mock()
        client.start_upload.return_value = {"upload_token": "up_1", "endpoint": "uploader.test.gifshow.com"}
        client.publish_photo.return_value = {"photo_id": "photo_1"}

        adapter = KuaishouAdapter(client=client)
        job = PublishJob(
            platform_targets=["kuaishou"],
            video_path="/tmp/video.mp4",
            title="Morning Workout",
            description="A short session",
            cover_path="/tmp/cover.jpg",
            tags=["fitness", "cardio"],
        )

        result = adapter.publish(job)

        self.assertEqual(result["platform_content_id"], "photo_1")
        client.publish_photo.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_kuaishou_adapter -v`
Expected: FAIL because the Kuaishou client and adapter do not exist yet

- [ ] **Step 3: Write minimal implementation**

```python
import requests


class KuaishouApiClient:
    API_ROOT = "https://open.kuaishou.com"

    def __init__(self, access_token: str, app_id: str, session: requests.Session | None = None):
        self.access_token = access_token
        self.app_id = app_id
        self.session = session or requests.Session()

    def start_upload(self) -> dict:
        response = self.session.post(
            f"{self.API_ROOT}/openapi/photo/start_upload",
            params={"access_token": self.access_token, "app_id": self.app_id},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        return {"upload_token": payload["upload_token"], "endpoint": payload["endpoint"]}

    def publish_photo(self, *, caption: str, video_path: str, cover_path: str, upload_token: str) -> dict:
        with open(video_path, "rb") as video_fh, open(cover_path, "rb") as cover_fh:
            response = self.session.post(
                f"{self.API_ROOT}/openapi/photo/publish",
                params={
                    "access_token": self.access_token,
                    "app_id": self.app_id,
                    "upload_token": upload_token,
                },
                files={"video": video_fh, "cover": cover_fh},
                data={"caption": caption},
                timeout=120,
            )
        response.raise_for_status()
        return response.json()
```

```python
from core.publishing.adapters.base import BasePublishingAdapter
from core.publishing.models import AdapterCapabilities, PublishJob
from core.services.kuaishou_api import KuaishouApiClient


def build_kuaishou_caption(title: str, description: str, tags: list[str]) -> str:
    hashtags = " ".join(f"#{tag}" for tag in tags[:5])
    parts = [title.strip(), description.strip(), hashtags.strip()]
    return "\n".join(part for part in parts if part).strip()


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
        upload_data = self.client.start_upload()
        created = self.client.publish_photo(
            caption=build_kuaishou_caption(job.title, job.description, job.tags),
            video_path=job.video_path,
            cover_path=job.cover_path,
            upload_token=upload_data["upload_token"],
        )
        return {
            "platform": "kuaishou",
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
        raise NotImplementedError("Kuaishou uses BiYou-managed scheduling in phase 1")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_kuaishou_adapter -v`
Expected: PASS with 1 test passing

- [ ] **Step 5: Commit**

```bash
git add tests/test_kuaishou_adapter.py core/services/kuaishou_api.py core/publishing/adapters/kuaishou.py
git commit -m "feat: add kuaishou publishing adapter"
```

### Task 9: Add the Browser Automation Base and Selector Config for Beta Adapters

**Files:**
- Create: `core/publishing/adapters/automation_base.py`
- Create: `config/publishing_automation.example.yaml`
- Modify: `requirements.txt`
- Test: `tests/test_automation_adapter_base.py`

- [ ] **Step 1: Write the failing test**

```python
import unittest

from core.publishing.adapters.automation_base import build_browser_launch_context


class TestAutomationAdapterBase(unittest.TestCase):
    def test_build_browser_launch_context_uses_project_profile_and_port(self):
        config = {"browser_profile": "~/.biyou-workflow-browser-data", "browser_debug_port": 39222}

        context = build_browser_launch_context(config)

        self.assertTrue(context["profile_dir"].endswith(".biyou-workflow-browser-data"))
        self.assertEqual(context["debug_port"], 39222)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_automation_adapter_base -v`
Expected: FAIL because `automation_base.py` does not exist yet

- [ ] **Step 3: Write minimal implementation**

```python
import asyncio

from playwright.async_api import async_playwright

from core.utils.browser_config import get_browser_runtime_config


def build_browser_launch_context(config: dict) -> dict:
    runtime = get_browser_runtime_config(config=config)
    return {
        "profile_dir": runtime["profile_dir"],
        "debug_port": runtime["debug_port"],
        "profile_fallback": runtime["profile_fallback"],
    }


class AutomationAdapterBase:
    stability_tier = "beta"
    execution_mode = "automation"

    def __init__(self, config: dict, selectors: dict):
        self.config = config
        self.selectors = selectors

    def build_runtime(self) -> dict:
        return build_browser_launch_context(self.config)

    async def _open_page(self, publish_url: str):
        runtime = self.build_runtime()
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch_persistent_context(
            user_data_dir=runtime["profile_dir"],
            headless=False,
            args=[f"--remote-debugging-port={runtime['debug_port']}"],
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()
        await page.goto(publish_url, wait_until="domcontentloaded")
        return playwright, browser, page

    async def _fill_first(self, page, selectors: list[str], value: str) -> None:
        for selector in selectors:
            locator = page.locator(selector).first
            if await locator.count():
                await locator.fill(value)
                return
        raise RuntimeError(f"Unable to locate fill target from selectors: {selectors}")

    async def _set_file_first(self, page, selectors: list[str], file_path: str) -> None:
        for selector in selectors:
            locator = page.locator(selector).first
            if await locator.count():
                await locator.set_input_files(file_path)
                return
        raise RuntimeError(f"Unable to locate file input from selectors: {selectors}")

    async def _click_first(self, page, selectors: list[str]) -> None:
        for selector in selectors:
            locator = page.locator(selector).first
            if await locator.count():
                await locator.click()
                return
        raise RuntimeError(f"Unable to locate clickable target from selectors: {selectors}")

    def run_async(self, coro):
        return asyncio.run(coro)
```

```yaml
wechat_channels:
  publish_url: "https://channels.weixin.qq.com"
  locators:
    title:
      - "textarea"
    description:
      - "[contenteditable='true']"
    video_upload:
      - "input[type='file']"
    cover_upload:
      - "input[type='file']"
    publish_button:
      - "button"

xiaohongshu:
  publish_url: "https://creator.xiaohongshu.com"
  locators:
    title:
      - "input"
    description:
      - "textarea"
    video_upload:
      - "input[type='file']"
    cover_upload:
      - "input[type='file']"
    publish_button:
      - "button"
```

Add this dependency line:

```txt
playwright
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_automation_adapter_base -v`
Expected: PASS with 1 test passing

- [ ] **Step 5: Commit**

```bash
git add tests/test_automation_adapter_base.py core/publishing/adapters/automation_base.py config/publishing_automation.example.yaml requirements.txt
git commit -m "feat: add automation adapter base"
```

### Task 10: Add the WeChat Channels Beta Automation Adapter

**Files:**
- Create: `core/publishing/adapters/wechat_channels.py`
- Test: `tests/test_wechat_channels_adapter.py`

- [ ] **Step 1: Write the failing test**

```python
import unittest

from core.publishing.adapters.wechat_channels import normalize_wechat_publish_payload
from core.publishing.models import PublishJob


class TestWechatChannelsAdapter(unittest.TestCase):
    def test_normalize_publish_payload_keeps_core_fields(self):
        job = PublishJob(
            platform_targets=["wechat_channels"],
            video_path="/tmp/video.mp4",
            title="Morning Workout",
            description="A short session",
            cover_path="/tmp/cover.jpg",
            tags=["fitness", "cardio"],
        )

        payload = normalize_wechat_publish_payload(job)

        self.assertEqual(payload["title"], "Morning Workout")
        self.assertIn("#fitness", payload["description"])
        self.assertEqual(payload["cover_path"], "/tmp/cover.jpg")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_wechat_channels_adapter -v`
Expected: FAIL because `wechat_channels.py` does not exist yet

- [ ] **Step 3: Write minimal implementation**

```python
import asyncio

from core.publishing.adapters.automation_base import AutomationAdapterBase
from core.publishing.models import AdapterCapabilities, PublishJob


def normalize_wechat_publish_payload(job: PublishJob) -> dict:
    hashtags = " ".join(f"#{tag}" for tag in job.tags[:5])
    description = "\n".join(part for part in [job.description.strip(), hashtags] if part).strip()
    return {
        "title": job.title.strip(),
        "description": description,
        "cover_path": job.cover_path,
        "video_path": job.video_path,
    }


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

    async def _publish_async(self, payload: dict) -> dict:
        playwright, browser, page = await self._open_page(self.selectors["publish_url"])
        try:
            await self._fill_first(page, self.selectors["locators"]["title"], payload["title"])
            await self._fill_first(page, self.selectors["locators"]["description"], payload["description"])
            await self._set_file_first(page, self.selectors["locators"]["video_upload"], payload["video_path"])
            await self._set_file_first(page, self.selectors["locators"]["cover_upload"], payload["cover_path"])
            await self._click_first(page, self.selectors["locators"]["publish_button"])
            return {"manual_required": False}
        finally:
            await browser.close()
            await playwright.stop()

    def publish(self, job: PublishJob) -> dict:
        payload = normalize_wechat_publish_payload(job)
        outcome = asyncio.run(self._publish_async(payload))
        return {
            "platform": "wechat_channels",
            "status": "failed_manual" if outcome["manual_required"] else "published",
            "platform_content_id": "",
            "platform_url": "",
            "native_schedule": False,
            "scheduled_by": "biyou",
            "scheduled_for": "",
            "published_at": "",
            "last_error": "manual intervention required" if outcome["manual_required"] else "",
            "extra": payload,
        }

    def schedule(self, job: PublishJob, publish_at: str) -> dict:
        return {
            "platform": "wechat_channels",
            "status": "scheduled",
            "platform_content_id": "",
            "platform_url": "",
            "native_schedule": False,
            "scheduled_by": "biyou",
            "scheduled_for": publish_at,
            "published_at": "",
            "last_error": "",
            "extra": normalize_wechat_publish_payload(job),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_wechat_channels_adapter -v`
Expected: PASS with 1 test passing

- [ ] **Step 5: Commit**

```bash
git add tests/test_wechat_channels_adapter.py core/publishing/adapters/wechat_channels.py
git commit -m "feat: add wechat channels beta adapter"
```

### Task 11: Add the Xiaohongshu Beta Automation Adapter and Update User Docs

**Files:**
- Create: `core/publishing/adapters/xiaohongshu.py`
- Modify: `config/config.example.yaml`
- Modify: `README.md`
- Modify: `README_zh.md`
- Test: `tests/test_xiaohongshu_adapter.py`

- [ ] **Step 1: Write the failing test**

```python
import unittest

from core.publishing.adapters.xiaohongshu import normalize_xiaohongshu_publish_payload
from core.publishing.models import PublishJob


class TestXiaohongshuAdapter(unittest.TestCase):
    def test_normalize_publish_payload_moves_tags_into_topic_text(self):
        job = PublishJob(
            platform_targets=["xiaohongshu"],
            video_path="/tmp/video.mp4",
            title="Morning Workout",
            description="A short session",
            cover_path="/tmp/cover.jpg",
            tags=["fitness", "cardio"],
        )

        payload = normalize_xiaohongshu_publish_payload(job)

        self.assertEqual(payload["title"], "Morning Workout")
        self.assertIn("#fitness", payload["body"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_xiaohongshu_adapter -v`
Expected: FAIL because `xiaohongshu.py` does not exist yet

- [ ] **Step 3: Write minimal implementation**

```python
import asyncio

from core.publishing.adapters.automation_base import AutomationAdapterBase
from core.publishing.models import AdapterCapabilities, PublishJob


def normalize_xiaohongshu_publish_payload(job: PublishJob) -> dict:
    hashtags = " ".join(f"#{tag}" for tag in job.tags[:5])
    body = "\n".join(part for part in [job.description.strip(), hashtags] if part).strip()
    return {
        "title": job.title.strip(),
        "body": body,
        "cover_path": job.cover_path,
        "video_path": job.video_path,
    }


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

    async def _publish_async(self, payload: dict) -> dict:
        playwright, browser, page = await self._open_page(self.selectors["publish_url"])
        try:
            await self._fill_first(page, self.selectors["locators"]["title"], payload["title"])
            await self._fill_first(page, self.selectors["locators"]["description"], payload["body"])
            await self._set_file_first(page, self.selectors["locators"]["video_upload"], payload["video_path"])
            await self._set_file_first(page, self.selectors["locators"]["cover_upload"], payload["cover_path"])
            await self._click_first(page, self.selectors["locators"]["publish_button"])
            return {"manual_required": False}
        finally:
            await browser.close()
            await playwright.stop()

    def publish(self, job: PublishJob) -> dict:
        payload = normalize_xiaohongshu_publish_payload(job)
        outcome = asyncio.run(self._publish_async(payload))
        return {
            "platform": "xiaohongshu",
            "status": "failed_manual" if outcome["manual_required"] else "published",
            "platform_content_id": "",
            "platform_url": "",
            "native_schedule": False,
            "scheduled_by": "biyou",
            "scheduled_for": "",
            "published_at": "",
            "last_error": "manual intervention required" if outcome["manual_required"] else "",
            "extra": payload,
        }

    def schedule(self, job: PublishJob, publish_at: str) -> dict:
        return {
            "platform": "xiaohongshu",
            "status": "scheduled",
            "platform_content_id": "",
            "platform_url": "",
            "native_schedule": False,
            "scheduled_by": "biyou",
            "scheduled_for": publish_at,
            "published_at": "",
            "last_error": "",
            "extra": normalize_xiaohongshu_publish_payload(job),
        }
```

```yaml
publishing:
  enabled: false
  default_targets:
    - bilibili
  managed_scheduler:
    state_file: "history/publishing_jobs.json"
  adapters:
    bilibili:
      enabled: true
      mode: api
    douyin:
      enabled: false
      mode: api
    kuaishou:
      enabled: false
      mode: api
    wechat_channels:
      enabled: false
      mode: automation
    xiaohongshu:
      enabled: false
      mode: automation
```

Document these user-facing points in both READMEs:

```md
## Multi-Platform Publishing

- Stable adapters: `Bilibili`, `Douyin`, `Kuaishou`
- Beta adapters: `WeChat Channels`, `Xiaohongshu`
- Unified fields: title, description, cover, tags, schedule intent
- Beta adapters use browser automation and may require manual re-login
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_xiaohongshu_adapter -v`
Expected: PASS with 1 test passing

- [ ] **Step 5: Run the focused regression suite and commit**

Run: `python3 -m unittest tests.test_publishing_models tests.test_publish_records tests.test_publishing_scheduler tests.test_publishing_manager tests.test_bilibili_adapter tests.test_publish_routing tests.test_douyin_adapter tests.test_kuaishou_adapter tests.test_automation_adapter_base tests.test_wechat_channels_adapter tests.test_xiaohongshu_adapter tests.test_commenter -v`
Expected: PASS with the new publishing tests and existing commenter coverage all green

```bash
git add tests/test_xiaohongshu_adapter.py core/publishing/adapters/xiaohongshu.py config/config.example.yaml README.md README_zh.md
git commit -m "feat: add xiaohongshu beta adapter and docs"
```

## Coverage Check

- Shared publish model: Task 1
- Result persistence and Bilibili dual-write migration: Task 2
- BiYou-managed scheduling for non-native platforms: Task 3
- Core publish orchestration and adapter routing: Task 4
- Stable Bilibili adapter migration: Tasks 5-6
- Stable Douyin adapter: Task 7
- Stable Kuaishou adapter: Task 8
- Beta automation foundation: Task 9
- WeChat Channels beta adapter: Task 10
- Xiaohongshu beta adapter and user docs: Task 11

## Final Verification Checklist

- `main.py` still supports the legacy Bilibili path when `publishing.enabled = false`
- Bilibili writes both `publish_records.bilibili` and `uploaded_bvid` / `uploaded_aid`
- `Douyin` and `Kuaishou` schedule requests are persisted into the BiYou-managed scheduler queue
- `WeChat Channels` and `Xiaohongshu` are marked as `beta` and never claim native schedule support
- Cleanup, commenter, and subtitle sync read the new Bilibili publish record safely

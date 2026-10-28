# Channel Daily Publish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a channel backlog workflow that preloads videos from `https://www.youtube.com/@RosarioCooking/videos` and schedules one prepared item per day at `19:05` Asia/Shanghai.

**Architecture:** Add a dedicated backlog state module plus a workflow orchestrator that reuses the existing `main.py` stage helpers for download, metadata, cover, upload, and cleanup. Expose the feature through a dedicated script with `sync`, `publish`, and `run` commands.

**Tech Stack:** Python 3, JSON state files, existing `yt_dlp`, existing `main.py` workflow helpers, `unittest`

---

### Task 1: Add Failing Tests for Backlog State and Selection

**Files:**
- Create: `tests/test_channel_daily_publish.py`
- Test: `tests/test_channel_daily_publish.py`

- [ ] **Step 1: Write the failing test**

```python
import unittest
from datetime import datetime

from core.workflows.channel_daily_publish import (
    choose_publish_candidate,
    has_scheduled_item_for_day,
)


class TestChannelDailyPublish(unittest.TestCase):
    def test_prefers_new_prepared_item_before_backlog(self):
        items = [
            {"youtube_id": "old1", "status": "prepared", "is_new": False, "discovered_at": "2026-04-06T10:00:00+08:00"},
            {"youtube_id": "new1", "status": "prepared", "is_new": True, "discovered_at": "2026-04-07T09:00:00+08:00"},
        ]
        chosen = choose_publish_candidate(items)
        self.assertEqual(chosen["youtube_id"], "new1")

    def test_falls_back_to_newest_unpublished_backlog_item(self):
        items = [
            {"youtube_id": "old1", "status": "prepared", "is_new": False, "discovered_at": "2026-04-05T09:00:00+08:00"},
            {"youtube_id": "old2", "status": "prepared", "is_new": False, "discovered_at": "2026-04-06T09:00:00+08:00"},
        ]
        chosen = choose_publish_candidate(items)
        self.assertEqual(chosen["youtube_id"], "old2")

    def test_blocks_second_schedule_for_same_day(self):
        items = [
            {"youtube_id": "a1", "status": "scheduled", "scheduled_for": "2026-04-07T19:05:00+08:00"},
        ]
        self.assertTrue(has_scheduled_item_for_day(items, "2026-04-07"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_channel_daily_publish`
Expected: FAIL with import errors because `core.workflows.channel_daily_publish` does not exist yet

- [ ] **Step 3: Write minimal implementation**

```python
# Create the module with placeholder implementations that satisfy imports only after tests fail.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_channel_daily_publish`
Expected: PASS for the selection logic tests

- [ ] **Step 5: Commit**

```bash
git add tests/test_channel_daily_publish.py core/workflows/channel_daily_publish.py
git commit -m "feat: add channel publish selection logic"
```

### Task 2: Add Persistent Backlog State Utilities

**Files:**
- Create: `core/services/channel_backlog.py`
- Modify: `core/workflows/channel_daily_publish.py`
- Test: `tests/test_channel_daily_publish.py`

- [ ] **Step 1: Write the failing test**

```python
def test_upsert_discovered_item_preserves_existing_state():
    state = {"items": {"abc": {"youtube_id": "abc", "status": "prepared", "is_new": True}}}
    item = upsert_discovered_item(state, {"youtube_id": "abc", "title": "Updated"})
    assert item["status"] == "prepared"
    assert item["title"] == "Updated"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_channel_daily_publish`
Expected: FAIL because `upsert_discovered_item` is undefined

- [ ] **Step 3: Write minimal implementation**

```python
def upsert_discovered_item(state, incoming):
    items = state.setdefault("items", {})
    youtube_id = incoming["youtube_id"]
    existing = items.get(youtube_id, {})
    merged = {
        "youtube_id": youtube_id,
        "source_url": incoming.get("source_url", existing.get("source_url", "")),
        "title": incoming.get("title", existing.get("title", "")),
        "discovered_at": existing.get("discovered_at", incoming.get("discovered_at", "")),
        "status": existing.get("status", "discovered"),
        "is_new": existing.get("is_new", True),
        "video_path": existing.get("video_path", ""),
        "metadata_path": existing.get("metadata_path", ""),
        "scheduled_for": existing.get("scheduled_for", ""),
        "uploaded_bvid": existing.get("uploaded_bvid", ""),
        "last_error": existing.get("last_error", ""),
    }
    items[youtube_id] = merged
    return merged
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_channel_daily_publish`
Expected: PASS for the state merge test

- [ ] **Step 5: Commit**

```bash
git add tests/test_channel_daily_publish.py core/services/channel_backlog.py core/workflows/channel_daily_publish.py
git commit -m "feat: add channel backlog state helpers"
```

### Task 3: Add Channel Feed Discovery

**Files:**
- Modify: `core/workflows/channel_daily_publish.py`
- Test: `tests/test_channel_daily_publish.py`

- [ ] **Step 1: Write the failing test**

```python
def test_build_watch_url_from_feed_item():
    item = {"youtube_id": "abc123xyz00"}
    self.assertEqual(
        build_watch_url(item),
        "https://www.youtube.com/watch?v=abc123xyz00",
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_channel_daily_publish`
Expected: FAIL because `build_watch_url` is undefined

- [ ] **Step 3: Write minimal implementation**

```python
def build_watch_url(item):
    return f"https://www.youtube.com/watch?v={item['youtube_id']}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_channel_daily_publish`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_channel_daily_publish.py core/workflows/channel_daily_publish.py
git commit -m "feat: add channel feed item normalization"
```

### Task 4: Implement Sync Workflow

**Files:**
- Modify: `core/workflows/channel_daily_publish.py`
- Create: `scripts/channel_daily_publish.py`
- Test: `tests/test_channel_daily_publish.py`

- [ ] **Step 1: Write the failing test**

```python
def test_sync_marks_item_prepared_after_pipeline_steps(self):
    workflow = ChannelDailyPublishWorkflow(config={"channel_url": "https://www.youtube.com/@RosarioCooking/videos"})
    workflow.discover_items = lambda: [{"youtube_id": "abc123xyz00", "title": "Video", "source_url": "https://www.youtube.com/watch?v=abc123xyz00"}]
    workflow.prepare_item = lambda item: {"video_path": "/tmp/video.mp4", "metadata_path": "/tmp/video.json"}
    state = workflow.sync()
    self.assertEqual(state["items"]["abc123xyz00"]["status"], "prepared")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_channel_daily_publish`
Expected: FAIL because `ChannelDailyPublishWorkflow.sync` is undefined or incomplete

- [ ] **Step 3: Write minimal implementation**

```python
class ChannelDailyPublishWorkflow:
    def sync(self):
        state = self.load_state()
        for incoming in self.discover_items():
            item = upsert_discovered_item(state, incoming)
            if item["status"] in {"prepared", "scheduled", "published"}:
                continue
            prepared = self.prepare_item(item)
            item["video_path"] = prepared.get("video_path", "")
            item["metadata_path"] = prepared.get("metadata_path", "")
            item["status"] = "prepared"
            item["prepared_at"] = self.now_iso()
        self.save_state(state)
        return state
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_channel_daily_publish`
Expected: PASS for sync orchestration tests

- [ ] **Step 5: Commit**

```bash
git add tests/test_channel_daily_publish.py core/workflows/channel_daily_publish.py scripts/channel_daily_publish.py
git commit -m "feat: add channel sync workflow"
```

### Task 5: Reuse Existing Main Workflow Stages for Preparation

**Files:**
- Modify: `core/workflows/channel_daily_publish.py`
- Test: `tests/test_channel_daily_publish.py`

- [ ] **Step 1: Write the failing test**

```python
def test_prepare_item_returns_metadata_and_video_paths(self):
    workflow = ChannelDailyPublishWorkflow(config={"channel_url": "x"})
    with patch("core.workflows.channel_daily_publish.main.step_download_video") as step_download, \
         patch("core.workflows.channel_daily_publish.main.step_fetch_metadata", return_value="/tmp/video.json"), \
         patch("core.workflows.channel_daily_publish.main.step_process_cover", return_value=True):
        step_download.return_value = (None, "/tmp/video.mp4")
        result = workflow.prepare_item({"source_url": "https://www.youtube.com/watch?v=abc123xyz00"})
    self.assertEqual(result["video_path"], "/tmp/video.mp4")
    self.assertEqual(result["metadata_path"], "/tmp/video.json")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_channel_daily_publish`
Expected: FAIL because `prepare_item` does not orchestrate the main workflow yet

- [ ] **Step 3: Write minimal implementation**

```python
def prepare_item(self, item):
    args = self.build_main_args(url=item["source_url"], skip_upload=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        download_future, video_path = main.step_download_video(args, executor)
        metadata_path = main.step_fetch_metadata(args, target_path=video_path)
        if not metadata_path:
            raise RuntimeError("Metadata preparation failed")
        if not main.step_process_cover(args, metadata_path, download_future):
            raise RuntimeError("Cover preparation failed")
        if download_future:
            resolved_video_path = main.wait_for_download(download_future)
            video_path = resolved_video_path or video_path
    return {"video_path": video_path, "metadata_path": metadata_path}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_channel_daily_publish`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_channel_daily_publish.py core/workflows/channel_daily_publish.py
git commit -m "feat: reuse main workflow for channel preparation"
```

### Task 6: Implement Daily Publish Scheduling

**Files:**
- Modify: `core/workflows/channel_daily_publish.py`
- Test: `tests/test_channel_daily_publish.py`

- [ ] **Step 1: Write the failing test**

```python
def test_publish_schedules_one_item_and_marks_it_scheduled(self):
    workflow = ChannelDailyPublishWorkflow(config={"publish_time": "19:05", "timezone": "Asia/Shanghai"})
    workflow.load_state = lambda: {
        "items": {
            "abc": {
                "youtube_id": "abc",
                "source_url": "https://www.youtube.com/watch?v=abc",
                "status": "prepared",
                "is_new": True,
                "discovered_at": "2026-04-07T09:00:00+08:00",
                "metadata_path": "/tmp/video.json",
            }
        }
    }
    workflow.schedule_upload = lambda item, schedule_ts: {"uploaded_bvid": "BV1xx"}
    result = workflow.publish()
    self.assertEqual(result["youtube_id"], "abc")
    self.assertEqual(workflow.load_state()["items"]["abc"]["status"], "scheduled")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_channel_daily_publish`
Expected: FAIL because `publish` is undefined or incomplete

- [ ] **Step 3: Write minimal implementation**

```python
def publish(self):
    state = self.load_state()
    items = list(state.get("items", {}).values())
    today = self.today_local_date()
    if has_scheduled_item_for_day(items, today):
        return None
    candidate = choose_publish_candidate(items)
    if not candidate:
        return None
    schedule_ts = self.compute_schedule_timestamp(today)
    result = self.schedule_upload(candidate, schedule_ts)
    candidate["status"] = "scheduled"
    candidate["scheduled_for"] = self.schedule_iso(today)
    candidate["uploaded_bvid"] = result.get("uploaded_bvid", "")
    candidate["is_new"] = False
    self.save_state(state)
    return candidate
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_channel_daily_publish`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_channel_daily_publish.py core/workflows/channel_daily_publish.py
git commit -m "feat: add daily publish scheduling"
```

### Task 7: Reuse Existing Upload Flow for Scheduled Publish

**Files:**
- Modify: `core/workflows/channel_daily_publish.py`
- Test: `tests/test_channel_daily_publish.py`

- [ ] **Step 1: Write the failing test**

```python
def test_schedule_upload_uses_main_upload_step(self):
    workflow = ChannelDailyPublishWorkflow(config={})
    with patch("core.workflows.channel_daily_publish.main.step_upload", return_value=True) as step_upload:
        with patch("core.workflows.channel_daily_publish.main.step_cleanup") as step_cleanup:
            result = workflow.schedule_upload({"metadata_path": "/tmp/video.json"}, 1234567890)
    self.assertTrue(result["success"])
    step_upload.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_channel_daily_publish`
Expected: FAIL because `schedule_upload` is undefined or incomplete

- [ ] **Step 3: Write minimal implementation**

```python
def schedule_upload(self, item, schedule_ts):
    args = self.build_main_args(url=None, skip_upload=False)
    ok = main.step_upload(args, schedule_ts, item["metadata_path"])
    if not ok:
        raise RuntimeError("Scheduled upload failed")
    main.step_cleanup(args, item["metadata_path"])
    metadata = load_metadata(path=item["metadata_path"]) if os.path.exists(item["metadata_path"]) else {}
    return {"success": True, "uploaded_bvid": metadata.get("uploaded_bvid", "")}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_channel_daily_publish`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_channel_daily_publish.py core/workflows/channel_daily_publish.py
git commit -m "feat: reuse scheduled upload flow for channel publisher"
```

### Task 8: Add CLI Entry Point and Config Wiring

**Files:**
- Create: `scripts/channel_daily_publish.py`
- Modify: `config/config.example.yaml`
- Modify: `README_zh.md`
- Test: `tests/test_channel_daily_publish.py`

- [ ] **Step 1: Write the failing test**

```python
def test_workflow_reads_channel_daily_publish_config(self):
    workflow = ChannelDailyPublishWorkflow(config={
        "channel_daily_publish": {
            "channel_url": "https://www.youtube.com/@RosarioCooking/videos",
            "publish_time": "19:05",
        }
    })
    self.assertEqual(workflow.channel_url, "https://www.youtube.com/@RosarioCooking/videos")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_channel_daily_publish`
Expected: FAIL because config wiring is incomplete

- [ ] **Step 3: Write minimal implementation**

```python
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["sync", "publish", "run"])
    args = parser.parse_args()
    workflow = ChannelDailyPublishWorkflow()
    if args.command == "sync":
        workflow.sync()
    elif args.command == "publish":
        workflow.publish()
    else:
        workflow.sync()
        workflow.publish()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_channel_daily_publish`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_channel_daily_publish.py scripts/channel_daily_publish.py config/config.example.yaml README_zh.md core/workflows/channel_daily_publish.py
git commit -m "feat: add channel daily publish entry point"
```

### Task 9: Final Verification

**Files:**
- Modify: `progress.md`

- [ ] **Step 1: Run targeted test suite**

Run: `python3 -m unittest tests.test_channel_daily_publish tests.test_collection_sync tests.test_tags tests.test_activity_keyword`
Expected: PASS

- [ ] **Step 2: Run a dry functional smoke check**

Run: `python3 scripts/channel_daily_publish.py run`
Expected: loads config and state without import/runtime errors; safe to interrupt before any real publish in a non-production verification context

- [ ] **Step 3: Update planning files**

```markdown
Record what changed, what was verified, and any remaining operational setup.
```

- [ ] **Step 4: Commit**

```bash
git add progress.md
git commit -m "chore: record channel daily publish verification"
```

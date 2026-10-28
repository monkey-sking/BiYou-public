# Channel Daily Publish Design

## Goal

Build a channel-driven backlog workflow that preloads and preprocesses videos from `https://www.youtube.com/@RosarioCooking/videos`, then publishes exactly one item per day to Bilibili at `19:05` Asia/Shanghai.

## User-Facing Outcome

- New videos from the configured YouTube channel are pulled into a local backlog before publish time.
- The system prepares downloadable assets and upload metadata ahead of time so daily publishing does not depend on same-minute download success.
- Each day, exactly one prepared video is scheduled for Bilibili publish at `19:05`.
- Selection rule:
  - Prefer newly discovered channel items that are already prepared.
  - If there is no new prepared item, fall back to this same channel's local backlog.
  - Backlog fallback order is newest unpublished item first.

## Scope

In scope:

- One configured YouTube channel/videos source.
- Persistent backlog state for channel items.
- A sync workflow that discovers channel items and prepares them locally.
- A publish workflow that schedules one prepared item per day.
- Reuse of the existing metadata, cover, and upload pipeline.

Out of scope:

- Arbitrary local video fallback outside this channel.
- Multi-channel scheduling.
- UI changes.
- Packaging or release automation.

## Existing Constraints

- `main.py` only accepts direct YouTube video pages and rejects channel/videos URLs.
- `monitor.py` can read a channel/videos feed, but it only checks the newest single item and immediately runs the full pipeline.
- The existing workflow already supports per-video Bilibili scheduling through `--schedule`.
- The existing preprocessing pipeline can stop before upload via `--skip-upload`.

## Architecture

Introduce a new channel backlog workflow with two responsibilities:

1. Sync the channel feed into a persistent local inventory.
2. Publish one prepared inventory item per day at the configured time.

The new workflow should live alongside the existing `main.py` flow rather than replacing it. It should reuse:

- `main.step_download_video(...)`
- `main.step_fetch_metadata(...)`
- `main.step_process_cover(...)`
- `main.step_upload(...)`
- `main.step_cleanup(...)`

This keeps all existing metadata generation, cover generation, subtitle behavior, and upload semantics intact.

## Data Model

Add a persistent JSON state file for this feature, stored in the project workspace.

Recommended path:

- `history/channel_daily_publish.json`

Top-level shape:

```json
{
  "channel_url": "https://www.youtube.com/@RosarioCooking/videos",
  "publish_time": "19:05",
  "timezone": "Asia/Shanghai",
  "items": {
    "YOUTUBE_ID": {
      "youtube_id": "abc123",
      "source_url": "https://www.youtube.com/watch?v=abc123",
      "title": "Video title",
      "discovered_at": "2026-04-07T10:00:00+08:00",
      "prepared_at": "2026-04-07T10:12:00+08:00",
      "status": "prepared",
      "is_new": true,
      "video_path": "/abs/path/to/video.mp4",
      "metadata_path": "/abs/path/to/video.json",
      "scheduled_for": "2026-04-08T19:05:00+08:00",
      "uploaded_bvid": "BV...",
      "last_error": ""
    }
  }
}
```

Item statuses:

- `discovered` — seen in the channel feed, not yet prepared
- `preparing` — currently running through local preprocessing
- `prepared` — local assets ready and eligible for publish
- `scheduled` — submitted to Bilibili and scheduled
- `published` — marked published/completed locally
- `failed_prepare` — preprocessing failed
- `failed_publish` — publish attempt failed

## Sync Workflow

Add a sync command that:

1. Reads the configured channel feed.
2. Fetches the latest N items from the channel using `yt-dlp --flat-playlist`.
3. Inserts unseen items into the backlog as `discovered`.
4. Prepares unseen items locally by reusing the existing pipeline stages up to cover generation.
5. Marks successful items as `prepared`.

Recommended default: inspect the latest 20 feed items each sync run. This is enough to absorb missed runs without turning the feature into a full historical crawler.

Preparation behavior:

- Convert channel item ID into a standard watch URL.
- Run download + metadata + cover steps.
- Do not upload during sync.
- Persist resolved `video_path` and `metadata_path` back into the backlog item.

## Publish Workflow

Add a publish command that:

1. Loads the backlog.
2. Computes today's target timestamp at `19:05` Asia/Shanghai.
3. Skips work if an item is already scheduled for today's slot.
4. Selects one candidate item:
  - First: latest prepared item where `is_new=true`
  - Otherwise: latest prepared unpublished item from this channel backlog
5. Calls the existing upload flow with the computed schedule timestamp.
6. Marks the chosen item as `scheduled`.
7. Stores returned `uploaded_bvid` when available.

After a successful scheduled upload:

- Set `status = "scheduled"`
- Set `scheduled_for`
- Set `uploaded_bvid` if present
- Clear `is_new`

## Newness Semantics

`is_new` is a backlog priority flag, not a permanent classification.

- A newly discovered item starts with `is_new = true`
- Once it is selected for scheduling, set `is_new = false`
- Prepared but unscheduled items can still be selected later via backlog fallback

This matches the user's rule: latest newly discovered content gets first priority, but only until it has been consumed by the daily schedule.

## Failure Handling

Sync failures:

- Keep the item in `failed_prepare`
- Preserve `last_error`
- Allow the next sync run to retry failed items if their assets are incomplete

Publish failures:

- Keep the item in `failed_publish`
- Preserve `last_error`
- Do not mark it as consumed
- Allow the next publish run to retry it if it remains the best candidate

Duplicate protection:

- Do not schedule more than one item per calendar day for this workflow
- Do not select items already marked `scheduled` or `published`

## Commands and Entry Points

Introduce a dedicated entry script instead of mutating `monitor.py` into a larger mixed-mode tool.

Recommended script:

- `scripts/channel_daily_publish.py`

Suggested commands:

- `python3 scripts/channel_daily_publish.py sync`
- `python3 scripts/channel_daily_publish.py publish`
- `python3 scripts/channel_daily_publish.py run`

Where `run` performs:

1. sync
2. publish

This allows one scheduled task or cron entry to run the whole automation while keeping the responsibilities separate inside the code.

## Testing Strategy

Add targeted unit tests for:

- Channel URL inventory selection
- Candidate prioritization
- New item vs backlog fallback ordering
- One-per-day scheduling guard
- State transitions after prepare and schedule

Avoid real network or Bilibili writes in tests. Mock external boundaries and verify orchestration behavior.

## Risks

- Existing `main.py` helper functions are workflow-oriented and may require careful argument scaffolding when reused programmatically.
- If a scheduled upload succeeds but local state update fails, inventory and Bilibili can drift; write state updates defensively.
- If preprocessing leaves partially valid assets behind, retry logic must prefer repair over duplicate insertion.

## Recommended Implementation Direction

Implement the orchestration as a new workflow module plus a thin script entry point, and keep `main.py` unchanged except for reuse through imported helper functions. This minimizes regression risk in the existing direct-video workflow while adding the new channel backlog behavior in a focused slice.

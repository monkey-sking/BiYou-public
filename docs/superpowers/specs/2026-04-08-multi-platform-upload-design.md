# Multi-Platform Upload Design

## Goal

Expand BiYou from a Bilibili-specific uploader into a multi-platform publishing framework for major domestic platforms, with a stable first release for `Bilibili`, `Douyin`, and `Kuaishou`, plus beta automation adapters for `WeChat Channels` and `Xiaohongshu`.

## User-Facing Outcome

- One preprocessing pipeline prepares a video once, then publishes it to multiple domestic platforms.
- The first release supports the same core publish fields across all supported platforms:
  - title
  - description
  - cover
  - tags
  - schedule intent
- `Bilibili`, `Douyin`, and `Kuaishou` are treated as stable adapters.
- `WeChat Channels` and `Xiaohongshu` are supported as beta adapters through creator-backend browser automation.
- Existing Bilibili workflows continue working during the migration.

## Scope

In scope:

- A new cross-platform publishing core.
- A unified publish-job model for core publish fields.
- Per-platform adapters for:
  - `Bilibili`
  - `Douyin`
  - `Kuaishou`
  - `WeChat Channels` (beta automation)
  - `Xiaohongshu` (beta automation)
- Unified scheduling semantics for all platforms.
- Backward-compatible persistence of publish results.
- A staged migration path from the current Bilibili-only uploader.

Out of scope for the first release:

- Full parity for platform-specific features such as comments, collections/playlists, subtitle re-upload, topic/activity automation, app anchors, or other platform-specific enrichment.
- Packaging or release execution.
- Production-grade browser anti-bot work beyond a documented beta path.
- Replacing the existing preprocessing pipeline.

## Existing Constraints

- `core/modules/uploader.py` is tightly coupled to Bilibili through `biliup`, Bilibili category IDs, mission/topic handling, collection logic, and Bilibili-specific result fields.
- `main.py` Step 3 and cleanup logic assume Bilibili upload state, especially `uploaded_bvid`.
- `core/modules/commenter.py`, `core/modules/subtitle_sync.py`, `core/services/bili_api.py`, and `core/services/bili_collection_api.py` are all Bilibili-specific post-publish support layers.
- Existing metadata and runtime state are shaped around a single platform result, not per-platform publish records.

## Confirmed Product Decisions

- Rollout option:
  - Build a 5-platform-capable framework now.
  - Promise stable first-release support for `Bilibili`, `Douyin`, and `Kuaishou`.
  - Treat `WeChat Channels` and `Xiaohongshu` as beta automation integrations initially.
- “Platform support” means more than upload-only.
  - Minimum unified fields are title, description, cover, tags, and schedule intent.
- Browser automation is acceptable for the beta adapters.

## Platform Tiers

### Stable Tier

- `Bilibili`
- `Douyin`
- `Kuaishou`

Stable-tier requirements:

- Support the unified core publish fields.
- Return structured publish results.
- Classify failures consistently.
- Support either native scheduling or BiYou-managed scheduling.

### Beta Tier

- `WeChat Channels`
- `Xiaohongshu`

Beta-tier rules:

- Use the same unified publish-job input model.
- Allow browser automation as the execution backend.
- Expose explicit `beta` status in logs and results.
- Distinguish retryable failures from manual-intervention failures.
- Fulfill the shared core publish fields through either native UI fields or documented platform-equivalent mapping.

## Design Options

### Option A: Pure API Adapters Only

Build only public-API integrations and postpone unsupported platforms.

Pros:

- Lowest runtime fragility.
- Cleaner test surface.
- Easier auth and failure handling.

Cons:

- Does not satisfy the requested 5-platform framework in the first pass.
- Blocks `WeChat Channels` and `Xiaohongshu` from the initial architecture.

### Option B: Mixed `API + Automation` Adapters

Use official API integrations where available, and browser automation where public publish APIs are not confirmed or not sufficient.

Pros:

- Matches the requested rollout.
- Keeps stable platforms on stronger execution paths.
- Allows the framework to normalize one publish-job model across all five targets.

Cons:

- More complex adapter contracts.
- Requires separate verification expectations for stable vs beta adapters.
- Browser automation needs stronger failure classification and operator guidance.

### Option C: Automation-First for All Platforms

Use creator-backend automation everywhere.

Pros:

- Fastest theoretical coverage.
- One implementation style.

Cons:

- Highest maintenance cost.
- Weakest long-term stability.
- Throws away the benefits of official APIs for stable platforms.

## Recommended Direction

Choose **Option B: Mixed `API + Automation` Adapters**.

This is the only option that satisfies all approved product requirements at once:

- 5-platform framework now
- stable first release for three platforms
- unified core publish-field support
- acceptable beta coverage for the two remaining platforms

## Architecture

Keep the existing preprocessing pipeline and insert a new publishing layer after metadata and cover preparation.

Recommended structure:

1. Reuse existing preprocessing:
   - download
   - metadata generation
   - translation
   - cover generation
2. Add a shared publishing core:
   - validate assets
   - normalize publish input
   - select the target adapter
   - execute publish or schedule
   - persist results
3. Keep platform-specific logic inside adapters.

### Recommended Module Layout

- `core/publishing/models.py`
- `core/publishing/manager.py`
- `core/publishing/adapters/base.py`
- `core/publishing/adapters/bilibili.py`
- `core/publishing/adapters/douyin.py`
- `core/publishing/adapters/kuaishou.py`
- `core/publishing/adapters/wechat_channels.py`
- `core/publishing/adapters/xiaohongshu.py`

The existing `core/modules/uploader.py` should not become the multi-platform hub. It should either remain as a temporary legacy Bilibili path or be gradually reduced as Bilibili logic moves into the new adapter layer.

## Unified Data Model

Introduce a platform-neutral publish-job model.

Suggested core shape:

```json
{
  "video_path": "/abs/path/video.mp4",
  "title": "Title",
  "description": "Description",
  "cover_path": "/abs/path/cover.jpg",
  "tags": ["tag1", "tag2"],
  "schedule_intent": {
    "mode": "publish_now"
  },
  "platform_targets": ["bilibili", "douyin"]
}
```

Suggested scheduled variant:

```json
{
  "schedule_intent": {
    "mode": "publish_at",
    "publish_at": "2026-04-08T19:05:00+08:00"
  }
}
```

Platform-specific fields should live in adapter extensions instead of polluting the shared model.

The shared contract should treat the core fields as required input semantics, not necessarily one-to-one native platform parameters. An adapter may satisfy a core field through platform-equivalent behavior.

Examples:

- Bilibili:
  - category/tid
  - mission/topic/activity
  - collection/season
- Douyin:
  - future platform-specific publish options
- Kuaishou:
  - future mini-program or mount-related options

## Adapter Contract

Each adapter should expose:

- `platform_name`
- `stability_tier`
- `execution_mode`
  - `api`
  - `automation`
- `capabilities`
  - `supports_native_schedule`
  - `supports_cover`
  - `supports_tags`
  - `supports_description`
- `validate(job)`
- `publish(job)`
- `schedule(job, publish_at)`

Result shape should be normalized, for example:

```json
{
  "platform": "bilibili",
  "status": "published",
  "native_schedule": true,
  "platform_content_id": "BV1...",
  "platform_url": "https://...",
  "published_at": "2026-04-08T19:05:00+08:00",
  "last_error": ""
}
```

Capability flags describe native support, not whether the shared publish-job field is accepted at all. For example, a platform may have `supports_tags = false` natively while the adapter still maps tags into a supported platform-specific representation.

## Scheduling Semantics

The shared publishing layer should support exactly two schedule intents:

- `publish_now`
- `publish_at(<datetime>)`

Scheduling behavior:

- If an adapter supports native schedule, the manager should use the adapter’s native scheduling path.
- If an adapter does not support native schedule or the native path is not reliable enough for the chosen tier, BiYou should keep the job and trigger publishing at the target time itself.
- This allows all five platforms to present a unified “supports scheduled publishing” story, even if the backend execution differs.

Persist the scheduling path per result:

- `native_schedule = true|false`
- `scheduled_by = "platform" | "biyou"`

## Execution Modes

### API Mode

Used for stable adapters where official capabilities are available or already proven.

Expected properties:

- structured auth
- more reliable error codes
- easier retry policies

### Automation Mode

Used for beta adapters where creator-backend publishing is accepted.

Expected properties:

- browser session dependency
- UI selector fragility
- higher manual-intervention risk

Automation adapters must explicitly surface when a run cannot continue safely without user action.

## Publish State Model

Recommended state values:

- `prepared`
- `scheduled`
- `publishing`
- `published`
- `failed_retryable`
- `failed_manual`

State guidance:

- `failed_retryable`:
  - transient API/network errors
  - temporary platform throttling
  - recoverable automation timing issues
- `failed_manual`:
  - login expired
  - verification/captcha
  - broken page flow
  - unsupported required manual confirmation

This distinction is especially important for beta automation adapters.

## Result Persistence

Replace single-platform publish state with per-platform records.

Recommended metadata shape:

```json
{
  "publish_records": {
    "bilibili": {
      "status": "published",
      "platform_content_id": "BV1...",
      "platform_url": "https://...",
      "native_schedule": true,
      "scheduled_by": "platform",
      "last_error": ""
    },
    "douyin": {
      "status": "scheduled",
      "platform_content_id": "123456789",
      "platform_url": "https://...",
      "native_schedule": true,
      "scheduled_by": "platform",
      "last_error": ""
    }
  }
}
```

## Migration and Backward Compatibility

Use a staged migration instead of a hard cutover.

### Phase 1 Compatibility

- Keep `core/modules/uploader.py` as the legacy Bilibili path.
- Update `main.py` Step 3 to route by configuration:
  - legacy Bilibili mode
  - new publish-manager mode
- Dual-write publish results:
  - new `publish_records.*`
  - legacy `uploaded_bvid` / `uploaded_aid` for Bilibili compatibility

### Phase 2 Compatibility Cleanup

- Move dependent modules to read from `publish_records` first.
- Keep fallback reads for old Bilibili fields until adjacent modules are migrated.
- Reduce legacy uploader responsibilities once the Bilibili adapter is trusted.

This avoids breaking cleanup, comments, subtitle sync, and other existing Bilibili post-publish flows during the first rollout.

## Failure Handling

The biggest release risk is not “publish failed.” It is “publish succeeded but local state is wrong.”

Required behavior:

- Persist result updates defensively and separately per platform.
- Never allow one platform result to overwrite another.
- Record:
  - final state
  - schedule path
  - content ID
  - content URL
  - last error
- Treat manual-intervention states as first-class outcomes, not generic failures.

## Testing Strategy

### Shared Publishing Core

Add tests for:

- asset validation failures
- capability downgrade behavior
- native schedule vs BiYou-managed schedule branching
- result persistence per platform
- multi-platform writes that do not overwrite each other

### Stable Tier Adapters

Add:

- adapter contract tests
- field-mapping tests
- error-classification tests
- scheduling-path tests

### Beta Tier Adapters

Use lighter verification gates:

- static flow checks
- selector and step-shape tests where possible
- narrow manual acceptance scripts

Beta adapters should not block stable-tier release readiness if they are marked beta and isolated correctly.

## Delivery Order

Recommended implementation sequence:

1. Build the shared models and publish manager.
2. Add the Bilibili adapter and keep compatibility with the current path.
3. Add the Douyin adapter.
4. Add the Kuaishou adapter.
5. Add the `WeChat Channels` beta automation adapter.
6. Add the `Xiaohongshu` beta automation adapter.

This yields usable checkpoints after every major step and avoids waiting for all five adapters before validating the design.

## Risks

- The current Bilibili-first workflow has deep assumptions in cleanup and post-publish helpers.
- Browser automation adapters are vulnerable to login drift and UI changes.
- Schedule semantics can drift if result persistence is weaker than execution reliability.
- If old and new result fields diverge during migration, downstream flows can behave inconsistently.

## References

- Douyin publish solution:
  - https://open.douyin.com/platform/resource/docs/ability/content-management/douyin-publish-solution
- Douyin create-video APIs:
  - https://open.douyin.com/platform/resource/docs/openapi/video-management/douyin/create/create-video/
- Kuaishou batch video mount capability:
  - https://open.kuaishou.com/docs/develop/functionAccessGuide/apivideomount
- Kuaishou video publish capability:
  - https://open.kuaishou.com/docs/develop/functionAccessGuide/fksVideoMount
- Xiaohongshu official open docs reference checked during scoping:
  - https://school.xiaohongshu.com/en/open/product/summary.html

## Recommended Next Step

Write an implementation plan that splits the work into:

- shared publish core
- metadata/result migration
- Bilibili adapter migration
- Douyin adapter
- Kuaishou adapter
- beta automation adapters
- verification and rollout safeguards

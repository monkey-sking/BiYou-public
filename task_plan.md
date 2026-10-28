# Task Plan: feature/multi-platform-upload

## Goal
把 BiYou 从“B 站专用上传流程”扩成“国内主流平台可扩展上传框架”，并完成第一期实现：`Bilibili` / `Douyin` / `Kuaishou` 稳定适配，`WeChat Channels` / `Xiaohongshu` beta 自动化适配。

## Current Phase
Live Batch Publish

### Current Focus
- New workstream `feature/bilibili-resource-link-kb`:
  - Goal: keep YouTube resource links available after Bilibili strips external links from descriptions.
  - Scope: extract and classify source links, save them in metadata, persist BVID-keyed SQLite KB rows after publish, post a resource DM notice comment, and add a testable Bilibili DM monitor.
  - Acceptance: metadata has `resource_links`; published BVIDs with resource links reach SQLite; comments use the DM notice when resources exist; DM auto-reply can be verified without live Bilibili traffic.
  - Risk: Medium because this touches metadata shape, post-publish behavior, and authenticated Bilibili private-message APIs.

- 当前工作流切到 `feature/batch-run-2026-04-29-youtube-links`：
  - 目标：对用户给出的 5 条 YouTube 链接执行真实发布
  - 链接：
    - `https://youtu.be/5nulzE_d3Po?si=BxGF5H5-ybUHpkgY`
    - `https://youtu.be/oUeOo5ESftc?si=ne_EwtbqD1lUhQZc`
    - `https://youtu.be/yzajLZXh9JU?si=XnUnM_xwoOggTIOH`
    - `https://youtu.be/4gZkdcGjab8?si=yHtwhP0ywBEvnnHG`
    - `https://youtu.be/yYP-ah-nEAM?si=S0LPDxYdXWupQ0bi`
  - 实际发布命令：`python3 main.py <5 urls> --skip-cover -y`
  - 验证口径：runtime logs + archived metadata
  - 结果：
    - 真实批处理完成：`成功: 5` / `失败: 0`
    - 第 1 条已存在历史发布记录，本轮跳过重复视频发布；Bilibili 评论已发布并置顶，历史 BVID 为 `BV1W4Q7BJE9y`
    - 第 2 条 Bilibili 已发布：`BV1rk9mBqEtW`；Kuaishou 已点击发布但未能从视频管理页验证，归档为 `failed_retryable`
    - 第 3 条 Bilibili 已发布：`BV1og9mBbEu6`；Kuaishou 归档为 `published`
    - 第 4 条 Bilibili 已发布：`BV1LV9mBtEu7`，评论已发布并置顶；Kuaishou 因 `1:00:16` 超过创作者中心 `60:00` 限制，归档为 `failed_manual`
    - 第 5 条 Bilibili 已发布：`BV1RV9mB4Ey7`；Kuaishou 归档为 `published`
  - 运行警告：
    - Gemini Web API 多次 180s 超时，已通过浏览器 fallback 完成封面生成
    - 第 5 条视频下载先遇到 SSL EOF / fragment 错误，后通过兼容单文件 fallback 成功并通过完整性校验
    - 第 2 条 Kuaishou 发布结果未能从 creator manager 验证
    - 第 4 条 Kuaishou 受平台 60 分钟时长限制阻断
    - 结束时 `.last_url` 缓存清理报 missing-file warning，不影响最终成功计数

Previous focus retained for historical context:
- 当前工作流已切到 `fix/live-platform-reruns`：
  - 用户当前目标仍是把两条目标视频 `fv9vA6RCNHU` / `DJC-re1i_xE` 尽量收敛到真实 5 平台结果，而不是停在代码层面的“理论支持”
  - 最新 live truth 已更新为：
    - `fv9vA6RCNHU`
      - `Bilibili`: published
      - `Douyin`: published
      - `Kuaishou`: published
      - `Xiaohongshu`: failed_manual
      - `WeChat Channels`: failed_manual
    - `DJC-re1i_xE`
      - `Bilibili`: published
      - `Douyin`: published
      - `Kuaishou`: published
      - `Xiaohongshu`: failed_retryable
      - `WeChat Channels`: failed_manual
  - 本轮新增确认：
    - `Douyin` 第二条视频的失败是验证假阴性，不是发布失败
    - `Xiaohongshu` 两条视频的封面上传都已在 live run 中确认成功
    - 第一条 `Xiaohongshu` 的 `-9136` 已经在中性文案/中性封面重试后稳定复现，可视为平台审核拦截
    - `WeChat Channels` 仍然只是登录门槛，没有暴露出更深的表单/selector 问题
  - 当前新的 runtime finding：
    - `Xiaohongshu` 页面出现 `转码中，不影响发布` 时，旧逻辑仍会因为 `当前速度/剩余时间` 误判为上传阻塞；该逻辑已修正
  - 下一步：
    - 只剩两个需要人工或更深平台侧介入的 blocker：
      - `WeChat Channels` creator-center login
      - 第二条 `Xiaohongshu` 的“发布后未验证到结果”问题

## Phases

### Phase 1: Requirements & Scope
- [x] Understand the user's target outcome
- [x] Confirm this is a new feature workstream, not a narrow fix
- [x] Confirm whether first release should optimize for platform count or stable delivery
- [x] Confirm what counts as "support" (upload only vs upload + cover + scheduling + post-publish actions)
- **Status:** complete

### Phase 2: Capability & Repo Research
- [x] Inspect current upload architecture and Bilibili-specific coupling
- [x] Check current official capability signals for major domestic platforms
- [x] Finalize Tier 1 / Tier 2 platform split and auth model
- **Status:** complete

### Phase 3: Design & Spec
- [x] Propose 2-3 architecture approaches with trade-offs
- [x] Present recommended adapter/model design to user
- [x] Write design spec after user approval
- **Status:** complete

### Phase 4: Implementation Plan
- [x] Write a detailed implementation plan with file ownership and validation strategy
- [x] Split work into upload core, platform adapters, metadata model, and post-publish actions
- [x] Define branch naming and verification scope
- **Status:** complete

### Phase 5: Delivery
- [x] Summarize the approved scope, risks, and next action
- [x] Hand off a concise acceptance card to the user
- [x] Keep planning files aligned with the chosen direction
- **Status:** complete

## Key Questions
1. 第一期更重要的是“尽快稳定跑通 3 个平台”，还是“先把 5 个主流平台框架都铺上，即使后两者先走自动化方案”？
   Answer: 选择方案 1，5 平台框架一次铺好，先承诺 3 平台稳定可用，视频号/小红书先做 beta 自动化
2. 你说的“支持平台”最低标准是什么：只要能发视频就算，还是要把封面、标题描述、标签、定时发布、合集/评论也一起统一进去？
   Answer: 核心发布字段都要打通，至少包括标题、简介、封面、标签、定时发布
3. 视频号和小红书 beta 版能否先接受创作者后台浏览器自动化？
   Answer: 可以

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Treat this as `feature/multi-platform-upload` | This changes product scope and core upload architecture |
| Start in design mode before code changes | Required by brainstorming workflow and appropriate for a high-impact feature |
| Use the current repo state as the baseline | Existing upload flow is tightly coupled to Bilibili and must be measured before refactor |
| Use a tiered platform rollout recommendation | Official platform capability differs enough that one-phase parity is risky |
| Use rollout option `1` | User chose “5-platform framework now, 3-platform stable first, WeChat Channels/Xiaohongshu as beta automation” |
| Define first-release support as “core publish fields unified” | User wants title, description, cover, tags, and schedule intent all wired through the platform layer |
| Accept browser-automation beta adapters for WeChat Channels and Xiaohongshu | User explicitly approved automation-first beta integrations for those two platforms |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| `session-catchup.py` has no Codex-native catchup yet | 1 | Proceeded by reading the existing planning files directly |
| Local `published` status was treated as end-to-end success | 1 | Switched success criteria to platform-backend verification, not local metadata only |
| Live browser profile was corrupted by `launch_persistent_context(...)` on the shared profile | 1 | Reworked automation to reuse the already-running BY browser via CDP and stopped using persistent context on the live profile |
| A shared `cover_path` was not enough for platform-specific cover/title tuning | 1 | Added platform payload/cover asset layering so later videos can derive per-platform cover variants from the shared base cover |
| Upload preflight still enforced Bilibili's `4:3` cover for non-Bilibili target sets | 1 | Made asset validation target-aware so non-Bilibili publish runs only require the cover assets their target set actually uses |
| YouTube metadata preflight passed but `yt-dlp` still hit Google's anti-bot login wall | 1 | Downloader now tries browser-sniff metadata/direct-stream fallback before opening the manual YouTube login prompt |
| A 1:00:23 source video made Douyin wait the full post-upload form timeout and aborted the rest of the platform batch | 1 | Douyin now fails fast on the `60:00` creator-center limit and `PublishManager` records platform exceptions instead of aborting later targets |

## Notes
- Risk level: High, because this feature touches core workflow, metadata shape, auth, and post-publish behavior.
- Local recommendation so far: Tier 1 = `Bilibili`, `Douyin`, `Kuaishou`; Tier 2 = `WeChat Channels`, `Xiaohongshu`.
- Auth and execution recommendation so far: API-first adapters for Tier 1 where official capabilities exist, automation adapters for Tier 2 beta.
- Written design spec: `docs/superpowers/specs/2026-04-08-multi-platform-upload-design.md`
- Written implementation plan: `docs/superpowers/plans/2026-04-08-multi-platform-upload.md`
- Previous `feature/channel-daily-publish` workstream is already archived in `docs/superpowers/specs/` and `docs/superpowers/plans/`.

## Implementation Status
- [x] Task 1: Added shared publishing models, adapter contract, registry, record helpers, and managed scheduler state in `core/publishing/`
- [x] Task 2: Preserved Bilibili legacy ID compatibility via `publish_records` mirroring and lookup helpers
- [x] Task 3: Added `PublishingManager` with preflight validation before any platform side effects
- [x] Task 4: Added stable `Bilibili` adapter and covered native scheduling behavior
- [x] Task 5: Routed `main.py` through the publish manager when `publishing.enabled` is true while preserving the legacy upload path
- [x] Task 6: Added stable `Douyin` adapter and phase-1 BiYou-managed scheduling guardrail
- [x] Task 7: Added stable `Kuaishou` adapter and phase-1 BiYou-managed scheduling guardrail
- [x] Task 8: Added shared beta automation adapter base on `playwright`
- [x] Task 9: Added `WeChat Channels` beta automation adapter
- [x] Task 10: Added `Xiaohongshu` beta automation adapter
- [x] Task 11: Updated docs/config examples and added regression coverage across publishing core, routing, adapters, scheduler, and Bilibili compatibility

## Validation Status
- Fresh verification run: `python3 -m unittest tests.test_publishing_models tests.test_publish_records tests.test_publishing_scheduler tests.test_publishing_manager tests.test_bilibili_adapter tests.test_publish_routing tests.test_douyin_adapter tests.test_kuaishou_adapter tests.test_automation_adapter_base tests.test_wechat_channels_adapter tests.test_xiaohongshu_adapter tests.test_commenter -v`
- Result: `61` tests passed, `0` failures
- Latest hardening regression run: `python3 -m unittest tests.test_downloader tests.test_browser_downloader tests.test_platform_payloads tests.test_publish_routing tests.test_douyin_adapter tests.test_douyin_automation_adapter tests.test_wechat_channels_adapter tests.test_xiaohongshu_adapter tests.test_publishing_manager -v`
- Result: `80` tests passed, `0` failures
- Latest payload/selector regression run: `python3 -m unittest tests.test_platform_payloads tests.test_kuaishou_adapter tests.test_kuaishou_automation_adapter tests.test_wechat_channels_adapter tests.test_xiaohongshu_adapter tests.test_douyin_adapter tests.test_publish_routing -v`
- Result: `72` tests passed, `0` failures
- Latest verification-hardening regression run: `python3 -m unittest tests.test_douyin_automation_adapter tests.test_wechat_channels_adapter tests.test_publish_routing tests.test_platform_payloads tests.test_kuaishou_automation_adapter tests.test_xiaohongshu_adapter -v`
- Result: `91` tests passed, `0` failures
- Latest automation-selector regression run: `python3 -m unittest tests.test_automation_adapter_base tests.test_kuaishou_automation_adapter tests.test_wechat_channels_adapter tests.test_xiaohongshu_adapter tests.test_douyin_automation_adapter -v`
- Result: `106` tests passed, `0` failures
- Latest WeChat parser regression run: `python3 -m unittest tests.test_wechat_channels_adapter tests.test_automation_adapter_base tests.test_kuaishou_automation_adapter tests.test_xiaohongshu_adapter tests.test_douyin_automation_adapter -v`
- Result: `107` tests passed, `0` failures
- Latest Kuaishou verification regression run: `python3 -m unittest tests.test_wechat_channels_adapter tests.test_kuaishou_automation_adapter tests.test_automation_adapter_base tests.test_xiaohongshu_adapter tests.test_douyin_automation_adapter -v`
- Result: `108` tests passed, `0` failures
- Latest browser-lifecycle regression run: `python3 -m unittest tests.test_automation_adapter_base tests.test_wechat_channels_adapter tests.test_kuaishou_automation_adapter tests.test_xiaohongshu_adapter tests.test_douyin_automation_adapter -v`
- Result: `109` tests passed, `0` failures
- Latest cross-platform verification regression run: `python3 -m unittest tests.test_kuaishou_automation_adapter tests.test_douyin_automation_adapter tests.test_wechat_channels_adapter tests.test_xiaohongshu_adapter tests.test_publishing_manager tests.test_publish_routing -v`
- Result: `103` tests passed, `0` failures
- Additional hardening fix landed after full-suite verification exposed a regression in `core/modules/commenter.py` keyword strictness for general Unreal/UE content
- Remaining release risk is concentrated in real credential/manual acceptance for `Douyin`, `Kuaishou`, `WeChat Channels`, and `Xiaohongshu`, especially the two beta automation adapters

## Runtime Wiring Status
- [x] `main.py` now resolves publish targets from `publishing.default_targets` instead of hard-coding `bilibili`
- [x] Runtime adapter assembly now supports:
  - `BilibiliAdapter`
  - `DouyinAdapter` with `publishing.adapters.douyin.access_token`
  - `KuaishouAdapter` with `publishing.adapters.kuaishou.access_token` + `app_id`
  - `WechatChannelsAdapter` / `XiaohongshuAdapter` with `publishing_automation` or `publishing.automation_config_file`
- [x] `run_multi_platform_publish(..., dry_run=True)` now validates multi-platform runtime wiring instead of returning before adapter selection
- [x] Config/docs now expose the real runtime fields needed to configure non-Bilibili targets

## Live Verification Status
- [x] `Bilibili` has a platform-confirmed publish result
- [x] `Douyin` has one platform-confirmed live publish (`7626940298626632966`) and one newer run that reached publish but could not be re-verified from creator center
- [x] `Kuaishou` has two live runs verified via creator manager, but the latest 60-minute workout surfaced a new description-field selector drift
- [x] `Xiaohongshu` now has live publish success on the latest two workout reruns after the title/verification fixes
- [ ] `WeChat Channels` still needs a verified-success live run; recent attempts keep ending at “publish result could not be verified from video list”
- [x] `Douyin` platform-length limits are now enforced before automation starts, so oversized videos fail clearly instead of stalling the rest of the batch

## Current Focus
- Treat the remaining work as `fix/live-platform-verification` follow-ups under the larger feature branch
- Keep platform-specific payload shaping and reusable cover variants as the default for future videos
- Platform optimization todo backlog (recommended execution order):
  1. Source intake & archive hygiene:
     - detect when historical archives still carry stale `platform_payloads` / `cover_assets` / old selector-era artifacts
     - add safer rerun refresh rules for title/description/tags/cover/video assets instead of only patching individual edge cases
     - normalize per-video metadata shape so later platform logic does not depend on ad-hoc historical keys
  2. Metadata rewrite layer:
     - make `Douyin / Kuaishou / WeChat Channels / Xiaohongshu` titles truly platform-native instead of only truncating the shared title
     - rewrite short-form descriptions/captions/bodies per platform instead of only compressing the Bilibili description
     - upgrade tag/topic generation from simple tag caps to platform-aware topic sets and hashtag/body placement
     - add platform-sensitive wording rules for fitness / tech / AI / tutorial / vlog domains instead of one shared heuristic pool
     - add banned/awkward phrase cleanup so Bilibili-style CTA or markdown residue never leaks into short-video platforms
  3. Cover & preview-asset layer:
     - finish the platform-specific cover-spec rollout with real sample regeneration and acceptance review
     - detect and trim black frames / empty intros before short-video publishing
     - choose or derive a stronger preview frame when a platform falls back to the video frame instead of the uploaded cover
     - decide when a platform needs portrait or alternate aspect assets beyond the current cover-only differentiation
     - add final checks that the live page actually accepted the intended cover file instead of silently falling back
  4. Video-asset adaptation layer:
     - derive platform-specific video files when duration / aspect / intro pacing / codec requirements differ
     - add short-video intro tightening and optional tail cleanup instead of sending the same long-form master everywhere
     - validate resolution / fps / bitrate / audio expectations before upload so platform failures move earlier in the pipeline
     - support explicit per-platform video-path overrides as a first-class strategy, not just isolated platform exceptions
  5. Subtitle / on-video-text layer:
     - decide which platforms should ship subtitles / burned-in text / no subtitles by default
     - optimize subtitle timing/length for short-video readability instead of only Bilibili upload compatibility
     - keep cover wording, title wording, and subtitle hook language aligned so the first seconds match the promise of the post
  6. Platform default-options layer:
     - promote important automation-only options into stronger defaults, not just passthrough fields
     - add platform-specific default policies for `visibility / allow_comment / declare_original / collection_name / poi_name`
     - keep room for later platform-specific switches such as series/collection routing, originality declarations, and audience controls
     - distinguish “supported in payload” from “wired to a real backend control/API field” and close the gaps systematically
  7. Platform taxonomy / routing layer:
     - improve Bilibili `category / mission_id / collection` handling beyond the current good-enough baseline
     - add equivalent platform-native grouping/routing logic where available for `Douyin / Kuaishou / WeChat Channels / Xiaohongshu`
     - map the same source content into different series/栏目/合集 strategies per platform instead of assuming one grouping fits all
  8. Scheduling & campaign layer:
     - add platform-aware publish-time defaults instead of one shared schedule intent
     - support staggered multi-platform release instead of one timestamp for all targets
     - improve rerun behavior so platform retries can selectively refresh payloads/assets without disturbing healthy targets
     - add operator-facing reasons when scheduling falls back from native to BiYou-managed behavior
  9. Compliance & risk-control layer:
     - detect platform-sensitive terms/claims in titles, cover text, descriptions, and hashtags before publish
     - add per-platform validation for duration, wording, originality declarations, and likely moderation triggers
     - surface “manual review expected” vs “likely blocked” before we click publish when the page already exposes warning signals
  10. Publish execution robustness:
      - harden selector config so cover upload, title fill, description fill, option controls, and publish confirmation are all independently verifiable
      - add positive checks that the actual file/control changed, not only that a selector accepted input
      - reduce hidden fallback behavior where the workflow appears successful but the platform silently uses defaults
  11. Post-publish verification & evidence:
      - verify that the final platform record really uses the expected cover/content identity, not only that a post exists
      - capture cleaner publish-result evidence for operator review when automation platforms end in review/pending states
      - distinguish `published`, `under_review`, `posted_but_cover_unknown`, `posted_but_url_unknown`, and `manual_gate` more clearly in records
      - keep a tighter archive of final platform URLs, content IDs, publish times, and verification source
  12. Comments / engagement / follow-up:
      - Bilibili comment automation exists, but equivalent per-platform engagement strategy is still mostly absent
      - decide whether platforms like `Douyin / Kuaishou / Xiaohongshu` need first-comment templates, pinned-comment support, or follow-up reminders
  13. Operator experience & configuration:
      - reduce the amount of per-platform selector hand-editing needed in `publishing_automation.yaml`
      - expose a clearer matrix of which platform fields are `not supported / payload-only / fully wired`
      - add safer defaults for browser profile reuse, login/session checks, and platform-specific override ergonomics
  14. Test coverage & fixtures:
      - add richer fixtures for real historical videos with stale payloads and platform assets
      - add regression coverage for platform video-asset adaptation, compliance preflight, and evidence capture
      - keep live-run learnings flowing back into deterministic tests instead of only fixing the last failure
- Latest hardening landed for `Douyin` / `WeChat Channels` verification:
  - `Douyin` now supports wider `work_list` response parsing and retries backend verification instead of only checking once
  - `WeChat Channels` now supports wider `post_list` response parsing and matches the expected short title when extracting the final record ID
- Latest hardening landed for `Douyin` cover upload:
  - the automation payload now preserves `cover_path` instead of dropping it
  - `DouyinAutomationAdapter` now attempts configured cover-image upload instead of always falling back to platform default frames
- Latest hardening landed for platform-specific publish options:
  - per-video `platform_options` now merges into generated platform payloads instead of relying only on prebuilt `platform_payloads`
  - short-video adapters now preserve extra platform-specific keys during normalization instead of discarding them
  - `Bilibili` now auto-lifts common top-level publish metadata such as `category`, `mission_id`, `copyright`, and `suggested_collection` into multi-platform payloads
- Focus the next step on real reruns for `Douyin` and `WeChat Channels` to confirm these verification fixes against the live creator backends
- Latest hardening landed for `Kuaishou` description-selector drift:
  - payload generation now emits shorter platform-native descriptions/tags for `Douyin` / `Kuaishou` / `WeChat Channels` / `Xiaohongshu`
  - legacy archived payloads are now refreshed during reruns when they still carry noisy long descriptions or oversize tag lists from older Bilibili-style metadata
  - `Kuaishou` automation now searches page + child frames and no longer waits only on the description selector
- Latest hardening landed for automation-platform option controls:
  - `AutomationAdapterBase` now supports reusable `fill / click / choice / boolean` option actions driven by selector config
  - `Douyin` / `Kuaishou` / `WeChat Channels` / `Xiaohongshu` now call the option-control layer during publish, so configured fields can be applied to live form widgets before the final publish click
  - `config/publishing_automation*.yaml` now includes starter `option_controls` examples for common fields like `visibility`, `allow_comment`, `declare_original`, `collection_name`, and `poi_name`
- Latest live DOM calibration landed for selector config:
  - confirmed `Kuaishou` pre-upload upload button exposes a stable `button[class*='_upload-btn_']` selector
  - confirmed `Xiaohongshu` pre-upload hidden file input exposes `input.upload-input`
  - confirmed `WeChat Channels` publish UI lives in frame `content` and exposes the expected short-title / description / publish controls in direct DOM inspection even when Playwright locator counts still return `0`
  - tightened config selectors accordingly while keeping `AutomationAdapterBase` DOM fallbacks as the critical compatibility path for `WeChat Channels`
- Latest selector-readiness hardening landed in the automation base:
  - `_wait_for_any_selector(...)` no longer requires editability and now treats visible non-editable controls such as publish buttons, switches, and confirm rows as valid readiness hits
  - this reduces reliance on DOM fallback for `form_ready`, `publish_button`, and option-control confirmation paths across the automation adapters
- Latest WeChat post-list parsing hardening landed from a live backend replay:
  - the current `post_list` response exposes the publish title in `shortTitle`, while nested `desc` objects carry large media metadata payloads
  - snapshot extraction now counts only ID-bearing rows and extracts clean title candidates from `shortTitle`/structured title containers instead of stringifying nested metadata dicts
  - this removes a real false-negative/false-count path in `WeChat Channels` verification where baseline and post-publish snapshots could be polluted by nested payload objects
- Latest Kuaishou manager verification hardening landed from a live DOM review:
  - the creator manage page exposes stable `.video-item` rows with dedicated title/status content once the page settles
  - verification now polls for a matching row-level status signal before falling back to the older whole-page text check
  - this reduces false negatives when the page shell loads earlier than the fully searchable whole-page body text
- Latest Kuaishou verification-identity hardening landed:
  - manager-row verification now preserves row-level `platform_content_id/platform_url` when the creator backend exposes them through links or row data attributes
  - plain verification misses now map to `failed_retryable`, while explicit page-validation failures remain `failed_manual`
- Latest Kuaishou bare-ID hardening landed:
  - manager-row verification now also accepts creator-manager variants that expose only a bare `platform_content_id` without a usable public URL
  - public `kuaishou.com/short-video/...` URLs are now reconstructed from that bare ID instead of returning an empty publish identity
- Latest automation-config docs hardening landed:
  - `README.md` / `README_zh.md` now describe the per-platform field matrix instead of only the top-level “5 platforms” summary
  - `config/config.example.yaml` now documents `Douyin` / `Kuaishou` automation mode as the primary browser-workflow example, with API mode kept as an explicit alternative
- Latest platform-cover spec hardening landed:
  - non-`Bilibili` cover variants no longer share a single implicit `16:9` spec
  - `Douyin` now keeps text inside a centered `3:4` safe area
  - `WeChat Channels` now keeps text inside a centered `6:7` safe area
  - `Xiaohongshu` now generates a true `3:4` portrait cover variant
  - reruns now refresh stale payload `cover_path` values when a newer platform asset exists
- Latest Douyin status hardening landed:
  - plain creator-center verification misses now map to `failed_retryable`
  - explicit SMS/security/manual-confirmation paths still map to `failed_manual`
- Latest WeChat verification-status hardening landed:
  - plain `post_list` verification misses now map to `failed_retryable` instead of `failed_manual`
- Latest shared browser-lifecycle hardening landed in the automation base:
  - scoped cleanup no longer calls `browser.close()` on the shared CDP browser connection
  - reused matching tabs are no longer closed on task exit
  - task-opened pages are still closed
  - task-launched browser instances can still be closed on exit, so dedicated automation browser windows do not leak while shared workflow sessions remain intact
- Latest workflow-browser recovery check confirmed the issue was a dead process, not missing login state:
  - the configured workflow profile still had state on disk
  - Chrome was relaunched successfully on `39222`
  - live read-only probes confirmed `Bilibili / Douyin / Kuaishou / Xiaohongshu / WeChat Channels` are all still logged in on the workflow profile
- Latest shared cleanup hardening landed outside the publish adapters too:
  - `browser_downloader` now uses task-owned cleanup instead of closing the shared CDP browser unconditionally
  - `scripts/clean_gemini_browser.py` now uses a task-owned tab and no longer closes the shared workflow browser
- Latest cover-stage/browser-ownership hardening landed:
  - `browser_gemini.close_browser()` no longer force-kills the shared workflow browser unless the current task actually owns the detached debug browser it launched
  - live verification confirmed calling `close_browser()` with `_OWNS_DEBUG_BROWSER=False` keeps `39222` alive
- Latest platform-cover fallback hardening landed:
  - failed platform-specific cover assets are no longer preferred during payload building
  - upload pre-checks now validate target-resolved platform cover paths before publish starts
- Latest Kuaishou frame-target hardening landed:
  - cross-target option control handling no longer resets the active DOM target back to the top-level page when the editor was discovered inside a frame
  - upload-processing and validation checks now stay anchored to the same active target that accepted the description fill
- Latest WeChat verification tightening landed:
  - only real backend post identifiers (`objectId` / `postId` / `exportId`) count as publish records
  - `post_list` verification now requires a matched backend row instead of treating unrelated `count` or ID-set changes as success
- Latest preflight/auth hardening landed:
  - `Bilibili` preflight now falls back to validating the live workflow-browser session over CDP when local cookie extraction/decryption fails
  - `-y` auto-confirm no longer blocks on `input(...)` during `Bilibili` / `Gemini` auth failures
- Latest `Douyin` publish-click hardening landed:
  - automation now detects `semi-modal` pointer-intercept failures on the final `发布` click
  - `publish_modal_confirm` / `publish_modal_close` selectors can clear the blocking dialog before one publish-click retry
- Latest `Gemini` workflow-bootstrap hardening landed:
  - blank BY browser tabs are no longer reused for Gemini session probing
  - live Gemini tabs are still reused, but non-Gemini startup now opens a fresh page instead of risking a blank-tab hang
- Latest live run status for `TUAyiCswYt4` after the first full publish attempt:
  - `Bilibili`: published (`BV1DWQcBjEgj`)
  - `Kuaishou`: published
  - `Xiaohongshu`: published
  - `Douyin`: failed on a modal-intercepted publish click; now patched for the next rerun
  - `WeChat Channels`: failed_retryable on backend verification
- Current live queue:
  - `TUAyiCswYt4` finished one full run and established the latest adapter/runtime fixes
  - `NoI3GyDvyKI` is now being processed next on the updated code path
- Preserve the new rule that one platform's runtime failure must not abort the remaining publish targets

# BiYou: YouTube <-> Bilibili Automation Tool

**[中文文档](README_zh.md)** | English

**BiYou** aims to build a **Cross-platform Video Synchronization & Localization Solution**. Beyond YouTube to Bilibili sync, we are expanding into **Bi-directional Sync**, **AI Subtitles/Dubbing**, and **Local Video Enhancement**.

---

## 📁 Project Layout

See [docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md) for the current directory layout and runtime artifact guide.

## ✨ Features

- **🤖 AI Integration**: Uses Gemini API to:
  - Translate titles and descriptions (English -> Chinese).
  - Generate standard (4:3) and widescreen (16:9) covers.
  - Recommend Bilibili categories and tags based on video content.
- 🌍 **Internationalization (I18n)**: Fully localized UI in **Chinese** (default) and **English**.
- 🎬 **AI Subtitles**: Automatically extracts audio, transcribes, and translates subtitles. Supports bilingual ASS files and auto-burning into video.
- 📝 **Auto Comment**: Automatically posts a sticky comment for fitness-related videos with customizable templates and retry logic.
- 🧹 **Smart Cleanup**:
  - Auto-cleans `.part` temporary files.
  - Option to delete source video after successful upload (`--delete-video`).
  - Archives metadata of processed videos to `history/` directory.
- **📂 Organized Storage**: Generated covers are saved alongside their source videos with clean suffix naming (`_cover_16x9.jpg`).
- **🔗 Deep Bilibili Integration**:
  - Authenticates via browser cookies (Chrome/Edge/etc.).
  - Supports scheduled publishing.
  - Supports scheduled publishing.
  - Supports auto-posting sticky comments.
- 🛡️ **Safety & Stability**: Built-in FFmpeg integrity check detects video corruption; supports **Auto-Repair** and resume to prevent redownloading large files.

## Multi-Platform Publishing

- Stable adapters: `Bilibili`, `Douyin`, `Kuaishou`
- Beta adapters: `WeChat Channels`, `Xiaohongshu`
- Unified fields: title, description, cover, tags, schedule intent
- Beta adapters use browser automation and may require manual re-login
- Config entry points:
  - `config/config.yaml -> publishing`
  - `config/publishing_automation.yaml` for `Douyin` / `Kuaishou` / `WeChat Channels` / `Xiaohongshu` locators
- Auth model:
  - `Bilibili`: browser cookies
  - `Douyin`: `automation` with logged-in browser profile, or `api` with `access_token`
  - `Kuaishou`: `automation` with logged-in browser profile, or `api` with `access_token` + `app_id`
  - `WeChat Channels` / `Xiaohongshu`: logged-in browser profile

### Platform Field Matrix

| Platform | Current publish fields | Extra platform-specific fields already wired |
|----------|------------------------|---------------------------------------------|
| `Bilibili` | `title`, `description`, `cover`, `tags`, schedule | `category/tid`, `copyright`, `mission_id`, `topic_id`, `activity_id`, `season`, `collection`, `suggested_collection` |
| `Douyin` | `title`, `description`, `cover`, `video_path`, schedule intent | `visibility`, `allow_comment`, plus any extra `platform_options.douyin.*` fields preserved for automation option controls |
| `Kuaishou` | `title`, `caption`, `cover`, `video_path`, schedule intent | `visibility`, `allow_comment`, plus any extra `platform_options.kuaishou.*` fields preserved for automation option controls |
| `WeChat Channels` | short `title`, `description`, `cover`, `video_path`, schedule intent | `declare_original`, `allow_comment`, `collection_name`, plus any extra `platform_options.wechat_channels.*` fields preserved for automation option controls |
| `Xiaohongshu` | `title`, `body`, `cover`, `video_path`, schedule intent | `visibility`, `allow_comment`, `poi_name`, plus any extra `platform_options.xiaohongshu.*` fields preserved for automation option controls |

### Multi-Platform Quick Setup

1. Copy the config files:

   ```bash
   cp config/config.example.yaml config/config.yaml
   cp config/publishing_automation.example.yaml config/publishing_automation.yaml
   ```

2. Enable your target platforms in `config/config.yaml`:

   ```yaml
   publishing:
     enabled: true
     default_targets:
       - bilibili
       - douyin
       - kuaishou
       - wechat_channels
       - xiaohongshu
     automation_config_file: "config/publishing_automation.yaml"
     adapters:
       bilibili:
         enabled: true
         mode: api
       douyin:
         enabled: true
         mode: automation
         automation: {}
         # Or switch to api mode if you have an official access token:
         # mode: api
         # access_token: "YOUR_DOUYIN_ACCESS_TOKEN"
       kuaishou:
         enabled: true
         mode: automation
         automation: {}
         # Or switch to api mode if you have official capability:
         # mode: api
         # access_token: "YOUR_KUAISHOU_ACCESS_TOKEN"
         # app_id: "YOUR_KUAISHOU_APP_ID"
       wechat_channels:
         enabled: true
         mode: automation
       xiaohongshu:
         enabled: true
         mode: automation
   ```

3. For `Douyin` / `Kuaishou` / `WeChat Channels` / `Xiaohongshu` browser automation:
   - point `browser_profile` to a dedicated logged-in profile
   - keep `browser_debug_port` stable and make sure that browser session stays open during automation
   - log into the creator dashboard with the browser profile pointed to by `browser_profile`
   - then update `config/publishing_automation.yaml` so `publish_url`, `locators`, and optional `option_controls` match the actual page DOM
   - `option_controls` now supports `fill / click / choice / boolean`, so platform-specific fields such as `visibility / allow_comment / declare_original / collection_name / poi_name` can be applied to real publish-form controls instead of only living in metadata

4. If a single video needs platform-specific options, add a `platform_options` block in `history/*.json` or `video_metadata.json`:

   ```json
   {
     "platform_options": {
       "bilibili": {
         "copyright": 2,
         "mission_id": 4058195,
         "suggested_collection": "居家暴汗健身跟练"
       },
       "douyin": {
         "visibility": "friends",
         "allow_comment": false
       },
       "wechat_channels": {
         "declare_original": true
       }
     }
   }
   ```

   The current runtime now preserves and forwards these platform-specific fields; `Bilibili` also auto-lifts common top-level metadata such as `category / mission_id / copyright / collection`. For automation platforms, once the matching field is wired in `config/publishing_automation.yaml` under `option_controls`, the value is also applied to the live creator form instead of being retained only in the payload.

5. Safe validation:

   ```bash
   python3 main.py "https://www.youtube.com/watch?v=VIDEO_ID" --dry-run -y
   ```

   This runs the full pre-processing flow and validates multi-platform runtime wiring during the publish stage without actually posting.

## 🔮 Roadmap

We are actively building the ultimate video localization suite:

- [x] **AI Subtitles**: Auto-generate, transcribe, and translate subtitles (Bilingual support).
- [ ] **AI Dubbing**: Remove original vocals and generate AI dubbing in target language.
- [ ] **Bi-directional Sync**: Support **Bilibili -> YouTube** reverse synchronization.
- [ ] **Local Workflow**:
  - Optimize metadata (titles/covers) for **local video files**.
  - Standalone image processing tools (smart crop, text removal).
- [ ] **More Platforms**: Support for TikTok, Instagram Reels, etc.

## 🛠 Prerequisites

- **Python 3.10+**
- **ffmpeg** (Must be in system PATH)
- **Deno 2.0+** (required for YouTube JS challenges handled by yt-dlp)
- **Browser**: Chrome/Edge/Firefox logged into **YouTube**, **Bilibili**, and **Gemini**.

## 🚀 Installation

1. **Clone the repository**:

    ```bash
    git clone <repository_url>
    cd biyou
    ```

2. **Install dependencies**:

    ```bash
    pip install -r requirements.txt
    ```

    This installs the pinned `yt-dlp[default]==2026.03.17`, including `yt-dlp-ejs`.

    If Homebrew Python blocks `pip install`, use:

    ```bash
    python3 -m pip install --break-system-packages --user -r requirements.txt
    ```

3. **Configuration**:
    Copy the example config and edit it:

    ```bash
    cp config/config.example.yaml config/config.yaml
    ```

## ⚙️ Configuration

Edit `config/config.yaml`:

```yaml
# Language: 'zh' (Chinese) or 'en' (English)
language: zh

# Default browser for cookies (chrome, edge, firefox)
browser: chrome

# Dedicated BiYou browser profile for cookies and automation
browser_profile: ~/.biyou-workflow-browser-data

# Remote debugging port used by browser automation
browser_debug_port: 39222

# Auto Mode: Skip manual confirmation (y/n)
auto_confirm: false

# Auto Comment Keywords: Only post comments for matching categories
auto_comment_keywords: ["Fitness", "Workout", "Gym", "健身", "运动"]
```

## 🖥 Usage

### Basic Usage

One-command full pipeline sync (includes auto-download, AI localization, and upload):

```bash
python3 main.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

**Manual Video Integrity Check:**

```bash
check-video /path/to/video.mp4
```

**Preload a Channel Backlog and Schedule One Item Per Day:**

Configure `config.yaml`:

```yaml
channel_daily_publish:
  channel_url: "https://www.youtube.com/@RosarioCooking/videos"
  publish_time: "19:05"
  timezone: "Asia/Shanghai"
  sync_window: 20
  state_file: "history/channel_daily_publish.json"
```

Sync the channel into local prepared backlog:

```bash
python3 scripts/channel_daily_publish.py sync
```

Schedule one item for the next available `19:05` slot:

```bash
python3 scripts/channel_daily_publish.py publish
```

Run both steps in sequence:

```bash
python3 scripts/channel_daily_publish.py run
```

### Command Line Arguments

| Argument | Description |
| :--- | :--- |
| `url` | YouTube URL (optional) |
| `-n`, `--dry-run` | Simulate only, no upload |
| `-y`, `--yes` | **Silent mode**: Auto-confirm all prompts (y) |
| `-s`, `--schedule` | Schedule time (e.g. `+4h`, `+1d`, `2024-01-01 12:00`) |
| `--delete-video` | Delete source file after upload |

## 📂 File Structure

```text
BiYou/
├── config/              # Configuration files
├── core/                # Core logic
│   ├── modules/         # Functional modules (download, upload, etc.)
│   ├── services/        # Service Layer (LLM, Subtitle processing)
│   ├── utils/           # Utilities (i18n, logger, config)
│   └── workflows/       # Task workflows
├── videos/              # Downloaded videos and raw metadata
├── history/             # Archived metadata for processed videos (Git ignored)
├── main.py              # Main entry point
├── requirements.txt     # Python dependencies
└── .gitignore           # Git ignore configuration
```

## 📝 Disclaimer

- **Content Compliance**: This tool is designed for creators to **manage and enhance their own original content** (inc. AI cover generation, title optimization). Please ensure you own the copyright or have permission for the videos you process.
- **Platform Rules**: Users are responsible for complying with Bilibili's Terms of Service. The author is not responsible for any account bans or content violations.

## ☕ Support & Sponsor

[![](https://img.shields.io/badge/Afdian-@runking-purple.svg)](https://afdian.com/a/runking)

If this tool saved you time, consider supporting me on **[Afdian](https://afdian.com/a/runking)** or **[GitHub Sponsors](https://github.com/sponsors/monkey-sking)**!

## ⚠️ Notes

- **Cookies**: BiYou now uses only the workflow-only isolated browser profile (`~/.biyou-workflow-browser-data`). Ensure that profile is logged into Bilibili, Gemini, and YouTube before running.
- **Quotas**: Gemini API has usage limits; if cover generation fails, check your quota or network.
- **Safety**: Use `--dry-run` first to verify metadata and covers are correct.
- **Agent Handoff**: `workflow_signals` can write structured run state to `logs/workflow_status/*.json` and `logs/workflow_events.jsonl`, plus trigger `agent_hook` on `run_failed` and `notify_hook` on non-recoverable failures.
- [x] Create `./check_env.sh` script to verify setup.
# Public source and validation scope

This repository contains a video-localization and publishing workflow. Source
availability does not establish a fully automated or universally supported
publishing service. Browser sessions, platform permissions and provider access
must be configured and validated separately.

Real configuration lives outside the checkout. Keep ignored compatibility links
in place and use only sanitized examples in Git. See `SECURITY.md` for the
configuration and history boundary. History cleanup does not revoke credentials.

The code in this repository is provided under the MIT license; third-party
tools and services retain their own terms and licenses.


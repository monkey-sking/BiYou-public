# BiYou Architecture Overview

## Core Design Philosophy
BiYou follows a **Service-Oriented** but pragmatic architecture.

- **Workflows (`src/workflows/`)**: Orchestrate high-level complex business logic. They tie together multiple services.
  - *Example*: `CoverWorkflow` orchestrates `ImageTranslationService` -> `WatermarkService`.
- **Services (`src/services/`)**: Atomic, reusable capabilities. Statless where possible.
  - *Example*: `ImageTranslationService` (wraps LLM interactions), `WatermarkService` (wraps image processing).
- **Modules (`src/modules/`)**: High-level functional blocks often wrapping external tools or complex sub-systems.
  - *Example*: `uploader` (wraps `biliup`), `downloader` (wraps `yt-dlp`).
- **Utils (`src/utils/`)**: Shared helpers (Logging, Config, I18n, Cookie).

## Key Data Flows

### 1. Main Sync Flow (`biyou.py`)
1. **Download**: `downloader.download_video`
2. **Fetch/Translate**: `fetcher.fetch_metadata` -> `Gemini`
3. **Cover Gen**: `CoverWorkflow.run`
   - -> `ImageTranslationService.process` (Gen Image)
   - -> `WatermarkService.remove_watermark` (Clean Image)
4. **Upload**: `uploader.run_upload`
   - -> `cookie.get_bilibili_credentials` (Auth)
   - -> `biliup` (API)
5. **Comment**: `commenter.post_comment`

## Service Factory Pattern
Services are often instantiated via **Factories** (e.g., `WatermarkServiceFactory`).
- **Goal**: Allow easy swapping of implementations without changing client code.
- **Example**: Currently `WatermarkService` uses `LocalRulesBased` (PIL cropping). In the future, we can switch to `AIInpainting` by just changing the factory or config.

## Cookie Handling Strategy
- **Source of Truth**: The Browser (Edge/Chrome/Firefox).
- **Mechanism**: `browser_cookie3` extracts cookies dynamically.
- **Priority**: Always fetch fresh from browser > cached file (fallback).
- **Validation**: Headers (User-Agent/Referer) are injected at the `biliup` session level to prevent `-101 Login Failed` errors.

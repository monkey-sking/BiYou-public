# BiYou Project Memory

## ⚡️ Quick Start

- **Sync Video**: `python3 main.py "URL"`
- **Dry Run**: `python3 main.py "URL" --dry-run`
- **Install**: `pip install -r requirements.txt`
- **Check Env**: `./check_env.sh`

## 🛠 Tech Stack

- **Language**: Python 3.10+
- **Core Libs**: `yt-dlp` (download), `ffmpeg` (processing), `biliup` (upload), `google-generativeai` (Gemini API), `playwright` (browser automation)
- **Cover Generation**: Gemini Thinking model via **Web API** (default, 图生图 via `gemini-webapi>=2.0.0`). Config `services.cover` in `config.yaml`:
  - `model: Thinking` — 高质量中文文字渲染
  - `use_browser: false` — Web API 优先；`true` 则浏览器自动化优先
  - `web_timeout_seconds: 180` — Thinking 模型图生图需要 60-180s（不要用 45s）
- **Config**: `config/config.yaml` (See `config/config.example.yaml` for reference)

## 📂 Architecture

- `core/modules/`: Core logic (Downloader, Uploader, Translator, etc.).
- `core/services/`: External API services (LLM, Subtitle processing).
- `main.py`: Main entry point for the sync pipeline.
- `videos/`: Default output directory for downloads and raw metadata.
- `history/`: Processed video metadata archives.
- `.agent/workflows/`: Standard workflow documentation.

## 📝 Coding Guidelines

- **I18n**: All user-facing strings must use `core/utils/i18n.py` for bilingual support (zh/en).
- **LLM**: Use `LLMFactory` for all AI calls to ensure model fallback and config consistency.
- **Error Handling**: Catch exceptions at the module level and log meaningful errors using the localized `print_t` or `log`.
- **Cookies**: Browser cookies are extracted automatically. Ensure the user is logged into the target browser (Chrome/Edge/Firefox).

## ⚠️ Important Rules

- **Do NOT** commit `config/config.yaml` or any JSON/TXT files containing credentials.
- Always check `requirements.txt` when adding new imports.
- Processed videos are archived in `history/`; do not delete this directory as it prevents duplicate uploads.

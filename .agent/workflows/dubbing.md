---
description: Run the AI dubbing pipeline on a local video file
---

Use this workflow to add AI-generated Chinese dubbing to a video.

**Prerequisites:**
- Video must be downloaded locally.
- `GPT-SoVITS` or similar TTS backend may be required (check project status).

```bash
python dubbing_cli.py "path/to/video.mp4"
```

**What this does:**
1. Extracts audio from the video.
2. Transcribes speech using Whisper/WhisperX.
3. Translates text to Chinese.
4. Generates AI voice using TTS.
5. Burns the new audio track into the video.

> [!NOTE]
> This feature is still under active development. Check `docs/PROJECT_STATUS.md` for the latest status.

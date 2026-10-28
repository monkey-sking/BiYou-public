---
description: Test the AI processing pipeline (Translation & Cover) without uploading
---

Use this workflow to verify AI outputs (translation, cover generation) without actually posting to Bilibili.

```bash
python automation/biyou.py "YOUR_VIDEO_URL_HERE" --dry-run
```

**What this does:**
1. Downloads the video.
2. Generates Chinese title/description using Gemini.
3. Generates the cover image.
4. **Stops** before the upload step.

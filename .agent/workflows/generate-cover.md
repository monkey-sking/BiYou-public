---
description: Generate AI-powered covers for a video (test cover generation only)
---

Use this workflow to generate cover images without downloading or uploading a video.

**Note:** This requires the video to already be downloaded locally.

```bash
python automation/biyou.py "YOUR_VIDEO_URL_HERE" --skip-fetch --skip-upload
```

**What this does:**
1. Skips metadata fetching (uses existing or cached data).
2. Generates Chinese-styled cover images using Gemini.
3. Saves covers to the video directory (e.g., `*_cover_16x9.jpg`).
4. Skips the upload step.

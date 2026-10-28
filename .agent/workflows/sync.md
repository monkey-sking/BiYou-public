---
description: Sync a YouTube video to Bilibili (Download -> AI Process -> Upload)
---

To sync a YouTube video to Bilibili, run the following command:

```bash
python3 main.py "YOUR_VIDEO_URL_HERE"
```

**Common Options:**
- `--delete-video`: Delete the downloaded file after successful upload.
- `--dry-run`: Test the process without actual uploading.

**Example:**
"Sync this video and delete local file after:"
```bash
python3 main.py "https://www.youtube.com/watch?v=VIDEO_ID" --delete-video
```

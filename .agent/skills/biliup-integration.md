# Biliup Integration Skill

This document describes how the BiYou project integrates with the `biliup` library for Bilibili uploads.

## Key Classes

**File:** `automation/src/modules/uploader.py`

### ExtendedData

We extend `biliup.plugins.bili_webup.Data` to add custom fields:

```python
from biliup.plugins.bili_webup import BiliBili, Data
from dataclasses import dataclass

@dataclass
class ExtendedData(Data):
    cover43: str = ''   # 4:3 cover URL (for feed/vertical display)
    dtime: int = 0      # Scheduled publish timestamp (10-digit Unix)
```

---

## Upload Flow

1. **Get Credentials**: `get_bilibili_credentials()` extracts cookies from browser or file.
2. **Prepare Data**: Create `ExtendedData` with title, description, tags, tid.
3. **Upload Covers**: Use raw POST to `/x/vu/web/cover/up` to avoid biliup's auto-cropping.
4. **Upload Video**: `bili.upload_file(video_path)` chunks and uploads.
5. **Submit**: `bili.submit()` finalizes the video.

---

## Common Issues

### Cover Cropping

- **Problem**: biliup's default `bili.cover_up()` auto-crops to 16:10.
- **Solution**: Use `upload_cover_raw()` helper (base64 POST directly).

### Category (TID) Matching

- **Problem**: Gemini may return partial category names like "健身".
- **Solution**: Fuzzy match against `config/category_map.yaml`.

### Scheduled Publishing

- **Rule**: `dtime` must be at least 4 hours in the future.
- **Format**: 10-digit Unix timestamp.

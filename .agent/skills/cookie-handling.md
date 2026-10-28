# Cookie Handling Skill

This document explains how BiYou handles browser cookies for YouTube and Bilibili authentication.

## Library

We use `browser-cookie3` to extract cookies from local browser profiles.

**Install:** `pip install browser-cookie3`

---

## Cookie Extraction

**Files:**
- `automation/src/utils/cookie.py`
- `automation/src/modules/uploader.py` (for Bilibili)
- `automation/src/modules/downloader.py` (for YouTube via yt-dlp)

### Bilibili Cookies

```python
from src.utils.cookie import get_bilibili_cookies

cookies = get_bilibili_cookies('chrome')  # Returns list of cookie dicts
```

**Required Cookies:**
- `SESSDATA` - Session token
- `bili_jct` - CSRF token
- `DedeUserID` - User ID

### YouTube Cookies

yt-dlp handles this internally when you pass `--cookies-from-browser chrome`.

---

## Troubleshooting

### "未登录" / Not Logged In

1. Ensure browser is **fully closed** before running (some browsers lock cookie DB).
2. Verify you are logged in on the **default profile** (not incognito).
3. Try a different browser: `config.yaml` → `browser: edge`.

### macOS Keychain Prompt

If macOS asks for Keychain access, click "Always Allow" for Python.

### Chrome Version Issues

`browser-cookie3` may break on Chrome updates. Check for library updates:
```bash
pip install --upgrade browser-cookie3
```

from typing import List, Tuple, Union

import re
from urllib.parse import parse_qs, urlparse


YOUTUBE_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def parse_youtube_video_id(url: str) -> str:
    """Extract a YouTube video id from supported watch/share/embed URL forms."""
    if not isinstance(url, str):
        return ""

    candidate = url.strip()
    if not candidate:
        return ""

    try:
        parsed = urlparse(candidate)
        host = (parsed.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]

        path = parsed.path or ""
        query = parse_qs(parsed.query or "")

        video_id = None
        if host == "youtu.be":
            video_id = path.strip("/").split("/")[0] or None
        elif host.endswith("youtube.com"):
            if path == "/watch":
                video_id = (query.get("v") or [None])[0]
            elif path.startswith(("/shorts/", "/embed/", "/live/")):
                parts = [p for p in path.split("/") if p]
                if len(parts) >= 2:
                    video_id = parts[1]

        if video_id and YOUTUBE_VIDEO_ID_RE.fullmatch(video_id):
            return video_id
    except Exception:
        return ""

    return ""


def is_supported_video_url(url: str) -> bool:
    """Return True when the URL points to a supported video page."""
    return bool(parse_youtube_video_id(url))


def split_supported_video_urls(urls: Union[List[str], Tuple[str, ...], None]) -> Tuple[List[str], List[str]]:
    """Split input URLs into supported video URLs and invalid/non-video URLs."""
    valid_urls: List[str] = []
    invalid_urls: List[str] = []

    if urls is None:
        return [], []

    for raw in urls:
        if not isinstance(raw, str):
            invalid_urls.append(raw)
            continue

        url = raw.strip()
        if not url:
            continue

        if is_supported_video_url(url):
            valid_urls.append(url)
        else:
            invalid_urls.append(url)

    return valid_urls, invalid_urls

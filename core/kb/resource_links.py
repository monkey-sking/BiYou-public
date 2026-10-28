import json
import os
import re
from urllib.parse import urlparse

import requests

from core.utils.logger import log


URL_RE = re.compile(r"https?://[^\s<>()\"']+", re.IGNORECASE)
VIDEO_REQUIRED_DOMAINS = (
    "assetstore.unity.com",
    "fab.com",
    "unrealengine.com/marketplace",
    "github.com",
    "gitlab.com",
    "bitbucket.org",
    "drive.google.com",
    "dropbox.com",
    "discord.gg",
    "discord.com",
    "patreon.com",
)
PROMOTIONAL_HINTS = (
    "sponsor",
    "sponsored",
    "ad",
    "affiliate",
    "merch",
    "shop",
    "newsletter",
    "instagram.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "tiktok.com",
)


def normalize_url(raw: str) -> str:
    return (raw or "").strip().rstrip(".,，。!！?？)]}、")


def classify_link(url: str, context: str = "") -> str:
    haystack = f"{url} {context}".lower()
    if any(domain in haystack for domain in VIDEO_REQUIRED_DOMAINS):
        return "video_required"
    if any(hint in haystack for hint in PROMOTIONAL_HINTS):
        return "promotional"
    return "video_required"


def extract_links_fallback(description: str) -> list[dict]:
    links = []
    seen = set()
    lines = (description or "").splitlines()
    for line in lines:
        for raw in URL_RE.findall(line):
            url = normalize_url(raw)
            if not url or url in seen:
                continue
            seen.add(url)
            host = urlparse(url).netloc or url
            links.append(
                {
                    "url": url,
                    "label": host,
                    "classification": classify_link(url, line),
                    "reason": "fallback_url_classifier",
                }
            )
    return links


def _load_hf_key(settings: dict) -> str:
    key = settings.get("api_key") or os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_API_KEY")
    if key:
        return key.strip()
    key_file = settings.get("api_key_file") or "/tmp/.hf_key_2"
    try:
        with open(os.path.expanduser(key_file), "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _parse_jsonish(text: str) -> list[dict]:
    if not text:
        return []
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", stripped, re.DOTALL)
        if not match:
            return []
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            return []
    if isinstance(payload, dict):
        payload = payload.get("links", [])
    if not isinstance(payload, list):
        return []
    return sanitize_links(payload)


def sanitize_links(raw_links) -> list[dict]:
    clean = []
    seen = set()
    for item in raw_links or []:
        if isinstance(item, str):
            item = {"url": item}
        if not isinstance(item, dict):
            continue
        url = normalize_url(str(item.get("url", "")))
        if not url or not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        classification = str(item.get("classification") or classify_link(url)).strip()
        if classification not in {"video_required", "promotional"}:
            classification = classify_link(url, str(item))
        clean.append(
            {
                "url": url,
                "label": str(item.get("label") or urlparse(url).netloc or url).strip(),
                "classification": classification,
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    return clean


def _extract_with_hf_share(title: str, description: str, settings: dict) -> list[dict]:
    key = _load_hf_key(settings)
    if not key:
        return []

    endpoint = settings.get("endpoint") or "https://router.huggingface.co/v1/chat/completions"
    model = settings.get("model") or "deepseek-v4"
    from core.services.llm.gemini_service import build_resource_link_extraction_prompt

    prompt = build_resource_link_extraction_prompt(title, description)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 1200,
    }
    resp = requests.post(endpoint, headers=headers, json=payload, timeout=int(settings.get("timeout", 25)))
    resp.raise_for_status()
    data = resp.json()
    content = ""
    if isinstance(data, dict):
        choices = data.get("choices") or []
        if choices:
            content = ((choices[0].get("message") or {}).get("content") or choices[0].get("text") or "")
        content = content or data.get("generated_text") or data.get("content") or ""
    elif isinstance(data, list) and data:
        content = data[0].get("generated_text", "") if isinstance(data[0], dict) else str(data[0])
    return _parse_jsonish(content)


def extract_resource_links(title: str, description: str, config: dict | None = None) -> list[dict]:
    config = config or {}
    settings = config.get("resource_link_extraction") or {}
    fallback = extract_links_fallback(description)
    if settings.get("enabled", True) is False:
        return fallback
    if not fallback:
        return []
    try:
        hf_links = _extract_with_hf_share(title, description, settings)
        if hf_links:
            return hf_links
    except Exception as exc:
        log.warning(f"⚠️ Resource link extraction via HF failed, using fallback: {exc}")
    return fallback


def attach_resource_links(metadata: dict, config: dict | None = None) -> dict:
    if not isinstance(metadata, dict):
        return metadata
    title = metadata.get("original_title") or metadata.get("title") or ""
    description = metadata.get("original_description") or metadata.get("description") or ""
    links = extract_resource_links(title, description, config=config)
    if links:
        metadata["resource_links"] = links
    else:
        metadata.pop("resource_links", None)
    return metadata


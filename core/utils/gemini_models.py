import re
from typing import Optional


try:
    from gemini_webapi.constants import Model as WebModel
except ImportError:
    WebModel = None


_MODE_ID_PATTERN = re.compile(r'"([0-9a-f]{16})"')

_FALLBACK_SPECS = {
    "Flash": {
        "webapi_name": "gemini-3-flash",
        "browser_mode_id": "fbb127bbb056c959",
        "browser_labels": ("快速", "Flash"),
    },
    "Thinking": {
        "webapi_name": "gemini-3-flash-thinking",
        "browser_mode_id": "5bf011840784117a",
        "browser_labels": ("思考", "Thinking"),
    },
    "Pro": {
        "webapi_name": "gemini-3-pro",
        "browser_mode_id": "9d8ca3786ebdfbea",
        "browser_labels": ("Pro", "Advanced", "高级"),
    },
}

_ENUM_CANDIDATES = {
    "Flash": ("G_3_0_FLASH", "ADVANCED_FLASH", "PLUS_FLASH", "BASIC_FLASH"),
    "Thinking": ("G_3_0_FLASH_THINKING", "ADVANCED_THINKING", "PLUS_THINKING", "BASIC_THINKING"),
    "Pro": ("G_3_1_PRO", "ADVANCED_PRO", "PLUS_PRO", "BASIC_PRO"),
}


def _extract_mode_id(enum_member) -> Optional[str]:
    try:
        _, headers, _ = enum_member.value
        marker = headers.get("x-goog-ext-525001261-jspb", "")
        match = _MODE_ID_PATTERN.search(marker)
        return match.group(1) if match else None
    except Exception:
        return None


def _get_web_enum(*names):
    if not WebModel:
        return None
    for name in names:
        if hasattr(WebModel, name):
            return getattr(WebModel, name)
    return None


def get_gemini_web_model_specs() -> dict[str, dict]:
    specs = {}
    for key, fallback in _FALLBACK_SPECS.items():
        enum_member = _get_web_enum(*_ENUM_CANDIDATES.get(key, ()))
        webapi_name = fallback["webapi_name"]
        browser_mode_id = fallback["browser_mode_id"]
        if enum_member:
            try:
                webapi_name = enum_member.value[0] or webapi_name
            except Exception:
                pass
            browser_mode_id = _extract_mode_id(enum_member) or browser_mode_id
        specs[key] = {
            "key": key,
            "enum": enum_member,
            "webapi_name": webapi_name,
            "browser_mode_id": browser_mode_id,
            "browser_labels": fallback["browser_labels"],
        }
    return specs


def normalize_gemini_model_key(model_key, default: str = "Thinking") -> str:
    if model_key is None:
        return default

    if hasattr(model_key, "name"):
        enum_name = getattr(model_key, "name", "")
        if any(n in enum_name for n in ("ADVANCED_PRO", "PLUS_PRO", "BASIC_PRO", "G_3_1_PRO", "G_3_0_PRO")):
            return "Pro"
        if any(n in enum_name for n in ("ADVANCED_THINKING", "PLUS_THINKING", "BASIC_THINKING", "G_3_0_FLASH_THINKING")):
            return "Thinking"
        if any(n in enum_name for n in ("ADVANCED_FLASH", "PLUS_FLASH", "BASIC_FLASH", "G_3_0_FLASH")):
            return "Flash"

    text = str(model_key).strip()
    if not text:
        return default

    if text in ("Flash", "Thinking", "Pro"):
        return text

    lowered = text.casefold()
    if lowered in ("auto", "unspecified"):
        return default

    if (
        "flash-thinking" in lowered
        or "thinking" in lowered
        or "思考" in text
    ):
        return "Thinking"

    if (
        "3.1 pro" in lowered
        or "2.5-pro" in lowered
        or "3.0-pro" in lowered
        or re.search(r"\bpro\b", lowered)
        or "高级" in text
        or "advanced" in lowered
    ):
        return "Pro"

    if (
        "2.5-flash" in lowered
        or "3.0-flash" in lowered
        or re.search(r"\bflash\b", lowered)
        # 2026-09-16: Gemini 网页改成 "Flash扩展" 这种中英混排，
        # \b 在中文前不成立 → "Flash扩展" 匹配不到 Flash，导致误判需切模型。
        or re.search(r"flash(?![a-z])", lowered)
        or "快速" in text
        or "极速" in text
    ):
        return "Flash"

    return default


def get_gemini_web_model_spec(model_key, default: str = "Thinking") -> dict:
    key = normalize_gemini_model_key(model_key, default=default)
    return get_gemini_web_model_specs()[key]

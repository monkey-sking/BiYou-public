"""Browser-based Gemini image generation tool using Playwright.
This module acts as a drop-in replacement/bypass for the Gemini Web API
when API-based image generation fails. Browser automation prefers the
configured browser app with the BY-specific profile over CDP, while
standalone fallback still uses the same browser executable with injected
BY profile cookies.
"""
import atexit
import asyncio
import os
import sys
import time
import subprocess
import json
import re
from calendar import monthrange
from pathlib import Path
from typing import Optional
from datetime import datetime

# Add project root to path
# (Moved up to ensure imports work if run as script)
project_root = str(Path(__file__).parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from playwright.async_api import async_playwright, Page
from core.utils.browser_config import (
    get_browser_debug_port,
    prepare_browser_launch,
    resolve_browser_profile_dir,
    terminate_browser_process,
)
from core.utils.gemini_models import (
    get_gemini_web_model_spec,
    normalize_gemini_model_key,
)
from core.utils.logger import log
from core.utils.cookie import get_cookies_from_browser, resolve_browser_app_info
from core.utils.gemini_compat import generate_content_compat

# --- Verification Service Init ---
try:
    from gemini_webapi.constants import Model as WebModel
    _HAS_WEBAPI = True
except ImportError:
    _HAS_WEBAPI = False
    
# Configuration
try:
    from core.utils.config import load_config, update_config
    _config = load_config()
    USER_DATA_DIR = resolve_browser_profile_dir(config=_config)
    DEBUG_PORT = get_browser_debug_port(config=_config)
    BROWSER_KEY, BROWSER_EXECUTABLE, _ = resolve_browser_app_info(
        browser=_config.get('browser'),
        user_data_dir=USER_DATA_DIR,
    )
    
    # 多账号索引 (authuser=0, 1, 2...)
    _account_index = _config.get('current_account_index', 0)
    _accounts_count = _config.get('gemini_accounts_count', 1)
except Exception:
    USER_DATA_DIR = os.path.expanduser("~/Library/Application Support/Citro Labs/ego lite")
    DEBUG_PORT = 39222
    BROWSER_KEY = 'ego'   # 2026-08-30: 原来写死 'chrome'，会把 ego profile 目录当 Chrome 读 → keychain 卡死
    BROWSER_EXECUTABLE = "/Applications/ego lite.app/Contents/MacOS/ego lite"
    _account_index = 0
    _accounts_count = 1

_account_pool = {}
_last_probe_ts = 0.0
_ACCOUNT_PROBE_TTL = 120.0
GEMINI_URL = "https://gemini.google.com/app"
GEN_TIMEOUT_MS = 120000  # 120s timeout for image generation
_OWNS_DEBUG_BROWSER = False
_PROMPT_INPUT_SELECTORS = (
    'div[contenteditable="true"]',
    'rich-textarea > div > p',
    'textarea[aria-label*="prompt"]',
    'textarea[placeholder*="Ask Gemini"]',
    '.ql-editor',
    '[role="textbox"]',
)
_SEND_BUTTON_SELECTORS = (
    'button[aria-label*="Submit"]',
    '[role="button"][aria-label*="Submit"]',
    'button:has-text("Submit")',
    'button:has-text("提交")',
    'button[type="submit"]',
    'button[aria-label*="Send"]',
    'button[aria-label*="发送"]',
    '[role="button"][aria-label*="Send"]',
    '[role="button"][aria-label*="发送"]',
    'button:has(mat-icon[data-mat-icon-name="send"])',
    '.send-button',
)


def _should_use_screenshot_cover_fallback() -> bool:
    try:
        services = (_config or {}).get("services", {})
        cover_config = services.get("cover", {}) if isinstance(services, dict) else {}
        return bool(cover_config.get("allow_screenshot_fallback", False))
    except Exception:
        return False


_UPLOAD_MENU_TRIGGER_SELECTORS = (
    'button[aria-label*="打开文件上传菜单"]',
    'button[aria-label*="关闭文件上传菜单"]',
    'button[aria-label*="文件上传菜单"]',
    'button[aria-label="Add files"]',
    'button[aria-label*="Add files"]',
    'button[aria-label*="上传"]',
    'button.upload-card-button',
)
_VISIBLE_UPLOAD_MENU_SELECTORS = (
    '[data-test-id="local-images-files-uploader-button"]',
    '[role="menuitem"]:has-text("Files")',
    'button:has-text("Files")',
    '[role="button"]:has-text("Files")',
    '[role="menuitem"]:has-text("Upload files")',
    'button:has-text("Upload files")',
    '[role="menuitem"]:has-text("文件")',
    'button:has-text("文件")',
    '[role="menuitem"][aria-label*="上传文件"]',
    '[role="menuitem"][aria-label*="Upload file"]',
    '[role="menuitem"]:has-text("上传文件")',
    '[role="menuitem"]:has-text("Upload file")',
    '[role="menuitem"]:has-text("从计算机上传")',
    '[role="menuitem"]:has-text("Upload from computer")',
    '[role="menuitem"]:has-text("From computer")',
)
_IMAGE_MODE_SELECTORS = (
    'button:has-text("Create image")',
    '[role="button"]:has-text("Create image")',
    '[role="menuitem"]:has-text("Create image")',
    'button:has-text("Generate image")',
    '[role="button"]:has-text("Generate image")',
    '[role="menuitem"]:has-text("Generate image")',
    'button:has-text("Edit image")',
    '[role="button"]:has-text("Edit image")',
    '[role="menuitem"]:has-text("Edit image")',
    'button:has-text("创建图片")',
    '[role="button"]:has-text("创建图片")',
    '[role="menuitem"]:has-text("创建图片")',
    'button:has-text("生成图片")',
    '[role="button"]:has-text("生成图片")',
    '[role="menuitem"]:has-text("生成图片")',
    'button:has-text("编辑图片")',
    '[role="button"]:has-text("编辑图片")',
    '[role="menuitem"]:has-text("编辑图片")',
)
_ATTACHMENT_PROGRESS_TOKENS = (
    "uploading",
    "upload in progress",
    "uploading file",
    "uploading image",
    "preparing",
    "processing file",
    "processing image",
    "上传中",
    "正在上传",
    "处理中",
    "准备中",
)
_HARD_TEXT_ONLY_MARKERS = (
    "i can't directly create images",
    "i can't directly edit images",
    "i cannot directly create images",
    "i cannot directly edit images",
    "i'm unable to create images",
    "i'm unable to edit images",
    "i am unable to create images",
    "i am unable to edit images",
    "i can help write a prompt",
    "here is a prompt you can use",
    "here's a prompt you can use",
    "i can describe the changes",
    "我不能直接生成图片",
    "我不能直接编辑图片",
    "我无法直接生成图片",
    "我无法直接编辑图片",
    "我可以帮你写提示词",
    "以下是提示词",
    "下面是提示词",
    "你可以使用以下提示词",
)
_HARD_GENERATION_FAILURE_MARKERS = (
    "image editing request failed",
    "image generation request failed",
    "image editing request declined",
    "image generation request declined",
    "request failed",
    "request declined",
    "editing failed",
    "editing declined",
    "generation failed",
    "generation declined",
    "无法编辑这张图片",
    "图片编辑请求失败",
    "图像编辑请求失败",
    "图片编辑请求被拒绝",
    "图像编辑请求被拒绝",
    "图片生成请求失败",
    "图片生成请求被拒绝",
    "编辑请求失败",
    "编辑请求被拒绝",
    "生成失败",
    "生成被拒绝",
)
_SOFT_GENERATION_MARKERS = (
    "generating",
    "creating image",
    "creating your image",
    "editing image",
    "integrating the simplified text",
    "evaluating text integration",
    "working on it",
    "hang tight",
    "one moment",
    "please wait",
    "processing",
    "正在生成",
    "正在制作",
    "正在处理",
    "请稍候",
    "稍等",
    "我来处理",
    "为你生成",
    "为你编辑",
)


# ── Smart wait helpers (replace blind wait_for_timeout) ──

_SNLMOE_PATTERN = re.compile(r'"SNlM0e":"([^"]+)"')


async def _wait_for_page_ready(
    page,
    timeout: float = 8.0,
    *,
    min_since_nav: float = 0.5,
) -> bool:
    """Wait for Gemini page to be interactive — SNlM0e token or key DOM signal."""
    deadline = time.time() + timeout
    last_nav = time.time()
    while time.time() < deadline:
        url = page.url or ""
        if "accounts.google.com" in url or "ServiceLogin" in url:
            return True
        if "gemini.google.com" not in url:
            await asyncio.sleep(0.2)
            continue
        try:
            html = await page.evaluate("document.documentElement.outerHTML || ''")
            if _SNLMOE_PATTERN.search(html):
                elapsed = time.time() - last_nav
                if elapsed < min_since_nav:
                    await asyncio.sleep(min_since_nav - elapsed)
                return True
        except Exception:
            pass
        try:
            if await page.locator("button.input-area-switch").first.is_visible(timeout=0.5):
                return True
        except Exception:
            pass
        await asyncio.sleep(0.25)
    return False


async def _wait_for_model_ready(page, target_key: str, timeout: float = 6.0) -> bool:
    """Wait for model selector button to match target after switch."""
    target = get_gemini_web_model_spec(target_key)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            btn = page.locator("button.input-area-switch").first
            if await btn.count() == 0:
                await asyncio.sleep(0.3)
                continue
            text = (await btn.text_content() or "").strip()
            mode_id = _extract_selected_mode_id(await btn.get_attribute("jslog"))
            if _model_matches_target(text, mode_id, target):
                return True
        except Exception:
            pass
        await asyncio.sleep(0.2)
    return False


async def _wait_for_send_ready(page, timeout: float = 5.0) -> bool:
    """Wait for a visible send button."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            btn = await _find_last_visible(page, _SEND_BUTTON_SELECTORS)
            if btn and await btn.is_visible():
                return True
        except Exception:
            pass
        await asyncio.sleep(0.2)
    return False


def _refresh_account_config():
    global _account_index, _accounts_count, USER_DATA_DIR, DEBUG_PORT, BROWSER_KEY, BROWSER_EXECUTABLE
    try:
        cfg = load_config()
        USER_DATA_DIR = resolve_browser_profile_dir(config=cfg)
        DEBUG_PORT = get_browser_debug_port(config=cfg)
        BROWSER_KEY, BROWSER_EXECUTABLE, _ = resolve_browser_app_info(
            browser=cfg.get('browser'),
            user_data_dir=USER_DATA_DIR,
        )
        _account_index = int(cfg.get('current_account_index', _account_index))
        _accounts_count = max(1, int(cfg.get('gemini_accounts_count', _accounts_count)))
    except Exception:
        pass


def _format_dt(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    return dt.isoformat(timespec="minutes")


def _parse_limit_reset_time(page_text: str) -> Optional[datetime]:
    if not page_text:
        return None

    now = datetime.now()
    text = " ".join(page_text.split())
    patterns = [
        r'重置时间[:：]?\s*(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})',
        r'resets?\s+at\s+(\d{1,2})[:：](\d{2})\s*(AM|PM)',
        r'available\s+again\s+at\s+(\d{1,2})[:：](\d{2})\s*(AM|PM)',
        r'今天\s*(\d{1,2})[:：](\d{2})',
        r'(\d{1,2})[:：](\d{2})\s*后可用',
    ]

    for idx, pattern in enumerate(patterns):
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        try:
            if idx == 0:
                month, day, hour, minute = map(int, match.groups())
                year = now.year
                if month < now.month - 1:
                    year += 1
                day = min(day, monthrange(year, month)[1])
                return now.replace(year=year, month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
            if idx in (1, 2):
                hour, minute = int(match.group(1)), int(match.group(2))
                ampm = match.group(3).upper()
                if ampm == 'PM' and hour != 12:
                    hour += 12
                if ampm == 'AM' and hour == 12:
                    hour = 0
                candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if candidate <= now:
                    candidate = candidate.replace(day=min(now.day + 1, monthrange(now.year, now.month)[1]))
                return candidate
            hour, minute = int(match.group(1)), int(match.group(2))
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate <= now:
                candidate = candidate.replace(day=min(now.day + 1, monthrange(now.year, now.month)[1]))
            return candidate
        except Exception:
            continue
    return None


def _extract_limit_status(page_text: str) -> tuple[bool, Optional[datetime]]:
    if not page_text:
        return False, None
    text_lower = page_text.lower()
    limit_keywords = [
        "达到上限", "limit reached", "can't create more images",
        "每日限额", "用量限额", "can't generate more images",
        "come back tomorrow", "exceeded your quota", "重置时间",
        "can create more images", "limit resets", "check your usage",
        "as soon as your limit", "usage in settings"
    ]
    is_limited = any(keyword.lower() in text_lower for keyword in limit_keywords)
    reset_at = _parse_limit_reset_time(page_text) if is_limited else None
    return is_limited, reset_at


def _mark_account_state(authuser: int, **updates):
    state = _account_pool.get(authuser, {
        "authuser": authuser,
        "valid": False,
        "status": "unknown",
        "cooldown_until": None,
        "reason": None,
        "last_checked": None,
    })
    state.update(updates)
    state["last_checked"] = datetime.now().isoformat(timespec="seconds")
    _account_pool[authuser] = state
    return state


def _account_is_available(state: dict) -> bool:
    if not state or not state.get("valid"):
        return False
    if state.get("status") == "invalid_login":
        return False
    cooldown_until = state.get("cooldown_until")
    if cooldown_until:
        try:
            if datetime.fromisoformat(cooldown_until) > datetime.now():
                return False
        except Exception:
            pass
    return True


def _get_next_available_account() -> Optional[int]:
    _refresh_account_config()
    if not _account_pool:
        return None
    total = max(_accounts_count, len(_account_pool))
    for offset in range(total):
        candidate = (_account_index + offset) % total
        state = _account_pool.get(candidate)
        if _account_is_available(state):
            return candidate
    return None

def _kill_browser_process(force: bool = False):
    """Kill the detached debug browser only when explicitly requested."""
    global _OWNS_DEBUG_BROWSER
    if not force:
        return
    if not _OWNS_DEBUG_BROWSER:
        print("   ℹ️ Skipping browser termination because the current task does not own the shared workflow browser.")
        return
    try:
        import subprocess
        # Matches the specific debugging port to avoid killing user's main Chrome
        # Use '--' to stop option parsing for pkill, allowing patterns starting with '-'
        subprocess.run(['pkill', '-f', '--', f'--remote-debugging-port={DEBUG_PORT}'], 
                       capture_output=True, timeout=5)
        _OWNS_DEBUG_BROWSER = False
        print("   🔌 Browser process terminated (atexit).")
    except:
        pass

# Register cleanup on script exit
atexit.register(_kill_browser_process)

def close_browser():
    """Public API to close only the task-owned debug browser after batch completion."""
    _kill_browser_process(force=True)

# --- Chat ID 日志 ---
CHAT_LOG_FILE = os.path.join(project_root, "logs", "gemini_chats.jsonl")

def _log_browser_chat_id(page_url: str):
    """从浏览器 URL 提取对话 ID 并记录"""
    try:
        match = re.search(r'gemini\.google\.com/app/([a-f0-9]+)', page_url)
        if match:
            cid = match.group(1)
            os.makedirs(os.path.dirname(CHAT_LOG_FILE), exist_ok=True)
            entry = {"cid": cid, "time": datetime.now().isoformat(), "date": datetime.now().strftime("%Y-%m-%d"), "source": "browser", "authuser": _account_index}
            with open(CHAT_LOG_FILE, "a") as f:
                f.write(json.dumps(entry) + "\n")
    except Exception:
        pass

# --- 多账号轮换 ---
def rotate_gemini_account():
    """切换到同一 Profile 下的下一个 Google 账号"""
    global _account_index
    try:
        _refresh_account_config()
        count = _accounts_count
        if count <= 1 and not _account_pool:
            print("⚠️ 只有 1 个账号，无法轮换 (gemini_accounts_count <= 1)")
            return False

        start = _account_index
        selected = None
        search_count = max(count, len(_account_pool) or 0, 1)
        for offset in range(1, search_count + 1):
            candidate = (start + offset) % search_count
            state = _account_pool.get(candidate)
            if state is None or _account_is_available(state):
                selected = candidate
                break

        if selected is None:
            print("⚠️ 没有其他可用账号可切换")
            return False

        _account_index = selected
        print(f"🔄 轮换到下一个账号: authuser={_account_index}")
        
        # Directly save to yaml since save_config is not available
        import yaml
        cfg_file = os.path.join(project_root, "config", "config.yaml")
        if os.path.exists(cfg_file):
            with open(cfg_file, 'r', encoding='utf-8') as f:
                raw_cfg = yaml.safe_load(f) or {}
            raw_cfg['current_account_index'] = _account_index
            with open(cfg_file, 'w', encoding='utf-8') as f:
                yaml.dump(raw_cfg, f, allow_unicode=True, default_flow_style=False)
                
        return True
    except Exception as e:
        print(f"❌ 账号轮换失败: {e}")
        return False

def _get_gemini_url():
    """构建带 authuser 参数的 Gemini URL"""
    return f"https://gemini.google.com/app?authuser={_account_index}"


def _get_gemini_url_for(authuser: int):
    return f"https://gemini.google.com/app?authuser={authuser}"


def _extract_authuser(page_url: str) -> Optional[int]:
    if not page_url:
        return None
    match = re.search(r'[?&]authuser=(\d+)', page_url)
    if match:
        try:
            return int(match.group(1))
        except Exception:
            return None
    return None


def _normalize_browser_text(text: Optional[str]) -> str:
    return " ".join((text or "").strip().lower().split())


def _looks_like_hard_text_only_response(response_text: str) -> bool:
    normalized = _normalize_browser_text(response_text)
    if not normalized:
        return False
    return any(marker in normalized for marker in _HARD_TEXT_ONLY_MARKERS)


def _looks_like_generation_in_progress(response_text: str, body_text: str = "") -> bool:
    normalized = _normalize_browser_text(f"{response_text} {body_text}")
    if not normalized:
        return False
    return any(marker in normalized for marker in _SOFT_GENERATION_MARKERS)


def _looks_like_generation_failure(response_text: str, body_text: str = "") -> bool:
    normalized = _normalize_browser_text(f"{response_text} {body_text}")
    if not normalized:
        return False
    if any(marker in normalized for marker in _SOFT_GENERATION_MARKERS):
        return False
    return any(marker in normalized for marker in _HARD_GENERATION_FAILURE_MARKERS)


async def _read_page_text(page: Page) -> str:
    try:
        return await page.evaluate("document.body ? document.body.innerText : ''")
    except Exception:
        return ""


async def _find_last_visible(page: Page, selectors: tuple[str, ...]):
    for selector in selectors:
        locator = page.locator(selector)
        try:
            count = await locator.count()
        except Exception:
            continue
        for idx in range(count - 1, -1, -1):
            candidate = locator.nth(idx)
            try:
                if await candidate.is_visible():
                    return candidate
            except Exception:
                continue
    return None


async def _wait_for_visible(page: Page, selectors: tuple[str, ...], timeout_seconds: float = 8.0):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        candidate = await _find_last_visible(page, selectors)
        if candidate:
            return candidate
        await page.wait_for_timeout(250)
    return None


async def _read_prompt_input_text(input_box) -> str:
    try:
        return await input_box.evaluate(
            """(el) => {
                const value = typeof el.value === 'string' ? el.value : '';
                const text = typeof el.innerText === 'string' ? el.innerText : (el.textContent || '');
                return (value || text || '').trim();
            }"""
        )
    except Exception:
        return ""


async def _ensure_image_generation_mode(page: Page) -> bool:
    """Best-effort switch into Gemini's image-focused workflow when the UI exposes it."""
    candidate = await _wait_for_visible(page, _IMAGE_MODE_SELECTORS, timeout_seconds=2.0)
    if not candidate:
        print("   ℹ️ Image mode entrypoint not found. Continuing with the default composer.")
        return False

    try:
        already_active = await candidate.evaluate(
            """(el) => {
                const pressed = (el.getAttribute('aria-pressed') || '').toLowerCase() === 'true';
                const selected = (el.getAttribute('aria-selected') || '').toLowerCase() === 'true';
                return pressed || selected || el.matches?.('[aria-current="true"], .selected, .active');
            }"""
        )
        if already_active:
            print("   ✅ Gemini image mode already active.")
            return True
    except Exception:
        pass

    label = ""
    try:
        label = ((await candidate.inner_text()) or (await candidate.get_attribute("aria-label")) or "").strip()
    except Exception:
        pass

    try:
        await _scroll_and_click(page, candidate)
        await page.wait_for_timeout(400)
        print(f"   ✅ Entered Gemini image mode via '{label or 'image action'}'.")
        return True
    except Exception as e:
        print(f"   ⚠️ Failed to enter Gemini image mode: {e}")
        return False


def _prompt_text_matches(current_text: str, expected_text: str) -> bool:
    current = _normalize_browser_text(current_text)
    expected = _normalize_browser_text(expected_text)
    if not current or not expected:
        return False
    probe = expected[: min(80, len(expected))]
    if probe and probe in current:
        return True
    return len(current) >= max(20, min(120, len(expected) // 3))


def _attachment_present(state: dict, baseline: Optional[dict], expected_name: str) -> bool:
    baseline = baseline or {}
    file_names = [name.lower() for name in state.get("file_names", [])]
    return (
        expected_name in file_names
        or state.get("remove_buttons", 0) > baseline.get("remove_buttons", 0)
        or state.get("preview_images", 0) > baseline.get("preview_images", 0)
    )


def _attachment_progress_active(state: dict) -> bool:
    if state.get("progress_elements", 0):
        return True
    body_text = _normalize_browser_text(state.get("body_text", ""))
    if not body_text:
        return False
    return any(token in body_text for token in _ATTACHMENT_PROGRESS_TOKENS)


def _prompt_dispatch_likely_started(initial_text: str, current_text: str) -> bool:
    initial = _normalize_browser_text(initial_text)
    current = _normalize_browser_text(current_text)
    if not initial:
        return False
    if not current:
        return True
    return len(current) <= max(8, len(initial) // 5)


def _get_generation_timeout_seconds(model: str) -> int:
    normalized = _normalize_browser_text(model)
    if "thinking" in normalized or "思考" in normalized:
        return 180
    return max(30, GEN_TIMEOUT_MS // 1000)


def _saved_image_looks_valid(path: str) -> bool:
    if not path or not os.path.isfile(path):
        return False
    try:
        if os.path.getsize(path) <= 0:
            return False
    except Exception:
        return False

    try:
        from PIL import Image

        with Image.open(path) as img:
            img.verify()
        return True
    except Exception:
        return False


def _accept_saved_image(path: str, source_label: str) -> bool:
    if _saved_image_looks_valid(path):
        return True
    try:
        os.remove(path)
    except Exception:
        pass
    print(f"   ⚠️ {source_label} produced an invalid image file at {path}. Trying next fallback...")
    return False


async def _collect_attachment_state(page: Page) -> dict:
    try:
        return await page.evaluate(
            """() => {
                const isVisible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && rect.width > 0
                        && rect.height > 0;
                };

                const fileNames = Array.from(document.querySelectorAll('input[type="file"]'))
                    .flatMap((el) => el.files ? Array.from(el.files).map((file) => file.name) : []);
                const removeButtons = Array.from(document.querySelectorAll(
                    'button[aria-label^="Remove file"], button[aria-label*="移除"], .remove-file-button'
                )).filter(isVisible).length;
                const previewImages = Array.from(document.querySelectorAll('img')).filter((img) => {
                    if (!isVisible(img)) return false;
                    const rect = img.getBoundingClientRect();
                    if (rect.width < 36 || rect.height < 36) return false;
                    return !img.closest('model-response, .model-response, user-query, user-message, .user-message, .user-prompt, [data-test-id="message-user"]');
                }).length;
                const progressElements = Array.from(document.querySelectorAll(
                    '[role="progressbar"], mat-progress-spinner, .mat-mdc-progress-spinner, [aria-busy="true"]'
                )).filter(isVisible).length;
                return {
                    file_names: fileNames,
                    remove_buttons: removeButtons,
                    preview_images: previewImages,
                    progress_elements: progressElements,
                    body_text: document.body ? document.body.innerText || '' : '',
                };
            }"""
        )
    except Exception:
        return {
            "file_names": [],
            "remove_buttons": 0,
            "preview_images": 0,
            "progress_elements": 0,
            "body_text": "",
        }


async def _wait_for_attachment_ready(
    page: Page,
    input_image_path: str,
    baseline: Optional[dict] = None,
    timeout_seconds: float = 30.0,
) -> bool:
    baseline = baseline or {
        "file_names": [],
        "remove_buttons": 0,
        "preview_images": 0,
        "progress_elements": 0,
        "body_text": "",
    }
    expected_name = os.path.basename(input_image_path).lower()
    stable_hits = 0
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        state = await _collect_attachment_state(page)
        attachment_present = _attachment_present(state, baseline, expected_name)
        progress_active = _attachment_progress_active(state)

        if attachment_present and not progress_active:
            stable_hits += 1
            if stable_hits >= 2:
                return True
        else:
            stable_hits = 0

        await asyncio.sleep(0.75)

    return False


async def _attachment_looks_usable(
    page: Page,
    input_image_path: str,
    baseline: Optional[dict] = None,
) -> bool:
    expected_name = os.path.basename(input_image_path).lower()
    state = await _collect_attachment_state(page)
    if not _attachment_present(state, baseline, expected_name):
        return False
    if not _attachment_progress_active(state):
        return True

    send_btn = await _find_last_visible(page, _SEND_BUTTON_SELECTORS)
    if not send_btn:
        return False

    try:
        is_disabled = await send_btn.evaluate(
            """(el) => {
                const ariaDisabled = (el.getAttribute('aria-disabled') || '').toLowerCase() === 'true';
                const disabled = ('disabled' in el && !!el.disabled)
                    || (typeof el.matches === 'function' && el.matches(':disabled'));
                return ariaDisabled || disabled;
            }"""
        )
        return not is_disabled
    except Exception:
        try:
            return await send_btn.is_visible()
        except Exception:
            return False


async def _probe_single_account(page: Page, authuser: int) -> dict:
    target_url = _get_gemini_url_for(authuser)
    try:
        await page.goto(target_url, wait_until="domcontentloaded", timeout=25000)
        await _wait_for_page_ready(page, timeout=8)
    except Exception:
        pass

    current_url = page.url or ""
    page_text = await _read_page_text(page)
    page_text_lower = page_text.lower()

    if "accounts.google.com" in current_url or "servicelogin" in current_url.lower():
        return _mark_account_state(
            authuser,
            valid=False,
            status="invalid_login",
            cooldown_until=None,
            reason="Gemini login required",
        )

    if "gemini.google.com" not in current_url:
        return _mark_account_state(
            authuser,
            valid=False,
            status="invalid_login",
            cooldown_until=None,
            reason=f"Unexpected URL: {current_url[:120]}",
        )

    # 2026-08-15: authuser>0 accounts that don't exist redirect back to /app (no /u/N/).
    # Without this check, non-existent accounts (e.g. authuser=3/4 when only 0-2 exist)
    # are falsely marked available — Google silently falls back to the default account.
    if authuser > 0:
        expected_markers = [f"/u/{authuser}/", f"authuser={authuser}"]
        if not any(marker in current_url for marker in expected_markers):
            return _mark_account_state(
                authuser,
                valid=False,
                status="invalid_login",
                cooldown_until=None,
                reason=f"authuser={authuser} does not exist (redirected to default)",
            )

    upload_sign_in_markers = (
        "sign in to upload files",
        "登录账号即可上传文件",
        "登录即可上传文件",
    )
    if any(marker in page_text_lower or marker in page_text for marker in upload_sign_in_markers):
        return _mark_account_state(
            authuser,
            valid=False,
            status="invalid_login",
            cooldown_until=None,
            reason="Gemini upload sign-in required",
        )

    if "not available" in page_text_lower or "没有访问权限" in page_text_lower:
        return _mark_account_state(
            authuser,
            valid=False,
            status="invalid_login",
            cooldown_until=None,
            reason="Gemini unavailable for this account",
        )

    is_limited, reset_at = _extract_limit_status(page_text)
    return _mark_account_state(
        authuser,
        valid=True,
        status="limited_until" if is_limited else "available",
        cooldown_until=_format_dt(reset_at),
        reason=f"Thinking limited until {_format_dt(reset_at)}" if is_limited else None,
    )


async def _probe_account_pool(page: Page, force: bool = False) -> dict:
    global _last_probe_ts
    _refresh_account_config()
    if not force and _account_pool and (time.time() - _last_probe_ts) < _ACCOUNT_PROBE_TTL:
        return _account_pool

    _account_pool.clear()
    max_scan = max(_accounts_count, _account_index + 1, 1) + 2
    consecutive_invalid = 0

    print(f"🔎 Probing Gemini accounts from BY browser profile (max authuser={max_scan - 1})...")
    for authuser in range(max_scan):
        state = await _probe_single_account(page, authuser)
        status = state.get("status")
        if status == "invalid_login":
            consecutive_invalid += 1
        else:
            consecutive_invalid = 0

        cooldown = state.get("cooldown_until")
        suffix = f", reset={cooldown}" if cooldown else ""
        print(f"   - authuser={authuser}: {status}{suffix}")

        if authuser >= _accounts_count and consecutive_invalid >= 2:
            break

    _last_probe_ts = time.time()
    available = [s for s in _account_pool.values() if _account_is_available(s)]
    print(f"✅ Gemini account probe complete: usable={len(available)}/{len(_account_pool)}")
    return _account_pool


async def _ensure_usable_account(page: Page) -> bool:
    global _account_index
    _refresh_account_config()
    current_state = await _probe_single_account(page, _account_index)
    current_status = current_state.get("status")
    cooldown = current_state.get("cooldown_until")
    suffix = f", reset={cooldown}" if cooldown else ""
    print(f"🔎 Current Gemini account authuser={_account_index}: {current_status}{suffix}")

    if _account_is_available(current_state):
        return True

    print(f"⚠️ Current Gemini account authuser={_account_index} is not usable. Probing fallback accounts...")
    await _probe_account_pool(page, force=True)
    next_account = _get_next_available_account()
    if next_account is not None:
        if next_account != _account_index:
            _account_index = next_account
            print(f"🔄 Switching to usable account: authuser={_account_index}")
        return True

    print("🛑 No usable Gemini account is currently available.")
    try:
        from core.utils.cookie import open_browser_for_login
        open_browser_for_login(_get_gemini_url(), browser=BROWSER_KEY)
        print(f"🔵 ACTION REQUIRED: Please log in to Gemini in the BY {BROWSER_KEY.title()} profile.")
    except Exception as e:
        print(f"⚠️ Failed to open BY {BROWSER_KEY.title()} profile for Gemini login: {e}")
    return False

def _load_playwright_cookies() -> list[dict]:
    """Load Google/Gemini cookies from the configured browser profile for standalone Playwright fallback."""
    cookies = []
    seen = set()
    for domain in (".google.com", "accounts.google.com", "gemini.google.com"):
        jar = get_cookies_from_browser(domain, browser=BROWSER_KEY, user_data_dir=USER_DATA_DIR)
        if not jar:
            continue
        for cookie in jar:
            key = (cookie.domain, cookie.path, cookie.name)
            if key in seen:
                continue
            seen.add(key)
            expires = cookie.expires if cookie.expires not in (None, 0) else int(time.time() + 3600 * 24 * 30)
            cookies.append({
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path or "/",
                "expires": int(expires),
                "httpOnly": False,
                "secure": bool(cookie.secure),
            })
    return cookies


async def _launch_cookie_injected_browser_context(
    p,
    headless: bool = False,
    *,
    executable_path: Optional[str] = None,
    browser_label: str = "standalone Chromium",
):
    """Launch a fresh Playwright browser context and seed it with BY profile cookies."""
    print(f"   🔄 Launching {browser_label} with injected profile cookies...")
    launch_kwargs = {
        "headless": headless,
        "args": [
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars",
            "--mute-audio",
            "--autoplay-policy=user-gesture-required",
            "--window-size=1280,800",
            # 2026-08-08: 独立 fallback 浏览器默认 off-screen，不弹可见窗口
            #（复用 ego 登录态失败时的兜底，不应打扰用户桌面）
            "--window-position=-2000,-2000",
        ],
    }
    if executable_path and os.path.exists(executable_path):
        launch_kwargs["executable_path"] = executable_path

    browser = await p.chromium.launch(**launch_kwargs)
    context = await browser.new_context()
    cookies = _load_playwright_cookies()
    if cookies:
        await context.add_cookies(cookies)
        print(f"   🍪 Injected {len(cookies)} cookies into standalone Gemini browser.")
    else:
        print("   ⚠️ No cookies found for standalone Gemini browser fallback.")
    page = await context.new_page()
    return browser, context, page


async def _launch_standalone_browser_context(p, headless: bool = False):
    """Fallback when CDP is unavailable: launch the configured browser and inject cookies.

    2026-08-08: 强制 headless（不再依赖调用方传参），兜底浏览器不应弹可见窗口。
    """
    # 2026-08-08: 浏览器操作审计日志（定位窗口来源）
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.expanduser("~/.hermes/scripts"))
        import browser_activity_log
        browser_activity_log.log_browser(
            "launch_browser", str(BROWSER_EXECUTABLE),
            "browser_gemini._launch_standalone_browser_context (headless fallback)",
            extra={"script": "BiYou browser_gemini.py"})
    except Exception:
        pass
    return await _launch_cookie_injected_browser_context(
        p,
        headless=True,
        executable_path=BROWSER_EXECUTABLE,
        browser_label=f"standalone {BROWSER_KEY.title()}",
    )


def _page_is_closed(page: Optional[Page]) -> bool:
    if page is None:
        return True
    try:
        return page.is_closed()
    except Exception:
        return True


async def _get_or_create_gemini_page(browser, preferred_authuser: Optional[int] = None) -> tuple:
    """Reuse a live Gemini tab from the BY browser profile, otherwise use a blank tab or open a fresh tab."""
    if len(browser.contexts) > 0:
        context = browser.contexts[0]
    else:
        context = await browser.new_context()

    matching_gemini = None
    reusable_gemini = None
    for pg in context.pages:
        if _page_is_closed(pg):
            continue
        url = pg.url or ""
        if url.startswith("chrome-extension://") or url.startswith("devtools://") or url.startswith("chrome://"):
            continue
        if "gemini.google.com" in url:
            if preferred_authuser is not None and _extract_authuser(url) == preferred_authuser:
                matching_gemini = pg
                break
            if reusable_gemini is None:
                reusable_gemini = pg
            continue

    if matching_gemini is not None:
        print(f"   ✅ Found existing Gemini tab for authuser={preferred_authuser}, reusing...")
        return context, matching_gemini
    if reusable_gemini is not None:
        print("   ✅ Found existing Gemini tab, reusing...")
        return context, reusable_gemini

    try:
        page = await context.new_page()
    except Exception:
        context = await browser.new_context()
        page = await context.new_page()
    print("   🆕 Opened fresh BY browser tab for Gemini workflow.")
    return context, page


async def _connect_configured_browser(p, startup_delay: int = 5):
    """Connect to the configured browser app running with the BY-specific profile.

    2026-08-10: 不再自动启动 CDP 浏览器实例。旧逻辑在连不上 debug 端口时
    用 _launch_debug_browser() Popen 新开浏览器 —— 这是窗口堆积/进程爆炸的
    另一根因（每次 Gemini 翻译失败就新起一个实例）。现在：如果已有实例在
    debug 端口就复用（CDP），否则直接抛异常让上层走 standalone fallback
    （注入 cookie 的独立上下文，稳定且不碰 ego）。
    """
    global DEBUG_PORT

    launch = prepare_browser_launch(
        browser_executable=BROWSER_EXECUTABLE,
        profile_dir=USER_DATA_DIR,
        preferred_debug_port=DEBUG_PORT,
    )
    DEBUG_PORT = launch["debug_port"]
    if launch["port_changed"]:
        try:
            update_config("browser_debug_port", DEBUG_PORT)
        except Exception:
            pass

    if launch["action"] != "reuse":
        # 2026-08-10: 不 Popen 启动新实例，直接让上层走 standalone fallback。
        print(f"   ⚠️ Configured {BROWSER_KEY.title()} BY profile is not running (no CDP instance). "
              f"Using standalone fallback (CLI/注入 cookie 模式)。")
        raise RuntimeError(
            f"No running {BROWSER_KEY.title()} CDP instance on port {DEBUG_PORT}; "
            f"falling back to standalone context"
        )

    print(f"   🔁 Reusing configured {BROWSER_KEY.title()} BY profile at port {DEBUG_PORT}.")

    cdp_url = f"http://localhost:{DEBUG_PORT}"
    print(f"   🔗 Connecting to {BROWSER_KEY.title()} at {cdp_url}...")
    try:
        return await p.chromium.connect_over_cdp(cdp_url)
    except Exception as exc:
        print(f"   ⚠️ Connection failed: {exc}. Retrying...")
        await asyncio.sleep(2)
        return await p.chromium.connect_over_cdp(cdp_url)

def _launch_debug_browser(launch: Optional[dict] = None):
    """Launch the configured Chromium browser in detached debug mode."""
    global DEBUG_PORT, _OWNS_DEBUG_BROWSER
    if not BROWSER_EXECUTABLE or not os.path.exists(BROWSER_EXECUTABLE):
        print(f"❌ {BROWSER_KEY.title()} executable not found at configured location.")
        return False

    os.makedirs(USER_DATA_DIR, exist_ok=True)

    if launch is None:
        launch = prepare_browser_launch(
            browser_executable=BROWSER_EXECUTABLE,
            profile_dir=USER_DATA_DIR,
            preferred_debug_port=DEBUG_PORT,
        )
    DEBUG_PORT = launch["debug_port"]
    if launch["port_changed"]:
        try:
            update_config("browser_debug_port", DEBUG_PORT)
        except Exception:
            pass

    if launch["action"] == "reuse":
        _OWNS_DEBUG_BROWSER = False
        print(f"🔁 Reusing existing {BROWSER_KEY.title()} profile at port {DEBUG_PORT}.")
        return True

    if launch.get("restart_required"):
        terminate_browser_process(launch.get("process"))

    print(f"🚀 Launching Detached {BROWSER_KEY.title()} (Port {DEBUG_PORT})...")
    
    # Launch command with optimized flags
    cmd = [
        BROWSER_EXECUTABLE,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={USER_DATA_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-infobars",
        "--mute-audio",
        "--autoplay-policy=user-gesture-required",
        "--window-size=1280,800",
        "--window-position=-2000,-2000",  # Position off-screen
        # Keep background activity running
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
    ]
    
    try:
        # Launch detached
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        _OWNS_DEBUG_BROWSER = True
        return True
    except Exception as e:
        print(f"❌ Failed to launch {BROWSER_KEY.title()}: {e}")
        return False

def _hide_chrome_window():
    """Hide Chrome window on macOS"""
    try:
        subprocess.run([
            'osascript', '-e',
            'tell application "System Events" to tell process "Google Chrome" to set visible to false'
        ], capture_output=True, timeout=5)
    except:
        pass

def _copy_image_to_clipboard(path):
    """Copy image to clipboard on macOS using AppleScript"""
    temp_png = None
    target_path = path

    try:
        abs_path = os.path.abspath(path)
        
        # AppleScript (and Mac Clipboard) generally hates WebP.
        # We MUST convert to PNG first.
        if abs_path.lower().endswith('.webp'):
            try:
                from PIL import Image
                im = Image.open(abs_path)
                temp_png = "/tmp/gemini_clip_temp.png"
                im.save(temp_png, "PNG")
                abs_path = temp_png # Use the temp png
                print(f"   Converted WebP to PNG for clipboard: {temp_png}")
            except Exception as e:
                print(f"   ⚠️ Image conversion error: {e}")
                return False

        # AppleScript command
        cmd = f'''
        set the clipboard to (read (POSIX file "{abs_path}") as «class PNGf»)
        '''
        
        process = subprocess.run(['osascript', '-e', cmd], capture_output=True, text=True)
        if process.returncode != 0:
            # Fallback to JPEG attempt
            cmd_jpg = f'''
            set the clipboard to (read (POSIX file "{abs_path}") as JPEG picture)
            '''
            process = subprocess.run(['osascript', '-e', cmd_jpg], capture_output=True, text=True)
            
            if process.returncode != 0:
                print(f"   ⚠️ Clipboard Error: {process.stderr}")
                return False
        
        return True
    except Exception as e:
        print(f"   ⚠️ Clipboard Exception: {e}")
        return False

async def _verify_image_text(image_path: str, prompt: str) -> bool:
    """
    Use Gemini Vision API to verify if the generated Chinese text is garbled.
    Returns True if text is normal or no text, False if noticeably garbled/hallucinated.
    """
    try:
        if not _HAS_WEBAPI:
            print("   ⚠️ Cannot verify image text: gemini-webapi module not installed.")
            return True # Default to pass if no verifier
            
        # Import existing web API client
        from core.utils.cookie import get_gemini_client
        client = await get_gemini_client()
        
        if not client:
             print("   ⚠️ Cannot verify image text: Could not initialize gemini-webapi client.")
             return True
             
        # Use a lightweight fast model for WebAPI
        # gemini_webapi 1.0.1+ 移除 constants.Model; generate_content 也无 model 参数,
        # None 会被 gemini_compat 层自动剥离 → 走默认模型
        _web_model = globals().get('WebModel')
        verify_model = getattr(_web_model, 'ADVANCED_FLASH', None) if _web_model else None
        
        verify_prompt = f"""
你是一个严格的中文字体排版和OCR校对专家。请检查这张图片上的中文字。
任务要求：
这原本是一张带有中文字的图片，原本要求生成的繁体或简体字内容接近："{prompt}"。

请仔细观察图片上的文字（尤其是主要标题部分）：
1. 它们是结构正常、可以被人类准确读出的汉字吗？
2. 还是说它们是 AI 乱画的“鬼画符”、笔画错乱、混杂了无法识别的符号或多余的乱码？
3. 如果图片上很少或没有明显文字，也算通过。

请直接回答 True（字形正常，或无明显乱码）或 False（明显是鬼画符、乱码、无法辨认的残缺汉字）。只回答一个单词。
"""
        print(f"   👁️ Verifying generated image text quality using Gemini WebAPI/Flash...", end="", flush=True)
        response = await generate_content_compat(
             client,
             verify_prompt,
             files=[image_path],
             model=verify_model
        )
        
        if not response or not response.text:
             print(" ⚠️ Verification failed to get text response.")
             return True
             
        result_text = response.text.strip().lower()
        if 'false' in result_text:
            print(" ❌ Failed (Garbled text detected!)")
            return False
            
        print(" ✅ Passed")
        return True
        
    except Exception as e:
        print(f" ⚠️ Verification error: {e}")
        return True # Default pass on error

def _extract_selected_mode_id(raw_value: Optional[str]) -> Optional[str]:
    if not raw_value:
        return None
    match = re.search(r'"([0-9a-f]{16})"', raw_value)
    return match.group(1) if match else None


def _model_matches_target(current_text: str, current_mode_id: Optional[str], target: dict) -> bool:
    target_mode_id = target.get("browser_mode_id")
    if current_mode_id and target_mode_id and current_mode_id == target_mode_id:
        return True
    return normalize_gemini_model_key(current_text or "", default="") == target.get("key", "")


async def _select_model(page: Page, model_name: str = "Thinking"):
    """Select Gemini model using the same aliases as config and gemini-webapi."""
    target_key = normalize_gemini_model_key(model_name, default="Thinking")
    target = get_gemini_web_model_spec(target_key)
    print(f"🔧 Selecting model: {target_key}...")

    async def _click_with_fallback(locator, label: str):
        try:
            await locator.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass

        try:
            await locator.click(timeout=5000)
            return
        except Exception as click_error:
            print(f"   ⚠️ Native click failed for {label}: {click_error}")

        try:
            element = await locator.element_handle()
            if element:
                await page.evaluate(
                    """(el) => {
  try { el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'}); } catch {}
  try { el.click(); } catch {}
}""",
                    element,
                )
                await page.wait_for_timeout(500)
                return
        except Exception as js_error:
            print(f"   ⚠️ JS click failed for {label}: {js_error}")

        raise RuntimeError(f"Could not click {label}")

    async def _locator_is_disabled(locator) -> bool:
        try:
            return await locator.evaluate(
                """(el) => {
                    const ariaDisabled = (el.getAttribute('aria-disabled') || '').toLowerCase() === 'true';
                    const disabled = ('disabled' in el && !!el.disabled)
                        || (typeof el.matches === 'function' && el.matches(':disabled'));
                    return ariaDisabled || disabled;
                }"""
            )
        except Exception:
            return False

    try:
        model_button = page.locator('button.input-area-switch').first

        if await model_button.count() == 0:
            print("   ⚠️ Model selector button not found. Using default model.")
            return

        current_text = (await model_button.text_content() or "").strip()
        current_mode_id = _extract_selected_mode_id(await model_button.get_attribute("jslog"))
        print(f"   📍 Current model: '{current_text}' (mode_id={current_mode_id})")

        if _model_matches_target(current_text, current_mode_id, target):
            print(f"   ✅ Already on {target_key} model")
            return

        print("   🖱️ Clicking model selector...")
        await page.evaluate("window.scrollTo(0, 0)")
        await _click_with_fallback(model_button, "model selector")
        try:
            await page.locator('button.bard-mode-list-button').first.wait_for(state="visible", timeout=3000)
        except Exception:
            pass

        option = None
        target_mode_id = target.get("browser_mode_id")
        # 2026-09-16: Gemini 改版后下拉项不再是 button.bard-mode-list-button，
        # 变成 [role=menuitem][data-mode-id]。两种选择器都试，兼容新旧 UI。
        _OPTION_SELECTORS = (
            '[data-mode-id="{v}"]',
            'button.bard-mode-list-button[data-mode-id="{v}"]',
        )
        if target_mode_id:
            for sel in _OPTION_SELECTORS:
                option = page.locator(sel.format(v=target_mode_id)).first
                if await option.count() > 0:
                    print(f"   ✅ Found {target_key} option by mode id {target_mode_id}")
                    break

        if not option or await option.count() == 0:
            for label in target.get("browser_labels", ()):
                for sel in ('[role=menuitem]:has-text("{v}")',
                            '[role=option]:has-text("{v}")',
                            'button.bard-mode-list-button:has-text("{v}")'):
                    option = page.locator(sel.format(v=label)).first
                    if await option.count() > 0:
                        print(f"   ✅ Found {target_key} option by label '{label}'")
                        break
                if option and await option.count() > 0:
                    break

        if option and await option.count() > 0:
            if await _locator_is_disabled(option):
                raise ValueError(
                    f"MODEL_UNAVAILABLE: Required model '{target_key}' is present but disabled in the current Gemini session."
                )
            await _click_with_fallback(option, f"{target_key} option")
            if not await _wait_for_model_ready(page, target_key, timeout=5):
                print("   ⚠️ Model switch may not have taken effect yet, continuing...")
            current_text = (await model_button.text_content() or "").strip()
            current_mode_id = _extract_selected_mode_id(await model_button.get_attribute("jslog"))
            if not _model_matches_target(current_text, current_mode_id, target):
                raise ValueError(
                    f"MODEL_UNAVAILABLE: Required model '{target_key}' did not become active after selection."
                )
            print(f"   ✅ Switched to {target_key} model")
            return

        print(f"   ⚠️ Could not find {target_key} option in dropdown.")
        await page.mouse.click(100, 100)

        if target_key in ["Thinking", "Pro", "Flash"]:
            raise ValueError(
                f"CRITICAL: Required model '{target_key}' could not be selected. Aborting to prevent bad generation."
            )

    except Exception as e:
        print(f"   ⚠️ Model selection failed: {e}.")
        raise e

async def _perform_generation_attempt(
    page: Page,
    prompt: str,
    input_image_path: str,
    output_path: str,
    model: str,
    attempt: int
) -> Optional[str]:
    """Single attempt at prompting and waiting for image"""
    has_input = bool(input_image_path) and os.path.exists(input_image_path)
    if "accounts.google.com" in (page.url or ""):
        _mark_account_state(
            _account_index,
            valid=False,
            status="invalid_login",
            cooldown_until=None,
            reason="Gemini login required",
        )
        return "ACCOUNT_LOGIN_REQUIRED"
    
    # Prompt
    print(f"📝 Entering prompt (Attempt {attempt})...", flush=True)
    try:
        await page.evaluate(
            """() => {
                try {
                    if (document.activeElement && typeof document.activeElement.blur === 'function') {
                        document.activeElement.blur();
                    }
                    if (document.body && typeof document.body.click === 'function') {
                        document.body.click();
                    }
                } catch (_) {}
            }"""
        )
    except Exception as e:
        print(f"   ⚠️ Failed to click body: {e}", flush=True)

    # Upload: We MUST ensure the correct image is uploaded for every generation (especially when aspect ratio changes).
    # If there's an existing image preview, remove it so we don't send multiple or the wrong one.
    try:
        # Click the "remove file" button on existing previews if any
        remove_btns = page.locator('button[aria-label^="Remove file"], button[aria-label*="移除"], .remove-file-button')
        count = await remove_btns.count()
        for i in range(count):
            if await remove_btns.nth(i).is_visible():
                await remove_btns.nth(i).click()
                await page.wait_for_timeout(500)
    except Exception as e:
        print(f"   ⚠️ Could not remove existing image preview: {e}")

    # Now upload the new image (skip for pure text-to-image: no source image)
    upload_ok = False
    if has_input:
        try:
            upload_ok = await _upload_source_image(page, input_image_path)
        except Exception as e:
            print(f"   ⚠️ UI Upload Error: {e}")
            try:
                await page.set_input_files('input[type="file"]', str(input_image_path))
                upload_ok = await _wait_for_attachment_ready(page, input_image_path)
                if upload_ok:
                    print("   ✅ Uploaded via final direct-file fallback.")
                else:
                    print("   ⚠️ Final direct-file fallback did not produce a stable attachment.")
            except Exception as inner_e:
                print(f"   ❌ Final direct-file fallback failed: {inner_e}")
    else:
        upload_ok = True  # 纯文生图：无附件需上传

    if not upload_ok:
        print("   ❌ Upload step failed before generation.")
        return None

    # Fill the prompt after upload because Gemini often remounts the composer when an attachment is added.
    input_box = await _wait_for_visible(page, _PROMPT_INPUT_SELECTORS, timeout_seconds=8.0)
    if not input_box:
        print("❌ Input box not found after upload.", flush=True)
        return None

    prompt_text = prompt.strip()
    prompt_ok = await _focus_and_fill_prompt_input(page, input_box, prompt_text + " ")
    if not prompt_ok:
        print("   ❌ Prompt text did not stick in Gemini input box after upload.")
        return None
    await _wait_for_send_ready(page, timeout=3)

    initial_response_count = 0
    initial_user_query_count = 0
    old_img_srcs = set()
    try:
        initial_response_count = await page.locator('model-response, .model-response').count()
        initial_user_query_count = await page.locator(
            'user-query, user-message, .user-message, .user-prompt, [data-test-id="message-user"]'
        ).count()
        urls = await page.evaluate('''() => {
            return Array.from(document.querySelectorAll("img")).map(img => img.src).filter(src => src);
        }''')
        if urls: old_img_srcs = set(urls)
    except: pass

    send_success = False
    try:
        input_box = await _find_last_visible(page, _PROMPT_INPUT_SELECTORS) or input_box
        send_btn = await _find_last_visible(page, _SEND_BUTTON_SELECTORS)
        initial_prompt_input_text = await _read_prompt_input_text(input_box)
        initial_attachment_state = await _collect_attachment_state(page)

        async def _conversation_started() -> bool:
            try:
                current_user_queries = await page.locator(
                    'user-query, user-message, .user-message, .user-prompt, [data-test-id="message-user"]'
                ).count()
                if current_user_queries > initial_user_query_count:
                    return True
            except Exception:
                pass
            try:
                if await page.locator('model-response, .model-response').count() > initial_response_count:
                    return True
            except Exception:
                pass
            try:
                if await page.locator(
                    'mat-progress-spinner, [role="progressbar"], button[aria-label*="Stop"], button[aria-label*="停止"], [aria-label*="思考"], [aria-label*="thinking"]'
                ).count() > 0:
                    return True
            except Exception:
                pass
            try:
                latest_input_box = await _find_last_visible(page, _PROMPT_INPUT_SELECTORS) or input_box
                current_prompt_text = await _read_prompt_input_text(latest_input_box)
                if _prompt_dispatch_likely_started(initial_prompt_input_text, current_prompt_text):
                    return True
            except Exception:
                pass
            try:
                current_attachment_state = await _collect_attachment_state(page)
                if current_attachment_state.get("remove_buttons", 0) < initial_attachment_state.get("remove_buttons", 0):
                    return True
            except Exception:
                pass
            try:
                body_text = (await page.evaluate("document.body.innerText || ''")).lower()
                for token in ("gemini 说", "你说", "思考中", "正在生成", "creating your image", "stop response", "停止这条回答"):
                    if token in body_text:
                        return True
            except Exception:
                pass
            return False

        async def _try_send_attempt(label: str, action) -> bool:
            try:
                await action()
                deadline = time.time() + 4.5
                while time.time() < deadline:
                    if await _conversation_started():
                        print(f"   ✅ Send triggered via {label}.")
                        return True
                    await asyncio.sleep(0.45)
            except Exception as exc:
                print(f"   ⚠️ Send attempt failed via {label}: {exc}")
            return False

        wait_deadline = time.time() + 5
        while time.time() < wait_deadline:
            try:
                input_box = await _find_last_visible(page, _PROMPT_INPUT_SELECTORS) or input_box
                send_btn = await _find_last_visible(page, _SEND_BUTTON_SELECTORS) or send_btn
                send_visible = bool(send_btn and await send_btn.is_visible())
            except Exception:
                send_visible = False

            send_actions = []
            if send_visible and send_btn:
                send_actions.extend(
                    [
                        ("send button click", lambda: _scroll_and_click(page, send_btn)),
                        ("send button js click", lambda: send_btn.evaluate("(el) => el.click()")),
                    ]
                )

            send_actions.extend(
                [
                    ("Enter", lambda: _send_via_key(input_box, "Enter")),
                    ("Control+Enter", lambda: _send_via_key(input_box, "Control+Enter")),
                    ("Meta+Enter", lambda: _send_via_key(input_box, "Meta+Enter")),
                ]
            )
            if send_visible and send_btn:
                send_actions.append(
                    (
                        "closest form submit",
                        lambda: send_btn.evaluate(
                            """(el) => {
                                const form = el.closest('form');
                                if (form && typeof form.requestSubmit === 'function') {
                                    form.requestSubmit();
                                    return;
                                }
                                el.click();
                            }"""
                        ),
                    )
                )

            for label, action in send_actions:
                if await _try_send_attempt(label, action):
                    send_success = True
                    break

            if send_success:
                break

            print("   ⏳ Waiting for Gemini send action to start a conversation...")
            await asyncio.sleep(1)

        if not send_success:
            print("   ❌ Gemini send action never started a conversation.")
            return "SEND_NOT_STARTED"
    except Exception as e:
        print(f"   ⚠️ Upload/Send Error: {e}")

    # Wait for Generation
    print("⏳ Waiting for generation (Monitoring UI)...")
    generated_img_element = None
    found_img = False
    start_time = time.time()
    saw_text_only_response = False
    text_only_preview = ""
    latest_response_preview = ""
    timeout_seconds = _get_generation_timeout_seconds(model)
    extended_grace_applied = False

    while time.time() - start_time < timeout_seconds:
        elapsed = int(time.time() - start_time)
        if elapsed % 5 == 0:
            print(f"   ... {elapsed}s", end="\r")
        
        await page.keyboard.press("End")

        try:
            body_text = await _read_page_text(page)
        except Exception:
            body_text = ""

        if _looks_like_generation_failure(latest_response_preview, body_text):
            print(f"\n   ⚠️ Detected Gemini failure state in UI, aborting image wait early.")
            failure_preview = _normalize_browser_text(body_text)[:220]
            if failure_preview:
                print(f"   📝 Failure preview: {failure_preview}")
            return "REQUEST_FAILED"
        
        try:
            responses = page.locator('model-response, .model-response')
            current_count = await responses.count()
            
            if current_count > 0:
                # Wait for a NEW response bubble if we had previous responses
                if current_count == initial_response_count:
                    await asyncio.sleep(2)
                    continue
                    
                last_response = responses.last
                # --- 限额检测 ---
                try:
                    # Check the entire page body text for limit banners or messages
                    page_text = await page.evaluate("document.body.innerText")
                    if page_text:
                        limit_keywords = [
                            "达到上限", "limit reached", "can't create more images", 
                            "每日限额", "用量限额", "can't generate more images", 
                            "come back tomorrow", "exceeded your quota",
                            "can create more images", "limit resets", "check your usage",
                            "as soon as your limit", "usage in settings"
                        ]
                        page_text_lower = page_text.lower()
                        if any(kw.lower() in page_text_lower for kw in limit_keywords):
                            print(f"\n   🛑 ACCOUNT LIMIT DETECTED IN UI")
                except: pass
                
                # Check for account limit
                try:
                    limit_indicators = ["达到上限", "limit reached", "用量限额", "can't generate more images", "明天再试", "check back tomorrow", "can create more images", "limit resets", "check your usage", "as soon as your limit", "usage in settings"]
                    page_text = await page.evaluate("document.body.innerText")
                    if any(indicator in page_text for indicator in limit_indicators):
                         reset_at = _parse_limit_reset_time(page_text)
                         _mark_account_state(
                             _account_index,
                             valid=True,
                             status="limited_until",
                             cooldown_until=_format_dt(reset_at),
                             reason=f"Thinking limited until {_format_dt(reset_at)}" if reset_at else "Thinking limit reached",
                         )
                         print(f"\n   🛑 ACCOUNT LIMIT DETECTED IN UI")
                         if reset_at:
                             print(f"   ⏰ Thinking limit resets at {_format_dt(reset_at)}")
                         return "ACCOUNT_LIMIT"
                except: pass

                # 1. Look for images in the last response bubble (Primary)
                candidates = last_response.locator('img')
                count = await candidates.count()
                
                if count > 0:
                    for i in range(count - 1, -1, -1):
                        img = candidates.nth(i)
                        try:
                            if await img.is_visible():
                                box = await img.bounding_box()
                                if box and box['width'] > 150 and box['height'] > 150:
                                    # Filter out icons/avatars by checking src if needed, but size is usually enough
                                    src = await img.get_attribute("src") or ""
                                    if src and src in old_img_srcs:
                                        continue
                                        
                                    if "googleusercontent.com" in src or "blob:" in src or "lh3.googleusercontent" in src:
                                        print(f"\n   ✨ Generated Image Detected! ({int(box['width'])}x{int(box['height'])})")
                                        generated_img_element = img
                                        found_img = True
                                        break
                        except: pass
                
                # 2. Global fallback (if not in bubble or bubble structure changed)
                if not found_img:
                     # Gemini sometimes uses a global gallery or shared container
                     all_imgs = page.locator('img')
                     acount = await all_imgs.count()
                     for i in range(acount - 1, max(-1, acount - 15), -1):
                          img = all_imgs.nth(i)
                          try:
                              if await img.is_visible():
                                  box = await img.bounding_box()
                                  src = await img.get_attribute("src") or ""
                                  # Generated images are usually from googleusercontent or blobs
                                  if box and box['width'] > 150 and box['height'] > 150:
                                      try:
                                          # Prevent grabbing the uploaded image from the user's prompt bubble
                                          in_user = await img.evaluate('el => el.closest("user-query, .user-message, .user-prompt, user-message, [data-test-id=\\"message-user\\"]") !== null')
                                          if in_user: continue
                                      except: pass
                                          
                                      if src and src in old_img_srcs:
                                          continue
                                          
                                      if "googleusercontent" in src or "blob:" in src or "lh3.googleusercontent" in src:
                                          print(f"\n   ✨ Generated Image Detected (Global)! ({int(box['width'])}x{int(box['height'])}) - {src[:30]}...")
                                          generated_img_element = img
                                          found_img = True
                                          break
                          except: pass

                # 2.5 Canvas/background-image fallback.
                # Gemini UI sometimes renders generated images inside <canvas> or
                # generic containers with CSS background-image instead of <img>.
                # In those cases the old detector incorrectly concluded that no
                # image was produced, even though a visible artifact existed.
                if not found_img:
                    visual_candidates = page.locator('canvas, [style*="background-image"], [data-test-id*="image"], [aria-label*="image" i], [aria-label*="图像"], [aria-label*="图片"]')
                    vcount = await visual_candidates.count()
                    for i in range(vcount - 1, max(-1, vcount - 25), -1):
                        candidate = visual_candidates.nth(i)
                        try:
                            if not await candidate.is_visible():
                                continue
                            box = await candidate.bounding_box()
                            if not box or box['width'] <= 150 or box['height'] <= 150:
                                continue

                            try:
                                in_user = await candidate.evaluate('el => el.closest("user-query, .user-message, .user-prompt, user-message, [data-test-id=\\"message-user\\"]") !== null')
                                if in_user:
                                    continue
                            except:
                                pass

                            meta = await candidate.evaluate('''el => {
                                const style = window.getComputedStyle(el);
                                return {
                                    tag: (el.tagName || '').toLowerCase(),
                                    src: el.getAttribute?.('src') || '',
                                    bg: style?.backgroundImage || '',
                                    text: (el.innerText || '').slice(0, 80),
                                };
                            }''')
                            src = (meta or {}).get('src', '') or ''
                            bg = (meta or {}).get('bg', '') or ''
                            tag = (meta or {}).get('tag', '') or 'node'

                            if src and src in old_img_srcs:
                                continue
                            if bg and any(old in bg for old in old_img_srcs if old):
                                continue

                            if src.startswith('blob:') or 'googleusercontent' in src or 'lh3.googleusercontent' in src or 'blob:' in bg or 'googleusercontent' in bg or tag == 'canvas':
                                print(f"\n   ✨ Generated Visual Detected ({tag})! ({int(box['width'])}x{int(box['height'])})")
                                if bg:
                                    print(f"      background-image: {bg[:80]}")
                                generated_img_element = candidate
                                found_img = True
                                break
                        except:
                            pass
                
                if found_img:
                    break

                # 3. Detect text-only responses so we can fail fast instead of
                # waiting for the full image timeout.
                try:
                    response_text = (await last_response.inner_text()).strip()
                    if response_text:
                        latest_response_preview = response_text[:180].replace("\n", " ")
                        if _looks_like_hard_text_only_response(response_text):
                            saw_text_only_response = True
                            text_only_preview = response_text[:180].replace("\n", " ")
                except Exception:
                    pass

                if saw_text_only_response:
                    try:
                        still_generating = _looks_like_generation_in_progress(
                            text_only_preview,
                            body_text,
                        )
                    except Exception:
                        still_generating = False

                    if not still_generating and elapsed >= 8:
                        print(f"\n   ⚠️ Detected text-only Gemini response, aborting image wait early.")
                        if text_only_preview:
                            print(f"   📝 Text-only preview: {text_only_preview}")
                        return "TEXT_ONLY_RESPONSE"
        except Exception as e: pass

        if not found_img and not extended_grace_applied and time.time() - start_time >= timeout_seconds:
            if _looks_like_generation_in_progress(latest_response_preview, body_text):
                timeout_seconds += 60
                extended_grace_applied = True
                print(f"\n   ⏳ Gemini still reports image generation in progress. Extending wait to {timeout_seconds}s...")
                continue

        await asyncio.sleep(2)
        await page.wait_for_timeout(1000)
    
    if generated_img_element:
        print("📥 Found image, downloading...")
        target_src = await generated_img_element.get_attribute("src")
        download_success = False
        
        if target_src and (target_src.startswith("http") or target_src.startswith("blob:")):
            print(f"   Fetching from URL: {target_src[:60]}...")
            try:
                fetch_result = await page.evaluate(r'''async (src) => {
                    try {
                        // For regular URLs, try to get original size
                        let fetchUrl = src;
                        if (src.startsWith("http") && !src.includes("blob:")) {
                             fetchUrl = src.replace(/=s\d+(?=[-?#]|$)/, "=s0");
                        }
                        
                        let response = await fetch(fetchUrl);
                        let source = "original";
                        
                        // Fallback to original src if s0 fails
                        if (!response.ok && fetchUrl !== src) {
                            response = await fetch(src);
                            source = "fallback";
                        }
                        
                        if (!response.ok) return { error: "Status " + response.status };
                        
                        const blob = await response.blob();
                        return await new Promise((resolve) => {
                            const reader = new FileReader();
                            reader.onloadend = () => resolve({ data: reader.result, source: source });
                            reader.readAsDataURL(blob);
                        });
                    } catch (e) { return { error: e.toString() }; }
                }''', target_src)
                
                if fetch_result and 'data' in fetch_result:
                    base64_data = fetch_result.get('data', '')
                    source_type = fetch_result.get('source', 'unknown')
                    if base64_data and "," in base64_data:
                        import base64
                        import re
                        _, encoded = base64_data.split(",", 1)
                        encoded = re.sub(r'\s', '', encoded)
                        if len(encoded) > 100:
                            missing_padding = len(encoded) % 4
                            if missing_padding: encoded += '=' * (4 - missing_padding)
                            data = base64.b64decode(encoded)
                            if len(data) > 0:
                                with open(output_path, "wb") as f:
                                    f.write(data)
                                if _accept_saved_image(output_path, "JS Fetch"):
                                    if source_type == 'original':
                                        print(f"   ✅ Saved via JS Fetch (Original Quality) to {output_path} ({len(data)} bytes)")
                                    else:
                                        print(f"   ⚠️ Saved via JS Fetch (Fallback Quality) to {output_path} ({len(data)} bytes)")
                                    download_success = True
                elif fetch_result and 'error' in fetch_result:
                    print(f"   ⚠️ JS Fetch error: {fetch_result.get('error')}")
            except Exception as e:
                print(f"   ⚠️ JS Fetch failed: {str(e).split(chr(10))[0][:80]}")

        # Canvas extraction: draw the <img> to a canvas and export as PNG.
        # Works even after blob: URLs are revoked because the img element
        # retains its decoded pixel data.
        if not download_success:
            try:
                canvas_result = await page.evaluate(r'''(imgEl) => {
                    try {
                        if (!imgEl || !imgEl.naturalWidth) return { error: "Image not loaded" };
                        const canvas = document.createElement('canvas');
                        canvas.width = imgEl.naturalWidth;
                        canvas.height = imgEl.naturalHeight;
                        const ctx = canvas.getContext('2d');
                        ctx.drawImage(imgEl, 0, 0);
                        return { data: canvas.toDataURL('image/png') };
                    } catch (e) { return { error: e.toString() }; }
                }''', await generated_img_element.element_handle())

                if canvas_result and 'data' in canvas_result:
                    base64_data = canvas_result['data']
                    if base64_data and "," in base64_data:
                        import base64 as b64mod
                        import re as re_mod
                        _, encoded = base64_data.split(",", 1)
                        encoded = re_mod.sub(r'\s', '', encoded)
                        if len(encoded) > 100:
                            missing_padding = len(encoded) % 4
                            if missing_padding: encoded += '=' * (4 - missing_padding)
                            data = b64mod.b64decode(encoded)
                            if len(data) > 0:
                                with open(output_path, "wb") as f:
                                    f.write(data)
                                if _accept_saved_image(output_path, "Canvas"):
                                    print(f"   ✅ Saved via Canvas extraction to {output_path} ({len(data)} bytes)")
                                    download_success = True
                if not download_success and canvas_result and 'error' in canvas_result:
                    print(f"   ⚠️ Canvas extraction error: {canvas_result.get('error')}")
            except Exception as e:
                print(f"   ⚠️ Canvas extraction failed: {str(e).split(chr(10))[0][:80]}")

        if not download_success:
            try:
                await generated_img_element.hover()
                await page.wait_for_timeout(500)
                download_btn = page.locator('button[aria-label*="Download"], button[aria-label*="下载"], mat-icon[data-mat-icon-name="download"]').last
                if await download_btn.count() > 0:
                     async with page.expect_download() as download_info:
                         await download_btn.click()
                     download = await download_info.value
                     await download.save_as(output_path)
                     if _accept_saved_image(output_path, "Download Button"):
                         print(f"   ✅ Saved via Download Button to {output_path}")
                         download_success = True
            except Exception as e:
                print(f"   ⚠️ Download Button failed: {e}")
        
        if not download_success:
            print("   ⚠️ Using screenshot fallback.")
            try:
                await generated_img_element.scroll_into_view_if_needed()
                await page.wait_for_timeout(500)
                await generated_img_element.screenshot(path=output_path)
                if _accept_saved_image(output_path, "Screenshot"):
                    print(f"   ✅ Saved screenshot to {output_path}")
                    download_success = True
            except Exception as e:
                print(f"   ❌ Screenshot failed: {e}")
                
        if download_success:
            return output_path

    return None


async def _focus_and_fill_prompt_input(page: Page, input_box, value: str) -> bool:
    """Focus Gemini's prompt box robustly across contenteditable/textarea variants."""
    handle = await input_box.element_handle()
    if handle:
        try:
            await page.evaluate(
                """(el) => {
                    el.scrollIntoView({block: 'center', inline: 'nearest'});
                    el.focus();
                }""",
                handle,
            )
        except Exception:
            pass

    try:
        await input_box.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        pass

    try:
        await input_box.click(force=True, timeout=5000)
    except Exception:
        if handle:
            await page.evaluate(
                """(el) => {
                    el.scrollIntoView({block: 'center', inline: 'nearest'});
                    el.focus();
                }""",
                handle,
            )

    try:
        await input_box.evaluate(
            """(el) => {
                if ('value' in el) {
                    el.value = '';
                }
                if ('textContent' in el) {
                    el.textContent = '';
                }
                if ('innerHTML' in el) {
                    el.innerHTML = '';
                }
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            }"""
        )
    except Exception:
        pass

    try:
        is_contenteditable = await input_box.evaluate(
            "(el) => !!el.isContentEditable && !('value' in el)"
        )
    except Exception:
        is_contenteditable = False

    if is_contenteditable:
        try:
            await input_box.focus()
            await page.keyboard.press("Meta+A")
            await page.keyboard.press("Backspace")
            await page.keyboard.insert_text(value)
            current_text = await _read_prompt_input_text(input_box)
            if _prompt_text_matches(current_text, value):
                return True
        except Exception:
            pass

    try:
        await input_box.fill(value, timeout=5000)
        current_text = await _read_prompt_input_text(input_box)
        if _prompt_text_matches(current_text, value):
            return True
    except Exception:
        pass

    if handle:
        try:
            await page.evaluate(
                """(el, text) => {
                    el.scrollIntoView({block: 'center', inline: 'nearest'});
                    el.focus();
                    if ('value' in el) {
                        el.value = text;
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        return;
                    }
                    el.textContent = text;
                    el.dispatchEvent(new InputEvent('input', {
                        bubbles: true,
                        data: text,
                        inputType: 'insertText'
                    }));
                }""",
                handle,
                value,
            )
            current_text = await _read_prompt_input_text(input_box)
            if _prompt_text_matches(current_text, value):
                return True
        except Exception:
            pass

    try:
        await input_box.focus()
        await page.keyboard.insert_text(value)
        current_text = await _read_prompt_input_text(input_box)
        return _prompt_text_matches(current_text, value)
    except Exception:
        return False


async def _upload_source_image(page: Page, input_image_path: str) -> bool:
    """Upload the source image using the current Gemini Web uploader DOM."""
    print("📎 Uploading source image...")
    baseline = await _collect_attachment_state(page)

    async def _try_existing_file_inputs(label: str) -> bool:
        try:
            file_inputs = page.locator('input[type="file"]')
            count = await file_inputs.count()
            for idx in range(count):
                candidate = file_inputs.nth(idx)
                try:
                    await candidate.set_input_files(str(input_image_path))
                    if await _wait_for_attachment_ready(page, input_image_path, baseline=baseline):
                        print(f"   ✅ Uploaded via {label} (input #{idx + 1}/{count}).")
                        return True
                    if await _attachment_looks_usable(page, input_image_path, baseline=baseline):
                        print(
                            f"   ✅ Uploaded via {label} (input #{idx + 1}/{count}) "
                            "after readiness probe timed out."
                        )
                        return True
                except Exception as inner_exc:
                    print(f"   ⚠️ {label} input #{idx + 1} failed: {inner_exc}")
            if count > 0:
                print(f"   ⚠️ {label} found {count} file input(s), but none produced a stable attachment.")
        except Exception as e:
            print(f"   ⚠️ {label} file-input scan failed: {e}")
        return False

    hidden_upload_selectors = [
        '[data-test-id="hidden-local-image-upload-button"]',
        '[data-test-id="hidden-local-file-upload-button"]',
        '.hidden-local-upload-button',
        '.hidden-local-file-upload-button',
        '.hidden-local-file-image-selector-button',
    ]

    if await _try_existing_file_inputs("direct file input"):
        return True

    local_upload_item = await _wait_for_visible(page, _VISIBLE_UPLOAD_MENU_SELECTORS, timeout_seconds=1.5)
    if local_upload_item is None:
        trigger = await _wait_for_visible(page, _UPLOAD_MENU_TRIGGER_SELECTORS, timeout_seconds=3.0)
        if not trigger:
            print("   ⚠️ Upload menu trigger not found.")
            return False

        await _scroll_and_click(page, trigger)
        await page.wait_for_timeout(400)
        if await _try_existing_file_inputs("post-trigger direct file input"):
            return True
        local_upload_item = await _wait_for_visible(page, _VISIBLE_UPLOAD_MENU_SELECTORS, timeout_seconds=3.0)

    if local_upload_item:
        try:
            await local_upload_item.scroll_into_view_if_needed(timeout=5000)
        except Exception:
            pass

        try:
            async with page.expect_file_chooser(timeout=10000) as fc_info:
                await local_upload_item.click(force=True, timeout=5000)
            file_chooser = await fc_info.value
            await file_chooser.set_files(str(input_image_path))
            if await _wait_for_attachment_ready(page, input_image_path, baseline=baseline):
                print("   ✅ File uploaded via visible local upload menu.")
                return True
            if await _attachment_looks_usable(page, input_image_path, baseline=baseline):
                print("   ✅ File uploaded via visible local upload menu after readiness timeout.")
                return True
            print("   ⚠️ Visible local upload menu fired chooser but attachment never stabilized.")
        except Exception as e:
            print(f"   ⚠️ Visible local upload menu path failed: {e}")

        try:
            await local_upload_item.click(force=True, timeout=5000)
            await page.wait_for_timeout(800)
        except Exception as e:
            print(f"   ⚠️ Visible local upload menu re-click failed: {e}")

        if await _try_existing_file_inputs("post-menu direct file input"):
            return True
    else:
        print("   ⚠️ Local upload menu item not found after opening upload menu.")

    for sel in hidden_upload_selectors:
        hidden_btn = page.locator(sel).first
        if await hidden_btn.count() == 0:
            continue
        try:
            await page.evaluate(
                """(selector) => {
                    const btn = document.querySelector(selector);
                    if (btn) btn.click();
                }""",
                sel,
            )
            await page.wait_for_timeout(300)
        except Exception:
            pass

        if await _try_existing_file_inputs(f"hidden button direct input {sel}"):
            return True

        try:
            async with page.expect_file_chooser(timeout=5000) as fc_info:
                await page.evaluate(
                    """(selector) => {
                        const btn = document.querySelector(selector);
                        if (btn) btn.click();
                    }""",
                    sel,
                )
            file_chooser = await fc_info.value
            await file_chooser.set_files(str(input_image_path))
            if await _wait_for_attachment_ready(page, input_image_path, baseline=baseline):
                print(f"   ✅ File uploaded via hidden button: {sel}")
                return True
            if await _attachment_looks_usable(page, input_image_path, baseline=baseline):
                print(f"   ✅ File uploaded via hidden button after readiness timeout: {sel}")
                return True
            print(f"   ⚠️ Hidden button chooser fired but attachment never stabilized: {sel}")
        except Exception as e:
            print(f"   ⚠️ Hidden button upload path failed ({sel}): {e}")

        if await _try_existing_file_inputs(f"hidden button fallback {sel}"):
            return True

    if await _attachment_looks_usable(page, input_image_path, baseline=baseline):
        print("   ✅ Existing attachment is already usable; skipping clipboard fallback.")
        return True

    try:
        if _copy_image_to_clipboard(str(input_image_path)):
            focus_target = await _wait_for_visible(page, _PROMPT_INPUT_SELECTORS, timeout_seconds=4.0)
            if focus_target:
                await _scroll_and_click(page, focus_target)
                await page.keyboard.press("Meta+V")
                await page.wait_for_timeout(2500)

                preview_locators = [
                    'button[aria-label^="Remove file"]',
                    'button[aria-label*="移除"]',
                    '.remove-file-button',
                    'img[src^="blob:"]',
                    'img[src^="data:image"]',
                ]
                for sel in preview_locators:
                    try:
                        if await page.locator(sel).count() > 0:
                            if await _wait_for_attachment_ready(page, input_image_path, baseline=baseline):
                                print("   ✅ File uploaded via clipboard paste.")
                                return True
                            if await _attachment_looks_usable(page, input_image_path, baseline=baseline):
                                print("   ✅ File uploaded via clipboard paste after readiness timeout.")
                                return True
                    except Exception:
                        pass

                send_btn = await _find_last_visible(page, _SEND_BUTTON_SELECTORS)
                try:
                    if send_btn and await _attachment_looks_usable(page, input_image_path, baseline=baseline):
                        print("   ✅ Clipboard paste enabled a usable attachment.")
                        return True
                except Exception:
                    pass

            print("   ⚠️ Clipboard paste did not produce a visible preview.")
        else:
            print("   ⚠️ Clipboard image preparation failed.")
    except Exception as e:
        print(f"   ⚠️ Clipboard upload fallback failed: {e}")

    print("   ❌ Failed to upload source image.")
    return False


async def _scroll_and_click(page: Page, locator, timeout: int = 5000):
    """Click a locator even when Gemini's layout leaves it outside the viewport."""
    handle = await locator.element_handle()
    if handle:
        try:
            await page.evaluate(
                """(el) => {
                    el.scrollIntoView({block: 'center', inline: 'nearest'});
                }""",
                handle,
            )
        except Exception:
            pass

    try:
        await locator.scroll_into_view_if_needed(timeout=timeout)
    except Exception:
        pass

    try:
        await locator.click(force=True, timeout=timeout)
        return
    except Exception:
        pass

    if handle:
        await page.evaluate(
            """(el) => {
                el.scrollIntoView({block: 'center', inline: 'nearest'});
                el.click();
            }""",
            handle,
        )


async def _send_via_key(input_box, key_combo: str):
    await input_box.focus()
    await input_box.press(key_combo)

async def generate_image_browser(
    prompt: str,
    input_image_path: str,
    output_path: str,
    model: str = "Thinking",
    headless: bool = False
) -> Optional[str]:
    """
    Generate image using Gemini Web via Playwright.
    """
    print("🌐 Browser Automation: Generating Image")
    print(f"   Prompt: {prompt[:50]}...")
    
    if not input_image_path or not os.path.exists(input_image_path):
        # input_image_path 为空字符串 = 纯文生图（无源图），允许继续；
        # 给了路径但文件不存在才真正报错。
        if input_image_path:
            print(f"❌ Input file not found: {input_image_path}")
            return None
    has_input = bool(input_image_path) and os.path.exists(input_image_path)

    if os.path.dirname(output_path):
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    async with async_playwright() as p:
        browser = None
        launched_standalone = False
        try:
            try:
                try:
                    browser = await _connect_configured_browser(p)
                except Exception as e:
                    print(f"   ⚠️ Connection failed: {e}. Retrying...")
                    await asyncio.sleep(2)
                    browser, context, page = await _launch_standalone_browser_context(p, headless=headless)
                    launched_standalone = True
                else:
                    context, page = await _get_or_create_gemini_page(browser, preferred_authuser=_account_index)
                    print(f"   ✅ Using configured {BROWSER_KEY.title()} profile for Gemini image workflow.")
            except Exception as browser_exc:
                print(
                    f"   ⚠️ Configured {BROWSER_KEY.title()} session failed: {browser_exc}. "
                    f"Falling back to isolated {BROWSER_KEY.title()} context..."
                )
                browser, context, page = await _launch_standalone_browser_context(p, headless=headless)
                launched_standalone = True

            if not await _ensure_usable_account(page):
                print("   ❌ Browser generation aborted: no usable Gemini account/session available.")
                return None

            if not launched_standalone and _page_is_closed(page):
                print("   ⚠️ BY browser tab closed during session prep. Opening a fresh tab...")
                context, page = await _get_or_create_gemini_page(browser, preferred_authuser=_account_index)

            # 2. Logic
            current_authuser = _extract_authuser(page.url or "")
            if "gemini.google.com" not in page.url or current_authuser != _account_index:
                print(f"📍 Navigating to Gemini (authuser={_account_index})...")
                await page.goto(_get_gemini_url())
            else:
                # Reload only if needed or keep existing state
                pass
            
            # Login Check
            await _wait_for_page_ready(page, timeout=6)
            
            # Ensure Clean State (New Chat)
            # User request: "Creat new conversation, old context affects flow"
            if "gemini.google.com" in page.url:
                 # Force Navigation to root to clear specific chat session ID
                 try:
                     await page.goto(_get_gemini_url())
                     await _wait_for_page_ready(page, timeout=8)
                     
                     # Force Remove Clean (Modals/Overlays)
                     # Lightboxes often intercept clicks. We NUKE them.
                     await page.evaluate("""() => {
                        document.querySelectorAll('.cdk-overlay-container').forEach(e => e.remove());
                        document.querySelectorAll('.cdk-live-announcer-element').forEach(e => e.remove());
                     }""")
                     
                     # 1. Try "New Chat" button (just in case)
                     new_chat_btns = [
                        'div[role="button"] span:has-text("New chat")',
                        'div[role="button"] span:has-text("新建对话")',
                        'button[aria-label="New chat"]', 
                        '.new-conversation-button'
                     ]
                     
                     for sel in new_chat_btns:
                         btn = page.locator(sel).first
                         if await btn.count() > 0 and await btn.is_visible():
                             await btn.click()
                             await _wait_for_page_ready(page, timeout=5)
                             break

                 except Exception as e:
                     print(f"   ⚠️ Error starting new chat: {e}")
            
            if "accounts.google.com" in page.url:
                _mark_account_state(
                    _account_index,
                    valid=False,
                    status="invalid_login",
                    cooldown_until=None,
                    reason="Gemini login required",
                )
                print("\n🔵 ACTION REQUIRED: Current Gemini account is not logged in.")
                if not await _ensure_usable_account(page):
                    return None
                await page.goto(_get_gemini_url())
                await _wait_for_page_ready(page, timeout=8)

            # Popups
            try:
                dismiss_selectors = ['button[aria-label="Close"]', 'button:has-text("Close")', 'button:has-text("No thanks")']
                for sel in dismiss_selectors:
                    if await page.locator(sel).count() > 0:
                        await page.locator(sel).first.click()
            except: pass

            # Best-effort: switch into the image-focused Gemini workflow if the page exposes it.
            await _ensure_image_generation_mode(page)

            async def _ensure_model_ready() -> None:
                last_error = None
                max_account_attempts = max(_accounts_count, 1)
                for _ in range(max_account_attempts):
                    try:
                        await _select_model(page, model)
                        return
                    except ValueError as exc:
                        last_error = exc
                        if not str(exc).startswith("MODEL_UNAVAILABLE:"):
                            raise
                        print(f"   ⚠️ {exc}")
                        if not rotate_gemini_account():
                            break
                        print(f"   🔄 Retrying Gemini image workflow with authuser={_account_index}...")
                        await page.goto(_get_gemini_url())
                        await _wait_for_page_ready(page, timeout=8)
                        await _ensure_image_generation_mode(page)

                if last_error is not None:
                    raise last_error

            # Model
            await _ensure_model_ready()

            # 3. Execution with Retries (+ Account Rotation on limit/garbled)
            max_retries = 3
            final_path = None
            terminal_result = None
            
            for attempt in range(1, max_retries + 1):
                result = await _perform_generation_attempt(page, prompt, input_image_path, output_path, model, attempt)
                
                if result == "ACCOUNT_LIMIT":
                    terminal_result = result
                    print(f"   🛑 Account {_account_index} hit limit.")
                    if rotate_gemini_account():
                        print(f"   🔄 Will retry with next account after reconnect...")
                        await page.goto(_get_gemini_url())
                        await _wait_for_page_ready(page, timeout=8)
                        await _ensure_image_generation_mode(page)
                        await _ensure_model_ready()
                        continue
                    else:
                        print("   ❌ No other accounts available.")
                        final_path = None
                        break

                if result == "ACCOUNT_LOGIN_REQUIRED":
                    terminal_result = result
                    print(f"   🛑 Account {_account_index} login is invalid.")
                    if rotate_gemini_account():
                        print(f"   🔄 Retrying with another logged-in account...")
                        await page.goto(_get_gemini_url())
                        await _wait_for_page_ready(page, timeout=8)
                        await _ensure_image_generation_mode(page)
                        await _ensure_model_ready()
                        continue
                    if not await _ensure_usable_account(page):
                        print("   ❌ Browser generation aborted after login check: still no usable Gemini account.")
                        final_path = None
                        break
                    await page.goto(_get_gemini_url())
                    await _wait_for_page_ready(page, timeout=8)
                    await _ensure_image_generation_mode(page)
                    await _ensure_model_ready()
                    continue

                if result == "TEXT_ONLY_RESPONSE":
                    terminal_result = result
                    print(f"   ⚠️ Gemini returned text instead of an image.")
                    if attempt < max_retries:
                        print("   🔄 Resetting Gemini chat and retrying image generation...")
                        await page.goto(_get_gemini_url())
                        await _wait_for_page_ready(page, timeout=8)
                        await _ensure_image_generation_mode(page)
                        await _ensure_model_ready()
                        continue
                    final_path = None
                    break

                if result == "REQUEST_FAILED":
                    terminal_result = result
                    print("   ⚠️ Gemini UI reported an explicit image-edit failure.")
                    if attempt < max_retries:
                        print("   🔄 Resetting Gemini chat and retrying after failure page...")
                        await page.goto(_get_gemini_url())
                        await _wait_for_page_ready(page, timeout=8)
                        await _ensure_image_generation_mode(page)
                        await _ensure_model_ready()
                        continue
                    final_path = None
                    break

                if result == "SEND_NOT_STARTED":
                    terminal_result = result
                    print("   ⚠️ Gemini composer never dispatched the prompt.")
                    if attempt < max_retries:
                        print("   🔄 Resetting Gemini chat and retrying send flow...")
                        await page.goto(_get_gemini_url())
                        await _wait_for_page_ready(page, timeout=8)
                        await _ensure_image_generation_mode(page)
                        await _ensure_model_ready()
                        continue
                    final_path = None
                    break
                
                if result and result != "GARBLED":
                    _mark_account_state(
                        _account_index,
                        valid=True,
                        status="available",
                        cooldown_until=None,
                        reason=None,
                    )
                    final_path = result
                    break
                elif result == "GARBLED":
                    terminal_result = result
                    if rotate_gemini_account():
                        print(f"   🔄 Garbled text → rotating account. Will retry with authuser={_account_index}...")
                        # Reload with new account URL
                        await page.goto(_get_gemini_url())
                        await _wait_for_page_ready(page, timeout=8)
                        await _ensure_image_generation_mode(page)
                        await _ensure_model_ready()
                    elif attempt < max_retries:
                        print(f"   🔄 Attempt {attempt} garbled, retrying same account...")
                        await page.goto(_get_gemini_url())
                        await _wait_for_page_ready(page, timeout=8)
                        await _ensure_image_generation_mode(page)
                        await _ensure_model_ready()
                    else:
                        print(f"   ⚠️ All {max_retries} attempts failed. Keeping last result.")
                        final_path = output_path
                else:
                    if attempt < max_retries:
                        print(f"   ⚠️ Attempt {attempt} produced no image artifact. Resetting chat and retrying...")
                        await page.goto(_get_gemini_url())
                        await _wait_for_page_ready(page, timeout=8)
                        await _ensure_image_generation_mode(page)
                        await _ensure_model_ready()
                        continue
                    print(f"   ⚠️ Attempt {attempt} failed completely.")
                    break

            if not final_path:
                print("   ❌ Browser generation finished without an image artifact.")
                if terminal_result:
                    return terminal_result
            return final_path
        except Exception as e:
            print(f"❌ Browser Error: {e}")
            return None
        finally:
            # Keep the detached CDP browser alive for reuse, but fully close standalone fallbacks.
            if browser and launched_standalone:
                try:
                    await browser.close()
                except:
                    pass

async def generate_text_browser(
    prompt: str,
    model: str = "Flash",
    headless: bool = False
) -> Optional[str]:
    """
    Generate text using Gemini Web via Playwright (CDP Mode).
    """
    print(f"🌐 Browser Automation: Generating Text (CDP Mode)")
    
    async with async_playwright() as p:
        browser = None
        launched_standalone = False
        try:
            # 1. Connect or Launch (Reuse logic)
            try:
                browser = await _connect_configured_browser(p)
            except:
                await asyncio.sleep(2)
                try:
                    browser = await _connect_configured_browser(p)
                except Exception:
                    browser, context, page = await _launch_standalone_browser_context(p, headless=headless)
                    launched_standalone = True

            if not launched_standalone:
                context, page = await _get_or_create_gemini_page(browser, preferred_authuser=_account_index)

            if not await _ensure_usable_account(page):
                print("   ❌ Browser text generation aborted: no usable Gemini account/session available.")
                return None

            if "gemini.google.com" not in page.url:
                await page.goto(_get_gemini_url())
                await _wait_for_page_ready(page, timeout=8)

            # Ensure new chat if needed or just type
            # For translation, we can just type in existing chat, but new chat is cleaner
            # Simplify: Just type.
            await _select_model(page, model)
            
            try:
                await page.click("body")
            except Exception:
                pass
            input_box = await _wait_for_visible(page, _PROMPT_INPUT_SELECTORS, timeout_seconds=8.0)
            
            retry = 0
            while not input_box and retry < 5:
                await asyncio.sleep(1)
                retry += 1
                input_box = await _wait_for_visible(page, _PROMPT_INPUT_SELECTORS, timeout_seconds=2.0)
                
            if not input_box:
                log.error("❌ Input box not found.")
                return None
                
            prompt_ok = await _focus_and_fill_prompt_input(page, input_box, prompt)
            if not prompt_ok:
                log.error("❌ Prompt text did not stick in Gemini text input.")
                return None
            await page.wait_for_timeout(500)

            send_success = False
            initial_response_count = 0
            initial_user_query_count = 0
            initial_prompt_input_text = await _read_prompt_input_text(input_box)

            try:
                initial_response_count = await page.locator('model-response, .model-response').count()
            except Exception:
                pass
            try:
                initial_user_query_count = await page.locator(
                    'user-query, user-message, .user-message, .user-prompt, [data-test-id="message-user"]'
                ).count()
            except Exception:
                pass

            async def _text_conversation_started() -> bool:
                try:
                    current_user_queries = await page.locator(
                        'user-query, user-message, .user-message, .user-prompt, [data-test-id="message-user"]'
                    ).count()
                    if current_user_queries > initial_user_query_count:
                        return True
                except Exception:
                    pass
                try:
                    if await page.locator('model-response, .model-response').count() > initial_response_count:
                        return True
                except Exception:
                    pass
                try:
                    if await page.locator(
                        'mat-progress-spinner, [role="progressbar"], button[aria-label*="Stop"], button[aria-label*="停止"], [aria-label*="思考"], [aria-label*="thinking"]'
                    ).count() > 0:
                        return True
                except Exception:
                    pass
                try:
                    latest_input_box = await _find_last_visible(page, _PROMPT_INPUT_SELECTORS) or input_box
                    current_prompt_text = await _read_prompt_input_text(latest_input_box)
                    if _prompt_dispatch_likely_started(initial_prompt_input_text, current_prompt_text):
                        return True
                except Exception:
                    pass
                return False

            async def _try_send_attempt(label: str, action) -> bool:
                try:
                    await action()
                    deadline = time.time() + 4.5
                    while time.time() < deadline:
                        if await _text_conversation_started():
                            print(f"   ✅ Text send triggered via {label}.")
                            return True
                        await asyncio.sleep(0.45)
                except Exception as exc:
                    print(f"   ⚠️ Text send attempt failed via {label}: {exc}")
                return False

            wait_deadline = time.time() + 5
            while time.time() < wait_deadline:
                try:
                    input_box = await _find_last_visible(page, _PROMPT_INPUT_SELECTORS) or input_box
                    send_btn = await _find_last_visible(page, _SEND_BUTTON_SELECTORS)
                    send_visible = bool(send_btn and await send_btn.is_visible())
                except Exception:
                    send_btn = None
                    send_visible = False

                send_actions = []
                if send_visible and send_btn:
                    send_actions.extend(
                        [
                            ("send button click", lambda: _scroll_and_click(page, send_btn)),
                            ("send button js click", lambda: send_btn.evaluate("(el) => el.click()")),
                        ]
                    )

                send_actions.extend(
                    [
                        ("Enter", lambda: _send_via_key(input_box, "Enter")),
                        ("Control+Enter", lambda: _send_via_key(input_box, "Control+Enter")),
                        ("Meta+Enter", lambda: _send_via_key(input_box, "Meta+Enter")),
                    ]
                )

                for label, action in send_actions:
                    if await _try_send_attempt(label, action):
                        send_success = True
                        break

                if send_success:
                    break

                print("   ⏳ Waiting for Gemini text send action to start a conversation...")
                await asyncio.sleep(1)

            if not send_success:
                log.error("❌ Browser text generation never dispatched the prompt.")
                return None
            
            # Wait for response
            print("⏳ Waiting for text generation...")
            # We wait for the "model-response" to appear and stabilize
            await asyncio.sleep(2) 
            
            # Polling for "Regenerate" button or "Copy" icon usually indicates done.
            # Or check for stopping of streaming.
            # Simple heuristic: Wait for the LAST model response container to contain text and be stable.
            
            start_time = time.time()
            last_text = ""
            stable_count = 0
            
            while time.time() - start_time < 60:
                # Get the last model response text
                responses = page.locator('model-response, .model-response')
                count = await responses.count()
                if count > 0:
                    last_response = responses.last
                    text = await last_response.inner_text()
                    
                    if text and text == last_text:
                        stable_count += 1
                        if stable_count > 2: # Stable for ~1.0s
                            # Perform cleanup of the text (remove citations, UI elements)
                            # Strip Gemini web UI conversational prefixes
                            cleaned = text.strip()
                            for prefix in ["Gemini 说", "Gemini says", "Gemini wrote", "Gemini：", "Gemini: "]:
                                if cleaned.startswith(prefix):
                                    cleaned = cleaned[len(prefix):].strip()
                                    break
                            return cleaned
                    else:
                        last_text = text
                        stable_count = 0
                
                await asyncio.sleep(0.5)
                print(".", end="", flush=True)

            # Strip Gemini web UI conversational prefixes on timeout path too
            if last_text:
                for prefix in ["Gemini 说", "Gemini says", "Gemini wrote", "Gemini：", "Gemini: "]:
                    if last_text.startswith(prefix):
                        last_text = last_text[len(prefix):].strip()
                        break
            # Check for account limit on empty/long timeout
            if not last_text.strip():
                try:
                    page_text = await page.evaluate("document.body.innerText || ''")
                    limit_indicators = ["达到上限", "limit reached", "用量限额", "can't generate more", "明天再试", "check back tomorrow", "can create more images", "limit resets", "check your usage", "as soon as your limit", "usage in settings"]
                    if any(indicator in page_text for indicator in limit_indicators):
                        print(f"\n   🛑 Account {_account_index} hit limit during text gen.")
                        rotate_gemini_account()
                except Exception:
                    pass
            return last_text.strip()
            
        except Exception as e:
            log.error(f"❌ Browser Text Gen Error: {e}")
            return None
        finally:
            if browser and launched_standalone:
                try:
                    await browser.close()
                except:
                    pass
            
            # Process cleanup handled by atexit

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(generate_image_browser("Test Prompt", "video_cover.png", "output.png"))

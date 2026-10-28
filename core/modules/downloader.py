#!/usr/bin/env python3
"""
使用 yt-dlp 下载 YouTube 视频、封面、字幕和元数据
"""
import os
import sys
import json
import time
import glob
import shutil
import urllib.request
import re
from typing import Optional
import yt_dlp
from http.cookiejar import MozillaCookieJar
from functools import lru_cache
from importlib import metadata
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from core.utils.config import load_config
from core.utils.i18n import print_t
from core.utils.video_utils import verify_video_integrity, repair_video, file_has_video_stream
# from core.modules.browser_downloader import sniff_video_info, get_youtube_cookies_via_browser, convert_cookies_to_netscape
import tempfile
from core.utils.cookie import resolve_browser_app_info
from core.utils.url_utils import parse_youtube_video_id

# Load config
CONFIG = load_config()
VERBOSE_CONSOLE_LOGS = bool(CONFIG.get("verbose_console_logs", False))
MIN_YT_DLP_VERSION = (2026, 3, 17)
MIN_YT_DLP_VERSION_TEXT = "2026.03.17"
YT_DLP_UPGRADE_HINT = "python3 -m pip install --break-system-packages --user -U 'yt-dlp[default]==2026.03.17'"

def _resolve_browser_cookie_profile(browser: Optional[str] = None) -> tuple[str, Optional[str]]:
    """
    Resolve the effective browser key and profile path used for cookie extraction.
    """
    browser_key, _, configured_profile = resolve_browser_app_info(browser=browser or CONFIG.get("browser"))
    if configured_profile:
        return browser_key, configured_profile

    if sys.platform == 'darwin':
        default_profiles = {
            'chrome': os.path.expanduser("~/Library/Application Support/Google/Chrome/Default"),
            'chromium': os.path.expanduser("~/Library/Application Support/Chromium/Default"),
            'edge': os.path.expanduser("~/Library/Application Support/Microsoft Edge/Default"),
            'brave': os.path.expanduser("~/Library/Application Support/BraveSoftware/Brave-Browser/Default"),
            'opera': os.path.expanduser("~/Library/Application Support/com.operasoftware.Opera"),
        }
        return browser_key, default_profiles.get(browser_key)

    return browser_key, None


def _console(msg: str):
    print(msg)


def _verbose(msg: str):
    if VERBOSE_CONSOLE_LOGS:
        print(msg)


def _parse_calendar_version(version_text: Optional[str]) -> tuple[int, int, int]:
    parts: list[int] = []
    for chunk in str(version_text or "").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits or "0"))
        if len(parts) == 3:
            break
    return tuple((parts + [0, 0, 0])[:3])


@lru_cache(maxsize=1)
def get_yt_dlp_runtime_status() -> dict[str, object]:
    version_text = getattr(getattr(yt_dlp, "version", None), "__version__", None) or getattr(yt_dlp, "__version__", "0")
    try:
        ejs_version = metadata.version("yt-dlp-ejs")
    except metadata.PackageNotFoundError:
        ejs_version = None

    return {
        "version": version_text,
        "version_tuple": _parse_calendar_version(version_text),
        "version_ok": _parse_calendar_version(version_text) >= MIN_YT_DLP_VERSION,
        "ejs_version": ejs_version,
        "deno_path": shutil.which("deno"),
    }


def warn_if_ytdlp_runtime_is_outdated() -> dict[str, object]:
    status = get_yt_dlp_runtime_status()

    if not status["version_ok"]:
        msg = (
            f"⚠️ 当前 yt-dlp 版本是 {status['version']}，低于已验证可用的 {MIN_YT_DLP_VERSION_TEXT}。"
            f" 这类版本在 YouTube 上容易触发 'The page needs to be reloaded' / 403。"
            f" 升级命令: {YT_DLP_UPGRADE_HINT}"
        )
        print_t('warning', msg)
        _console(msg)
    elif not status["deno_path"]:
        msg = "⚠️ 未检测到 deno。YouTube 的 JS challenge 可能无法被 yt-dlp 正常处理。"
        print_t('warning', msg)
        _console(msg)
    elif not status["ejs_version"]:
        _verbose("ℹ️ 未检测到本地 yt-dlp-ejs，将依赖 remote_components=ejs:github 在线拉取 EJS 组件。")

    return status

class YdlLogger:
    """Redirect yt-dlp logs to print_t for thread-safe/buffered output"""
    _SHOW_KEYWORDS = (
        "Merging formats into",
        "has already been downloaded",
        "Deleting original file",
    )

    _SUPPRESS_KEYWORDS = (
        "Downloading webpage",
        "Downloading android",
        "Downloading tv ",
        "Downloading web player API JSON",
        "Downloading web safari player API JSON",
        "Downloading m3u8 information",
        "Downloading player ",
        "Solving JS challenges",
        "Downloading challenge solver",
        "Extracting cookies from",
        "Extracted ",
        "Writing video subtitles to:",
        "Unable to download video subtitles for",
        "Downloading subtitles:",
        "Testing format ",
        "Downloading m3u8 manifest",
        "Total fragments:",
    )

    def _is_noisy_info(self, msg: str) -> bool:
        if VERBOSE_CONSOLE_LOGS:
            return False

        if msg.startswith("[download]"):
            # Keep dynamic progress line and final summary for media downloads.
            if "%" in msg and "ETA" in msg:
                return False
            if "100% of" in msg and ("MiB" in msg or "GiB" in msg):
                return False
            # Subtitle download lines are too noisy.
            if ".vtt" in msg or ".srt" in msg:
                return True
            # Keep only meaningful one-line status updates for media files.
            return not any(k in msg for k in ("Destination:", "has already been downloaded", "100% of"))

        # Keep only key yt-dlp state changes on concise mode.
        if any(k in msg for k in self._SHOW_KEYWORDS):
            return False
        if any(k in msg for k in self._SUPPRESS_KEYWORDS):
            return True

        # Default suppress for verbose extractor details.
        if msg.startswith("[youtube]") or msg.startswith("[info]"):
            return True
        return False

    def debug(self, msg):
        if msg.startswith('[debug] '):
             pass
        else:
             self.info(msg)
    def info(self, msg):
        if self._is_noisy_info(msg):
            return
        if msg.startswith('[download]') and '%' in msg and 'ETA' in msg:
            import sys
            sys.stdout.write(f"\r\033[K{msg}")
            sys.stdout.flush()
        else:
            print_t('info', msg)
    def warning(self, msg):
        print_t('warning', msg)
    def error(self, msg):
        print_t('error', msg)

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)


def get_cookiesfrombrowser_option(browser: str):
    """Prefer project custom profile when available."""
    browser_key, profile = _resolve_browser_cookie_profile(browser)
    if browser_key in ("chrome", "chromium", "edge", "brave", "opera", "vivaldi"):
        if profile and os.path.exists(profile):
            return (browser_key, profile)
    # ego: yt-dlp cannot read ego's keychain-encrypted cookie store; must use cookie file
    if browser_key == 'ego':
        return None
    return (browser_key,)


def apply_youtube_cookie_source(ydl_opts: dict, browser: str, cookie_file: str, announce: bool = False) -> Optional[str]:
    """
    Prefer live browser cookies over stale exported files.
    Returns the chosen source: "browser", "file", or None.
    """
    browser_cookie_opt = None
    try:
        browser_cookie_opt = get_cookiesfrombrowser_option(browser)
    except Exception:
        browser_cookie_opt = None

    if browser_cookie_opt:
        ydl_opts['cookiesfrombrowser'] = browser_cookie_opt
        ydl_opts.pop('cookiefile', None)
        if announce:
            _console(f"🍪 使用浏览器 Cookies: {browser_cookie_opt[0]}")
        return "browser"

    if os.path.exists(cookie_file):
        ydl_opts['cookiefile'] = cookie_file
        ydl_opts.pop('cookiesfrombrowser', None)
        if announce:
            _console(f"🍪 使用 cookies 文件: {cookie_file}")
        return "file"

    ydl_opts.pop('cookiesfrombrowser', None)
    ydl_opts.pop('cookiefile', None)
    return None

def convert_cookies_to_netscape(cookies_list: list) -> str:
    """Convert JSON cookies list to Netscape format string"""
    import time
    out = "# Netscape HTTP Cookie File\n# This file is generated by biyou\n\n"
    for c in cookies_list:
        domain = c.get('domain', '')
        # Basic Netscape format: domain, flag, path, secure, expiration, name, value
        flag = "TRUE" if domain.startswith('.') else "FALSE"
        path = c.get('path', '/')
        secure = "TRUE" if c.get('secure') else "FALSE"
        expiry_val = c.get('expiry')
        if expiry_val is None:
            expiry_val = time.time() + 3600*24*30 # Default 30 days
        expiry = str(int(expiry_val))
        name = c.get('name', '')
        value = c.get('value', '')
        out += f"{domain}\t{flag}\t{path}\t{secure}\t{expiry}\t{name}\t{value}\n"
    return out


def write_temp_cookie_file(cookies_list: list) -> str:
    """Write Netscape cookies to a temp file and return path."""
    if not cookies_list:
        return None
    netscape_content = convert_cookies_to_netscape(cookies_list)
    fd, temp_path = tempfile.mkstemp(suffix='.txt', text=True)
    with os.fdopen(fd, 'w') as f:
        f.write(netscape_content)
    return temp_path


def is_sabr_control_stream(url: str) -> bool:
    """Detect YouTube SABR control URL (not actual downloadable media)."""
    if not url:
        return False
    try:
        query = parse_qs(urlparse(url).query)
        has_sabr = query.get('sabr', ['0'])[-1] == '1' or 'sabr=1' in url
        has_mime = 'mime' in query or 'mime=' in url
        has_range = 'range' in query or 'range=' in url
        return has_sabr and not has_mime and not has_range
    except Exception:
        return ('sabr=1' in url) and ('mime=' not in url) and ('range=' not in url)


def with_youtube_no_autoplay(url: Optional[str]) -> Optional[str]:
    """Prevent manual-login helper from opening a watch page that blasts audio immediately."""
    if not url:
        return url
    try:
        parsed = urlparse(url)
        if not parsed.netloc or "youtube.com" not in parsed.netloc:
            return url
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["autoplay"] = ["0"]
        query["mute"] = ["1"]
        return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
    except Exception:
        return url

def refresh_youtube_cookies(cookie_file_path, browser: Optional[str] = None):
    """Attempt to refresh youtube_cookies.txt from configured browser/profile"""
    browser_key, profile = _resolve_browser_cookie_profile(browser)
    print_t('info', f"🔄 Attempting to refresh YouTube cookies from {browser_key.title()}...")
    try:
        if browser_key == 'ego':
            # ego cookie store uses its own keychain secret; yt-dlp cannot read it.
            # Extract via CDP (get_cookies_from_browser has an ego branch) instead.
            from core.utils.cookie import get_cookies_from_browser
            cj = get_cookies_from_browser('.youtube.com', 'ego')
            if not cj:
                print_t('error', "❌ No YouTube cookies found in ego (CDP extraction failed).")
                return False
            cookies_list = []
            for c in list(cj):
                cookies_list.append({
                    'domain': c.domain,
                    'name': c.name,
                    'value': c.value,
                    'path': c.path,
                    'secure': c.secure,
                    'expiry': c.expires,
                })
        else:
            from yt_dlp.cookies import extract_cookies_from_browser
            profile_arg = profile if profile and os.path.exists(profile) else None
            cj = extract_cookies_from_browser(browser_key, profile_arg)
            if not cj:
                 print_t('error', f"❌ No YouTube cookies found in {browser_key.title()}.")
                 return False

            # Convert to list of dicts
            cookies_list = []
            for c in list(cj):
                 cookies_list.append({
                     'domain': c.domain,
                     'name': c.name,
                     'value': c.value,
                     'path': c.path,
                     'secure': c.secure,
                     'expiry': c.expires
                 })
        
        netscape_content = convert_cookies_to_netscape(cookies_list)
        
        if len(netscape_content) > 100:
             ensure_dir(os.path.dirname(cookie_file_path))
             with open(cookie_file_path, 'w') as f:
                 f.write(netscape_content)
             print_t('success', f"✅ Refreshed cookies saved to {cookie_file_path}")
             return True
        return False
    except Exception as e:
        print_t('error', f"❌ Failed to refresh cookies from {browser_key.title()}: {e}")
        return False


def prompt_youtube_login(browser: str = "chrome", video_url: Optional[str] = None) -> bool:
    """
    Open the configured browser/profile for manual YouTube login and wait for confirmation.
    Returns True if we likely can retry, False if running non-interactively or user declined.
    """
    try:
        from core.utils.cookie import open_browser_for_login
        login_url = with_youtube_no_autoplay(video_url) or "https://www.youtube.com"
        print("      ❌ YouTube Cookie Invalid. Opening browser for login...")
        open_browser_for_login(login_url, browser)
        input("      Press [Enter] once you have logged in to YouTube to continue...")
        return True
    except Exception as e:
        print_t('error', f"❌ Failed to open browser for YouTube login: {e}")
        return False


def is_youtube_auth_error(error: Exception | str) -> bool:
    """Heuristic for YouTube auth / anti-bot errors that require manual login."""
    error_str = str(error)
    keywords = (
        "Sign in to confirm",
        "The page needs to be reloaded",
        "LOGIN_REQUIRED",
        "not a bot",
        "Cookie 已过期",
    )
    return any(k in error_str for k in keywords)


def should_try_browser_sniff(error: Exception | str) -> bool:
    """Reserve browser sniffing for anti-bot/auth/no-media failures, not every yt-dlp error."""
    error_str = str(error)
    fallback_keywords = (
        "HTTP Error 403",
        "LOGIN_REQUIRED",
        "Sign in to confirm",
        "The page needs to be reloaded",
        "not a bot",
        "yt-dlp returned no media info",
        "No downloadable media stream obtained",
        "Requested format is not available",
    )
    return is_youtube_auth_error(error) or any(k in error_str for k in fallback_keywords)

def sanitize_filename(name: str) -> str:
    """Sanitize filename to avoid directory creation or invalid chars"""
    if not name:
        return "Unknown"
    # Replace common invalid chars
    name = name.replace("/", "⧸").replace("\\", "⧹")
    name = name.replace(":", "：").replace("*", "＊")
    name = name.replace("?", "？").replace("\"", "＂")
    name = name.replace("<", "＜").replace(">", "＞").replace("|", "｜")
    return name.strip()


def _download_progressive_fallback_stream(
    progressive_stream_url: Optional[str],
    *,
    video_dir: str,
    title_hint: str,
    stream_headers_override: Optional[dict] = None,
    user_agent_override: Optional[str] = None,
    temp_cookie_path: Optional[str] = None,
) -> Optional[str]:
    """Download a known progressive stream directly instead of spending more time on blocked yt-dlp strategies."""
    if not progressive_stream_url:
        return None

    def _has_header(headers: dict[str, str], name: str) -> bool:
        lowered = name.lower()
        return any(k.lower() == lowered for k in headers)

    def _load_cookie_header(netscape_cookie_path: Optional[str]) -> Optional[str]:
        if not netscape_cookie_path or not os.path.exists(netscape_cookie_path):
            return None
        try:
            jar = MozillaCookieJar()
            jar.load(netscape_cookie_path, ignore_discard=True, ignore_expires=True)
            pairs = [f"{cookie.name}={cookie.value}" for cookie in jar]
            return "; ".join(pairs) if pairs else None
        except Exception:
            return None

    title = sanitize_filename(title_hint or "video")
    target_video_path = os.path.join(video_dir, f"{title}.mp4")
    if os.path.exists(target_video_path) and os.path.getsize(target_video_path) <= 1024:
        try:
            os.remove(target_video_path)
        except Exception:
            pass

    _console("🔄 使用页面解析到的渐进式视频流进行兜底下载...")

    direct_headers = dict(stream_headers_override or {})
    if not _has_header(direct_headers, "User-Agent"):
        direct_headers["User-Agent"] = user_agent_override or CONFIG.get("user_agent")
    if not _has_header(direct_headers, "Referer"):
        direct_headers["Referer"] = "https://www.youtube.com/"
    if not _has_header(direct_headers, "Cookie"):
        cookie_header = _load_cookie_header(temp_cookie_path)
        if cookie_header:
            direct_headers["Cookie"] = cookie_header

    try:
        request = urllib.request.Request(progressive_stream_url, headers=direct_headers)
        with urllib.request.urlopen(request, timeout=60) as response, open(target_video_path, "wb") as f:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)

        if os.path.exists(target_video_path) and os.path.getsize(target_video_path) > 1024:
            print_t('dl_complete', "Browser Progressive Stream")
            return target_video_path
    except Exception as e:
        _verbose(f"⚠️ 原始 HTTP 渐进式下载失败，回退 yt-dlp generic: {e}")

    try:
        ydl_opts_stream = {
            'outtmpl': os.path.join(video_dir, '%(title)s.%(ext)s'),
            'quiet': False,
            'no_warnings': True,
            'http_headers': stream_headers_override or {},
            'nocheckcertificate': True,
            'force_ipv4': True,
            'force_generic_extractor': True,
            'fixup': 'never',
            'overwrites': True,
        }
        if user_agent_override:
            ydl_opts_stream['user_agent'] = user_agent_override
        if temp_cookie_path and os.path.exists(temp_cookie_path):
            ydl_opts_stream['cookiefile'] = temp_cookie_path

        with yt_dlp.YoutubeDL(ydl_opts_stream) as ydl:
            ydl.params['outtmpl'] = {'default': target_video_path}
            ydl.download([progressive_stream_url])

        if os.path.exists(target_video_path) and os.path.getsize(target_video_path) > 1024:
            print_t('dl_complete', "Browser Progressive Stream")
            return target_video_path
    except Exception as e:
        _verbose(f"⚠️ 渐进式兜底下载失败: {e}")
    return None


def _find_local_info_by_video_id(video_dir: str, video_id: str) -> Optional[dict]:
    """Lookup local *.info.json cache by video id and return normalized info."""
    if not video_dir or not video_id or not os.path.isdir(video_dir):
        return None

    info_files = glob.glob(os.path.join(video_dir, "*.info.json"))
    if not info_files:
        return None

    # Recent files first improves hit speed for repeated runs.
    info_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)

    for info_path in info_files:
        try:
            with open(info_path, "r", encoding="utf-8") as f:
                info_dict = json.load(f)
        except Exception:
            continue

        if str(info_dict.get("id", "")).strip() != str(video_id):
            continue

        base_path = info_path[:-10]  # strip ".info.json"
        title = sanitize_filename(info_dict.get("title") or os.path.basename(base_path))
        description = info_dict.get("description", "")

        candidates = []
        ext = info_dict.get("ext")
        if ext:
            candidates.append(f"{base_path}.{ext}")
        candidates.extend([
            f"{base_path}.mp4",
            f"{base_path}.mkv",
            f"{base_path}.webm",
            f"{base_path}.m4v",
        ])

        seen = set()
        video_path = None
        for cand in candidates:
            if cand in seen:
                continue
            seen.add(cand)
            if os.path.exists(cand) and os.path.getsize(cand) > 1024:
                video_path = cand
                break

        if not video_path:
            # Keep a deterministic expected path for downstream checks.
            video_path = f"{base_path}.mp4"

        return {
            "title": title,
            "description": description,
            "video_path": video_path,
            "id": info_dict.get("id"),
            "tags": info_dict.get("tags", []),
            "info_dict": info_dict,
            "local_cache_hit": True,
            "local_info_path": info_path,
        }

    return None

def fetch_video_info(url: str):
    """
    Step 1: 仅获取视频元数据和封面 (快速)
    Returns: info dict or None
    """
    CONFIG = load_config()
    VIDEO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), CONFIG.get('video_source_dir', '../videos')))
    
    print_t('dl_start', url)
    print_t('dl_dir', VIDEO_DIR)
    warn_if_ytdlp_runtime_is_outdated()
    
    ensure_dir(VIDEO_DIR)

    # Fast path: if we already have local info cache for this YouTube ID, reuse it.
    video_id = parse_youtube_video_id(url)
    if video_id:
        cached = _find_local_info_by_video_id(VIDEO_DIR, video_id)
        if cached:
            cached_video_path = cached.get("video_path")
            if cached_video_path and os.path.exists(cached_video_path) and os.path.getsize(cached_video_path) > 1024:
                _console(f"⚡️ 命中本地缓存，跳过网页抓取: {video_id}")
                if cached.get("local_info_path"):
                    _verbose(f"   使用本地元数据: {cached['local_info_path']}")
                print_t('dl_meta_done', cached.get("title", "Unknown"))
                return cached
            _console(f"⚠️ 仅命中元数据缓存，实际视频缺失，继续探测下载状态: {video_id}")
    
    # yt-dlp 配置 - 仅获取信息和封面，不下载视频
    browser = CONFIG.get('browser', 'chrome')
    proxy = CONFIG.get('proxy')
    if not proxy:
        proxy = (CONFIG.get('services', {}) or {}).get('cover', {}).get('proxy')
    
    # Cookie 策略:
    # 1. 尝试使用手动 cookies.txt
    # 2. 如果失败/不存在，尝试 Playwright 获取新鲜 Cookies (浏览器模拟)
    # 3. 最后尝试 yt-dlp 原生从浏览器读取
    
    cookie_file = os.path.join(os.path.dirname(__file__), '../../config/youtube_cookies.txt')
    use_fresh_browser_cookies = False
    temp_cookie_path = None
    prompted_youtube_login = False
    last_error = None

    ydl_opts_info = {
        'skip_download': True,        # 关键: 不下载视频
        'writethumbnail': True,       # 下载封面
        'writeinfojson': True,        # 下载元数据
        'nooverwrites': True,         # 不覆盖现有的封面/json，避免重复下载
        'outtmpl': os.path.join(VIDEO_DIR, '%(title)s.%(ext)s'),
        'proxy': proxy,               # 显式加代理
        'nocheckcertificate': True,   # 忽略 SSL 证书错误 (常见于代理环境)
        'force_ipv4': True,           # Prefer IPv4 to avoid IPv6 TLS issues
        'quiet': True,  # Production mode
        'no_warnings': True,
        # Prefer the true best stream first; some YouTube videos only expose 480p/720p+
        # through HLS, and forcing MP4-first here collapses metadata selection to 360p.
        'format': 'bestvideo+bestaudio/best/bestvideo[ext=mp4]+bestaudio[ext=m4a]',
        'logger': YdlLogger(), # Redirect logs to print_t
        'retries': 0,
        'fragment_retries': 0,
        'socket_timeout': 30,
        'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
        # Do not force yt-dlp impersonation here: some curl_cffi builds report all
        # browser targets as unavailable on this machine, which aborts before download.
        'remote_components': ['ejs:github'],
        # 2026-08-20: YouTube SABR + PO Token 反爬升级，默认 tv_downgraded client
        # 报 "The page needs to be reloaded" / 只吐 SABR。改为依次 fallback：
        # web_embedded(不需 PO token，已实测可下载) → web_safari(HLS) → mweb(官方建议)
        'extractor_args': {
            'youtube': {
                'player_client': ['default', 'web_embedded', 'web_safari', 'mweb'],
            }
        },
    }
    
    # 步骤 A: 设置 Cookie 源
    # 步骤 A: 设置 Cookie 源
    # Priority 1: Auto-saved cookies (from previous sessions)
    auto_cookie_file = "youtube_cookies.json"
    if os.path.exists(auto_cookie_file):
        try:
             # Convert JSON to Netscape for yt-dlp if needed, or just pass as is if supported?
             # yt-dlp supports Netscape format natively. Our JSON needs conversion.
             # But for now, let's just Try Native Browser first as requested if no file.
             pass
        except:
             pass

    apply_youtube_cookie_source(ydl_opts_info, browser, cookie_file, announce=True)

    # Retry loop for Auth failures
    max_retries = 2
    for attempt in range(max_retries):
        try:
            with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
                info = ydl.extract_info(url, download=True) # download=True needed for thumb/json write
                
                # Clean up temp cookie file
                if temp_cookie_path and os.path.exists(temp_cookie_path):
                     os.remove(temp_cookie_path)

                # Construct expected video path for later use
                video_filename = ydl.prepare_filename(info)
                
                title = info.get('title', 'Unknown')
                # Sanitize title immediately
                title = sanitize_filename(title)
                info['title'] = title

                desc = info.get('description', '')
                
                print_t('dl_meta_done', title)
                
                return {
                    "title": title,
                    "description": desc,
                    "video_path": video_filename, 
                    "id": info.get('id'),
                    "tags": info.get('tags', []),
                    "info_dict": info,
                    "local_cache_hit": False,
                }
                
        except Exception as e:
            last_error = e
            is_auth_error = is_youtube_auth_error(e) or "HTTP Error 403" in str(e)
            sniff_possible = should_try_browser_sniff(e)
            
            if is_auth_error and attempt == 0:
                print_t('dl_fail', f"Auth/Bot Error detected: {e}")
                refreshed = refresh_youtube_cookies(cookie_file, browser)
                if refreshed:
                    print_t('info', "🔄 Retrying with fresh cookies...")
                    apply_youtube_cookie_source(ydl_opts_info, browser, cookie_file)
                    continue
                if sniff_possible:
                    continue
                if not prompted_youtube_login and prompt_youtube_login(browser, url):
                    prompted_youtube_login = True
                    if refresh_youtube_cookies(cookie_file, browser):
                        print_t('info', "🔄 Retrying with freshly logged-in YouTube cookies...")
                        apply_youtube_cookie_source(ydl_opts_info, browser, cookie_file)
                        continue
            
            # If not retrying or retry failed
            if is_auth_error and not sniff_possible and not prompted_youtube_login and prompt_youtube_login(browser, url):
                prompted_youtube_login = True
                if refresh_youtube_cookies(cookie_file, browser):
                    print_t('info', "🔄 Retrying with freshly logged-in YouTube cookies...")
                    apply_youtube_cookie_source(ydl_opts_info, browser, cookie_file)
                    continue
            print_t('dl_fail', e)
            
            # Only fall back to sniffing on final attempt
            if attempt == max_retries - 1 and sniff_possible:
                _console("🔄 正在尝试浏览器嗅探兜底策略（元数据 + 直链）...")
                
                from core.modules.browser_downloader import sniff_video_info
                
                sniffed = sniff_video_info(url, cookie_file)
                if sniffed and (sniffed.get('url') or sniffed.get('ua') or sniffed.get('cookies')):
                    _console(f"✅ 浏览器嗅探成功（{'直链' if sniffed.get('url') else '元数据/凭证'}）")
                    stream_override = sniffed.get('url')
                    if sniffed.get('is_sabr_control'):
                        stream_override = None
                    
                    # Construct a yt-dlp compatible info dict
                    return {
                        "title": sniffed.get('title', "Browser_Sniffed_Video_" + str(int(time.time()))),
                        "description": sniffed.get('description', "Downloaded via Browser Sniffing"),
                        "thumbnail": sniffed.get('thumbnail'), # URL or None
                        "video_path": os.path.join(VIDEO_DIR, f"{sniffed.get('title', 'video')}.mp4"), # Temporary path
                        "id": sniffed.get('id', "browser_sniff"),
                        "ext": "mp4", 
                        "tags": [],
                        "stream_url_override": stream_override,
                        "progressive_stream_fallback_url": sniffed.get('progressive_stream_url'),
                        "http_headers_override": sniffed.get('headers'),
                        "user_agent_override": sniffed.get('ua'),
                        "cookies_override": sniffed.get('cookies', []),
                        "visitor_data_override": sniffed.get('visitor_data'),
                        "playability_status": sniffed.get('playability_status'),
                        "playability_reason": sniffed.get('playability_reason'),
                        "requires_login": sniffed.get('requires_login', False),
                    }
                return None
            
            # If intermediate failure (non-auth), we might want to break or continue? 
            # Usually strict yt-dlp error means we should break unless it's transient. 
            # But here we handle auth specifically. For other errors, we likely break to sniffing.
            break # Break loop so the controlled fallback decision below can run.
            # actually if we break here, we exit the loop, and return None because the loop finishes without return.
            # We need to ensure we hit the sniffing logic.
            # Let's just pass and let the loop finish? 
            # With 'break', we exit loop. checking `if attempt == max_retries - 1` logic needs care.
            
            # Correct logic:
            # If we are NOT continuing (failed auth refresh or just random error)
            # We want to fall back to Sniffing.
            pass 
    
    # Final Fallback outside loop if all attempts failed (and didn't return)
    # Actually, the 'if attempt == max_retries - 1' check inside loop is fine IF we run all iterations.
    # But if we 'break', we miss it.
    
    # Let's simplify: moving sniffing logic to after loop.
    
    if last_error and should_try_browser_sniff(last_error):
        _console("🔄 正在尝试浏览器嗅探兜底策略（元数据 + 直链）...")
        from core.modules.browser_downloader import sniff_video_info

        sniffed = sniff_video_info(url, cookie_file)
        if sniffed and (sniffed.get('url') or sniffed.get('ua') or sniffed.get('cookies')):
             stream_override = sniffed.get('url')
             if sniffed.get('is_sabr_control'):
                 stream_override = None
             # ... same logic ...
             return {
                "title": sniffed.get('title', "Browser_Sniffed_Video_" + str(int(time.time()))),
                "description": sniffed.get('description', "Downloaded via Browser Sniffing"),
                "thumbnail": sniffed.get('thumbnail'), 
                "video_path": os.path.join(VIDEO_DIR, f"{sniffed.get('title', 'video')}.mp4"), 
                "id": sniffed.get('id', "browser_sniff"),
                "ext": "mp4", 
                "tags": [],
                "stream_url_override": stream_override,
                "progressive_stream_fallback_url": sniffed.get('progressive_stream_url'),
                "http_headers_override": sniffed.get('headers'),
                "user_agent_override": sniffed.get('ua'),
                "cookies_override": sniffed.get('cookies', []),
                "visitor_data_override": sniffed.get('visitor_data'),
                "playability_status": sniffed.get('playability_status'),
                "playability_reason": sniffed.get('playability_reason'),
                "requires_login": sniffed.get('requires_login', False),
            }
    return None


def download_video_stream(url: str, info: dict = None):
    """
    Step 2: 下载实际视频文件和字幕 (耗时)
    Returns: video_path or None
    """
    info = info or {} # Safety init
    CONFIG = load_config()
    VIDEO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), CONFIG.get('video_source_dir', '../videos')))
    
    ensure_dir(VIDEO_DIR)
    browser = CONFIG.get('browser', 'chrome')
    warn_if_ytdlp_runtime_is_outdated()

    # 清理残留的 .part 文件
    cleanup_temp_files()

    # Also clear stale tiny output files for the current title.
    title_hint = sanitize_filename((info or {}).get('title', '')) if info else ''
    if title_hint:
        stale_patterns = [
            os.path.join(VIDEO_DIR, f"{title_hint}.mp4"),
            os.path.join(VIDEO_DIR, f"{title_hint}.webm"),
            os.path.join(VIDEO_DIR, f"{title_hint}.mkv"),
            os.path.join(VIDEO_DIR, f"{title_hint}.f*.mp4"),
            os.path.join(VIDEO_DIR, f"{title_hint}.f*.webm"),
            os.path.join(VIDEO_DIR, f"{title_hint}.f*.m4a"),
        ]
        for pattern in stale_patterns:
            for stale_file in glob.glob(pattern):
                try:
                    if os.path.exists(stale_file) and os.path.getsize(stale_file) <= 256 * 1024:
                        os.remove(stale_file)
                        _verbose(f"🧹 已清理残留小文件: {os.path.basename(stale_file)}")
                except Exception:
                    pass

    # Define fallback strategies with i18n support
    strategies = [
        {
            'name': 'Best Available',
            'format': 'bestvideo+bestaudio/best',
            'i18n_key': 'dl_strat_best_any'
        },
        {
            'name': 'Best Quality (MP4)',
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'i18n_key': 'dl_strat_best_mp4'
        },
        {
            'name': 'Compatibility',
            'format': 'best',
            'i18n_key': 'dl_strat_compat'
        }
    ]

    last_error = None
    prompted_youtube_login = False

    # Cookie 策略 for Video Download (Mirror logic)
    cookie_file = os.path.join(os.path.dirname(__file__), '../../config/youtube_cookies.txt')
    auto_cookie_file = "youtube_cookies.json"
    temp_cookie_path = None
    cookies_override = info.get('cookies_override') or []
    
    # Priority: Browser-exported cookies from sniffing fallback
    if cookies_override:
        try:
            temp_cookie_path = write_temp_cookie_file(cookies_override)
            if temp_cookie_path:
                _console(f"🍪 使用浏览器导出的 Cookies（{len(cookies_override)} 条）")
        except Exception as e:
            print_t('warning', f"Failed to write browser-exported cookies: {e}")

    # Priority: Auto-saved cookies
    if not temp_cookie_path and os.path.exists(auto_cookie_file):
        try:
             # with open(auto_cookie_file, 'r') as f:
             #     cookies_json = json.load(f)
             # netscape_content = convert_cookies_to_netscape(cookies_json)
             # fd, temp_cookie_path = tempfile.mkstemp(suffix='.txt', text=True)
             # with os.fdopen(fd, 'w') as f:
             #     f.write(netscape_content)
             # print_t(f"Using auto-saved cookies for download from {auto_cookie_file}")
             pass
        except Exception as e:
             _verbose(f"⚠️ 使用自动保存 Cookies 失败: {e}")

    # Fallback to manual file if auto failed or missed
    if not temp_cookie_path and os.path.exists(cookie_file):
         # ... existing refresh logic logic removed for brevity/stability, just use file ...
         pass

    # Check if we have a pre-sniffed stream from Step 1
    stream_url_override = info.get('stream_url_override')
    progressive_stream_fallback_url = info.get('progressive_stream_fallback_url')
    stream_headers_override = info.get('http_headers_override')
    user_agent_override = info.get('user_agent_override')
    visitor_data_override = info.get('visitor_data_override')

    # Common override opts
    common_opts_override = {}
    if user_agent_override:
        common_opts_override['user_agent'] = user_agent_override
    if visitor_data_override:
        common_opts_override['extractor_args'] = {
            'youtube': {
                'visitor_data': [visitor_data_override],
                # 2026-08-20: 原 'web' 只回 SABR 格式，改 fallback 链
                'player_client': ['default', 'web_embedded', 'web_safari', 'mweb'],
                'formats': ['missing_pot'],
            }
        }
    if temp_cookie_path:
        common_opts_override['cookiefile'] = temp_cookie_path
    elif not apply_youtube_cookie_source(common_opts_override, browser, cookie_file):
        common_opts_override.pop('cookiefile', None)

    if info.get('requires_login'):
        reason = info.get('playability_reason') or info.get('playability_status') or "YouTube requires a signed-in browser session."
        print_t('dl_fail', f"YouTube login required: {reason}")
        return None

    progressive_fallback_attempted = False
    if progressive_stream_fallback_url and not stream_url_override:
        progressive_fallback_attempted = True
        direct_progressive_path = _download_progressive_fallback_stream(
            progressive_stream_fallback_url,
            video_dir=VIDEO_DIR,
            title_hint=info.get('title') or info.get('id') or 'video',
            stream_headers_override=stream_headers_override,
            user_agent_override=user_agent_override,
            temp_cookie_path=temp_cookie_path,
        )
        if direct_progressive_path:
            return direct_progressive_path

    
    # If we have an override, we prioritize a "Direct Stream" download (or just generic with new cookies+UA)
    if stream_url_override:
        if is_sabr_control_stream(stream_url_override):
            print_t('warning', "Detected SABR control stream URL. Skip direct stream mode and use authenticated yt-dlp fallback.")
            stream_url_override = None
        else:
            _console("🚀 使用浏览器嗅探直链进行下载（或仅复用请求头）...")
            try:
                 # Use the UA captured from browser if available
                 user_agent_override = info.get('user_agent_override')
                 
                 # 1. Save Cover Image (if available from sniff info)
                 thumb_url = info.get('thumbnail')
                 if thumb_url:
                     try:
                         _verbose("   正在保存嗅探到的封面...")
                         # Use curl or requests to download cover
                         # info['title'] is safe filename base
                         thumb_path = os.path.join(VIDEO_DIR, f"{info['title']}.jpg") # Assume jpg/webp
                         # Simple download ref
                         cmd = f"curl -s -L '{thumb_url}' -o '{thumb_path}'"
                         os.system(cmd)
                     except Exception as e:
                         _verbose("   ⚠️ 保存嗅探封面失败: " + str(e))
                 
                 ydl_opts_stream = {
                    'outtmpl': os.path.join(VIDEO_DIR, '%(title)s.%(ext)s'),
                    'quiet': False, # Show progress for stream download
                    'no_warnings': True,
                    'http_headers': stream_headers_override or {},
                    'nocheckcertificate': True,
                    'force_ipv4': True,
                    # Key fix: Don't let yt-dlp try to extract info from the googlevideo URL (which fails).
                    # Just treat it as a direct file/stream.
                    'force_generic_extractor': True, 
                    'fixup': 'never', # Skip ffmpeg fixup if not needed, speeds up testing
                    'overwrites': True,
                 }
                 if user_agent_override:
                     ydl_opts_stream['user_agent'] = user_agent_override
                 if temp_cookie_path:
                     ydl_opts_stream['cookiefile'] = temp_cookie_path

                 with yt_dlp.YoutubeDL(ydl_opts_stream) as ydl:
                    # IMPORTANT: We must manually set the filename because direct URL download 
                    # often results in 'videoplayback.mp4' or similar.
                    # structure: outtmpl is used, but we need to ensure 'title' is in the context.
                    
                    # We can use the 'ydl.prepare_filename' trick?
                    # Or just rely on passing the 'info_dict' to download?
                    # download() takes a list of URLs.
                    
                    # Better approach:
                    # Use a custom Context dict?
                    # Actually, simply setting 'outtmpl' to the exact filename avoids template/extraction issues.
                    title = sanitize_filename(info['title'])
                    target_video_path = os.path.join(VIDEO_DIR, f"{title}.mp4")

                    # A previous failed run may leave a tiny file that makes yt-dlp think it's already downloaded.
                    if os.path.exists(target_video_path) and os.path.getsize(target_video_path) <= 1024:
                        try:
                            os.remove(target_video_path)
                            _verbose(f"🧹 重试前清理残留小文件: {os.path.basename(target_video_path)}")
                        except Exception:
                            pass

                    ydl.params['outtmpl'] = {'default': target_video_path}
                    
                    _verbose(f"   🚀 开始直链下载: {target_video_path}")
                    ydl.download([stream_url_override])
                    
                    # Verify
                    if os.path.exists(target_video_path) and os.path.getsize(target_video_path) > 1024:
                         print_t('dl_complete', "Browser Stream")
                         return target_video_path
                    else:
                         print_t('dl_fail', "Stream download resulted in empty/small file.")
                         
            except Exception as e:
                print_t('dl_fail', f"Stream download failed: {e}")
                # Continue to normal yt-dlp strategy loop with exported cookies

    last_error = None
    
    # Normal Strategies Loop
    for strategy in strategies:
        try:
             # Logic Hook: If we identified this needs browser sniffing (from previous failures or just trying)
             # But 'strategies' dictates formats. 
             # Let's keep existing logic, then add a "Sniff Strategy" at the end if all fail?
             # Or wrap the inner block.
             
            if strategy['name'] != strategies[0]['name']:
                print_t(strategy['i18n_key'])
            else:
                 print_t('dl_bg_start', url)

            proxy = CONFIG.get('proxy')
            if not proxy:
                proxy = (CONFIG.get('services', {}) or {}).get('cover', {}).get('proxy')
            
            ydl_opts_video = {
                'format': strategy['format'],
                'merge_output_format': 'mp4',
                'outtmpl': os.path.join(VIDEO_DIR, '%(title)s.%(ext)s'),
                'writethumbnail': False,
                'writeinfojson': False,
                # 'cookiesfrombrowser': (browser,), # We set this conditionally below
                'proxy': proxy,
                'nocheckcertificate': True,
                'force_ipv4': True,
                # More resilient defaults for flaky connections / SSL EOF
                'retries': 3,
                'fragment_retries': 3,
                'extractor_retries': 3,
                'retry_sleep': 1,
                'http_chunk_size': 5 * 1024 * 1024,
                'continuedl': True,           # 允许断点续传
                'overwrites': True,           # 强制覆盖异常残留文件，避免“已下载”误判
                'concurrent_fragments': 1,    # Disable concurrency to fix malloc error
                'quiet': False,               # Enable progress bar
                'no_warnings': False,         # Show warnings (like 403 retries)
                'socket_timeout': 30,         # 增加超时设置
                'logger': YdlLogger(),
                'writesubtitles': True,
                'writeautomaticsub': True,
                'subtitleslangs': ['en', 'zh-Hans', 'zh-Hant'],
                'subtitlesformat': 'vtt', # Force VTT format to avoid karaoke mess
                'postprocessors': [
                    {'key': 'FFmpegMetadata'},
                ],
                'remote_components': ['ejs:github'],
                # 2026-08-20: YouTube SABR + PO Token 反爬升级，同样 fallback client 链（见 ydl_opts_info）
                'extractor_args': {
                    'youtube': {
                        'player_client': ['default', 'web_embedded', 'web_safari', 'mweb'],
                    }
                },
                'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
                'ignoreerrors': True, # Ignore subtitle/non-critical errors (e.g. 429 on subs). File existence check catches real video DL failures.
            }
            
            # Apply overrides (Cookies + UA) from browser sniff result
            if common_opts_override:
                ydl_opts_video.update(common_opts_override)
            # Fallback legacy logic if no override
            elif temp_cookie_path:
                  ydl_opts_video['cookiefile'] = temp_cookie_path
            else:
                  apply_youtube_cookie_source(ydl_opts_video, browser, cookie_file)

            # Retry loop for Auth failures
            for attempt in range(2):
                try:
                    with yt_dlp.YoutubeDL(ydl_opts_video) as ydl:
                        info = ydl.extract_info(url, download=True)
                        if not info:
                            raise RuntimeError("yt-dlp returned no media info (likely blocked by anti-bot).")
                        title = info.get('title', 'Unknown')
                        title = sanitize_filename(title) # Sanitize

                        info['title'] = title
                        video_path = _resolve_downloaded_video_path(
                            ydl.prepare_filename(info),
                            title_hint=title,
                        )
                        
                        print_t('dl_complete', title)
                        print_t('dl_path', video_path)
                        
                        # Verify integrity
                        if not os.path.exists(video_path):
                            _console(f"⚠️ 视频文件未生成: {video_path}，可能遇到下载限制或流中断。")
                            break # Skip to next strategy because video failed to download despite ignoreerrors=True

                        if not verify_video_integrity(video_path):
                            # Try automatic repair first (it's fast)
                            if repair_video(video_path):
                                # Re-verify after repair
                                if verify_video_integrity(video_path):
                                     return video_path
                            
                            # If repair fails or still invalid, try resuming download (yt-dlp will handle this)
                            _console("🔄 视频完整性校验未通过，尝试恢复下载...")
                            try:
                                with yt_dlp.YoutubeDL(ydl_opts_video) as ydl_retry:
                                     ydl_retry.extract_info(url, download=True)
                                
                                if verify_video_integrity(video_path):
                                     return video_path
                            except:
                                pass
                        else:
                            return video_path
                        
                        # If still here, this strategy failed integrity/repair
                        _console("⚠️ 视频损坏且自动修复失败，尝试下一个下载策略...")
                        # If we successfully downloaded but verification failed, we BREAK the retry loop 
                        # to try next strategy, NOT retry auth.
                        break 
                        
                except Exception as e:
                    # Check Auth
                    is_auth_error = is_youtube_auth_error(e) or "HTTP Error 403" in str(e)
                    
                    if is_auth_error and attempt == 0:
                        print_t('dl_fail', f"Auth/Bot Error detected: {e}")
                        refreshed = refresh_youtube_cookies(cookie_file, browser)
                        if refreshed:
                            print_t('info', "🔄 Retrying with fresh cookies...")
                            apply_youtube_cookie_source(ydl_opts_video, browser, cookie_file)
                            continue
                        if not prompted_youtube_login and prompt_youtube_login(browser, url):
                            prompted_youtube_login = True
                            if refresh_youtube_cookies(cookie_file, browser):
                                print_t('info', "🔄 Retrying with freshly logged-in YouTube cookies...")
                                apply_youtube_cookie_source(ydl_opts_video, browser, cookie_file)
                                continue
                    if is_auth_error and not prompted_youtube_login and prompt_youtube_login(browser, url):
                        prompted_youtube_login = True
                        if refresh_youtube_cookies(cookie_file, browser):
                            print_t('info', "🔄 Retrying with freshly logged-in YouTube cookies...")
                            apply_youtube_cookie_source(ydl_opts_video, browser, cookie_file)
                            continue
                    
                    # If not auth retry, re-raise to be caught by outer try-except (strategy loop)
                    raise e
                
        except Exception as e:
            last_error = e
            # If it's the last strategy, don't continue loop effectively, but loop naturally ends
            continue

    if progressive_stream_fallback_url and not stream_url_override and not progressive_fallback_attempted:
        progressive_path = _download_progressive_fallback_stream(
            progressive_stream_fallback_url,
            video_dir=VIDEO_DIR,
            title_hint=info.get('title') or info.get('id') or 'video',
            stream_headers_override=stream_headers_override,
            user_agent_override=user_agent_override,
            temp_cookie_path=temp_cookie_path,
        )
        if progressive_path:
            return progressive_path

    # Fallback: retry with aria2c external downloader when native yt-dlp fails
    if last_error is not None and not stream_url_override:
        _console("🔄 尝试 aria2c 作为外部下载器进行兜底...")
        aria2c_proxy = CONFIG.get('proxy')
        if not aria2c_proxy:
            aria2c_proxy = (CONFIG.get('services', {}) or {}).get('cover', {}).get('proxy')
        for strategy in strategies:
            try:
                ydl_opts_aria2c = {
                    'format': strategy['format'],
                    'merge_output_format': 'mp4',
                    'outtmpl': os.path.join(VIDEO_DIR, '%(title)s.%(ext)s'),
                    'proxy': aria2c_proxy,
                    'nocheckcertificate': True,
                    'force_ipv4': True,
                    'retries': 3,
                    'fragment_retries': 3,
                    'extractor_retries': 3,
                    'retry_sleep': 1,
                    'http_chunk_size': 5 * 1024 * 1024,
                    'external_downloader': 'aria2c',
                    'external_downloader_args': ['-x', '16', '-s', '16', '-k', '1M', '--disable-ipv6=true'],
                    'continuedl': True,
                    'overwrites': True,
                    'concurrent_fragments': 1,
                    'quiet': False,
                    'no_warnings': False,
                    'socket_timeout': 30,
                    'logger': YdlLogger(),
                    'writesubtitles': True,
                    'writeautomaticsub': True,
                    'subtitleslangs': ['en', 'zh-Hans', 'zh-Hant'],
                    'subtitlesformat': 'vtt',
                    'postprocessors': [{'key': 'FFmpegMetadata'}],
                    'remote_components': ['ejs:github'],
                    'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
                    'ignoreerrors': True,
                }
                if common_opts_override:
                    ydl_opts_aria2c.update(common_opts_override)
                elif temp_cookie_path:
                    ydl_opts_aria2c['cookiefile'] = temp_cookie_path
                else:
                    apply_youtube_cookie_source(ydl_opts_aria2c, browser, cookie_file)

                with yt_dlp.YoutubeDL(ydl_opts_aria2c) as ydl:
                    info = ydl.extract_info(url, download=True)
                    if not info:
                        continue
                    title = sanitize_filename(info.get('title', 'Unknown'))
                    info['title'] = title
                    video_path = _resolve_downloaded_video_path(
                        ydl.prepare_filename(info),
                        title_hint=title,
                    )
                    if os.path.exists(video_path) and verify_video_integrity(video_path):
                        print_t('dl_complete', f"{title} (aria2c fallback)")
                        return video_path
            except Exception:
                continue

    # Last-resort: sniff stream URL and try direct download if we never had an override.
    if last_error is not None and not stream_url_override and should_try_browser_sniff(last_error):
        try:
            from core.modules.browser_downloader import sniff_video_info
            sniffed = sniff_video_info(url, cookie_file)
            if sniffed and sniffed.get('url') and not is_sabr_control_stream(sniffed.get('url')):
                _console("🔄 使用浏览器嗅探直链进行最后一次下载尝试...")
                temp_cookie_path2 = None
                try:
                    if sniffed.get('cookies'):
                        temp_cookie_path2 = write_temp_cookie_file(sniffed.get('cookies'))
                except Exception as e:
                    _verbose(f"⚠️ 直链下载写入临时 Cookies 失败: {e}")

                ydl_opts_stream = {
                    'outtmpl': os.path.join(VIDEO_DIR, '%(title)s.%(ext)s'),
                    'quiet': False,
                    'no_warnings': True,
                    'http_headers': sniffed.get('headers') or {},
                    'nocheckcertificate': True,
                    'force_ipv4': True,
                    'force_generic_extractor': True,
                    'fixup': 'never',
                    'overwrites': True,
                }
                if sniffed.get('ua'):
                    ydl_opts_stream['user_agent'] = sniffed.get('ua')
                if temp_cookie_path2:
                    ydl_opts_stream['cookiefile'] = temp_cookie_path2

                with yt_dlp.YoutubeDL(ydl_opts_stream) as ydl:
                    title = sanitize_filename(sniffed.get('title') or "video")
                    target_video_path = os.path.join(VIDEO_DIR, f"{title}.mp4")
                    if os.path.exists(target_video_path) and os.path.getsize(target_video_path) <= 1024:
                        try:
                            os.remove(target_video_path)
                        except Exception:
                            pass
                    ydl.params['outtmpl'] = {'default': target_video_path}
                    ydl.download([sniffed.get('url')])

                if temp_cookie_path2 and os.path.exists(temp_cookie_path2):
                    try:
                        os.remove(temp_cookie_path2)
                    except Exception:
                        pass

                if os.path.exists(target_video_path) and os.path.getsize(target_video_path) > 1024:
                    print_t('dl_complete', "Browser Stream (Fallback)")
                    return target_video_path
        except Exception as e:
            _verbose(f"⚠️ 直链兜底失败: {e}")

    # Cleanup temp cookies after all strategies and fallbacks finish using them.
    if temp_cookie_path and os.path.exists(temp_cookie_path):
        try:
            os.remove(temp_cookie_path)
        except:
            pass

    # If we fall through here, and NO sniffing happened earlier (e.g. step 1 succeeded but download failed?)
    # That's rare if step 1 used sniffing.
    # But if standard download fails here, we could try sniffing AGAIN as a last resort?
    # For now, let's assume Step 1 handles the heavy lifting of switching modes.
    
    if last_error is None:
        last_error = "No downloadable media stream obtained (possible anti-bot block or invalid YouTube cookies)."
    print_t('dl_fail', last_error)
    return None

def _resolve_downloaded_video_path(
    prepared_path: str,
    *,
    title_hint: str = "",
    extra_extensions: Optional[list[str]] = None,
) -> str:
    """Resolve the real downloaded media path when yt-dlp's prepared filename is stale."""
    if prepared_path and os.path.exists(prepared_path) and os.path.getsize(prepared_path) > 1024:
        try:
            if file_has_video_stream(prepared_path):
                return prepared_path
        except Exception:
            pass

    candidates: list[str] = []
    if prepared_path:
        base_no_ext, current_ext = os.path.splitext(prepared_path)
        candidates.append(prepared_path)
        extensions = extra_extensions or [".mp4", ".mkv", ".webm", ".m4v", ".mov"]
        for ext in extensions:
            if ext != current_ext:
                candidates.append(base_no_ext + ext)

        normalized_base = re.sub(r'\.f\d+(?:-\d+)?$', '', base_no_ext)
        if normalized_base and normalized_base != base_no_ext:
            for ext in extensions:
                candidates.append(normalized_base + ext)

        directory = os.path.dirname(prepared_path) or "."
    else:
        directory = "."

    if title_hint:
        safe_title = sanitize_filename(title_hint)
        for matched in sorted(glob.glob(os.path.join(directory, f"{safe_title}*"))):
            if matched.endswith((".part", ".aria2", ".json", ".info.json", ".jpg", ".jpeg", ".png", ".webp", ".vtt", ".srt", ".ass")):
                continue
            candidates.append(matched)

    best_path = ""
    best_size = -1
    best_video_path = ""
    best_video_size = -1
    seen = set()
    for cand in candidates:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        try:
            if os.path.exists(cand):
                size = os.path.getsize(cand)
                if size > 1024 and size > best_size:
                    best_path = cand
                    best_size = size
                if size > 1024 and file_has_video_stream(cand) and size > best_video_size:
                    best_video_path = cand
                    best_video_size = size
        except Exception:
            continue

    return best_video_path or best_path


# Combined wrapper for backward compatibility or simple usage
def download_youtube_video(url: str):
    info = fetch_video_info(url)
    if info:
        path = download_video_stream(url, info=info)
        if path:
            info['video_path'] = path
            return info
    return None

def cleanup_temp_files():
    """Clean up temporary download files (.part, .ytdl)"""
    
    CONFIG = load_config()
    VIDEO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), CONFIG.get('video_source_dir', '../videos')))

    if not os.path.exists(VIDEO_DIR):
        return
        
    cleaned = False
    for f in os.listdir(VIDEO_DIR):
        if f.endswith(('.part', '.ytdl')):
            try:
                os.remove(os.path.join(VIDEO_DIR, f))
                cleaned = True
                # We could log detailed files, but one simple message is enough
            except:
                pass
    
    if cleaned:
        pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print_t('info', "Usage: python download_video.py <URL>")
        sys.exit(1)
    
    url = sys.argv[1]
    download_youtube_video(url)

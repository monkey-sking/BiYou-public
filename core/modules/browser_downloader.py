"""
Browser-based video info sniffer using Playwright.
Acts as a fallback when yt-dlp fails (e.g. 403 Forbidden).
"""
import os
import sys
import json
import time
import asyncio
import subprocess
from typing import Optional, Dict, Any
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from playwright.async_api import async_playwright, Page, Request

from core.utils.browser_config import (
    get_browser_debug_port,
    prepare_browser_launch,
    resolve_browser_profile_dir,
    terminate_browser_process,
)
from core.utils.logger import log
from core.utils.config import load_config, update_config
from core.utils.cookie import get_cookies_from_browser, get_user_agent, resolve_browser_app_info

DEBUG_PORT = get_browser_debug_port(load_config())


class _BrowserPageSession:
    def __init__(
        self,
        page,
        browser,
        context,
        *,
        close_page_on_exit: bool = True,
        close_browser_on_exit: bool = False,
    ):
        self.page = page
        self.browser = browser
        self.context = context
        self._close_page_on_exit = bool(close_page_on_exit)
        self._close_browser_on_exit = bool(close_browser_on_exit)

    async def close(self):
        if self._close_page_on_exit and self.page:
            close_page = getattr(self.page, "close", None)
            if callable(close_page):
                maybe_close = close_page()
                if asyncio.iscoroutine(maybe_close):
                    await maybe_close

        if self._close_browser_on_exit and self.browser:
            close_browser = getattr(self.browser, "close", None)
            if callable(close_browser):
                maybe_close = close_browser()
                if asyncio.iscoroutine(maybe_close):
                    await maybe_close


def _with_youtube_no_autoplay(url: str) -> str:
    """Keep YouTube watch navigation silent while preserving the original target."""
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


async def _install_no_autoplay_hooks(page: Page):
    """Mute and pause media elements as early as possible to avoid surprise playback."""
    await page.add_init_script("""
() => {
  const silence = (media) => {
    try { media.autoplay = false; } catch {}
    try { media.defaultMuted = true; } catch {}
    try { media.muted = true; } catch {}
    try { media.volume = 0; } catch {}
    try { media.pause(); } catch {}
  };

  const patchExisting = () => {
    try {
      for (const media of document.querySelectorAll('video, audio')) {
        silence(media);
      }
    } catch {}
  };

  const origPlay = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function(...args) {
    silence(this);
    return origPlay.apply(this, args);
  };

  document.addEventListener('DOMContentLoaded', patchExisting, { once: true });
  window.addEventListener('load', patchExisting, { once: true });
  new MutationObserver(() => patchExisting()).observe(document, { childList: true, subtree: true });
}
""")


async def _pause_page_media(page: Page):
    try:
        await page.evaluate("""
() => {
  for (const media of document.querySelectorAll('video, audio')) {
    try { media.autoplay = false; } catch {}
    try { media.defaultMuted = true; } catch {}
    try { media.muted = true; } catch {}
    try { media.volume = 0; } catch {}
    try { media.pause(); } catch {}
  }
}
""")
    except Exception:
        pass


def _score_progressive_format(fmt: dict) -> tuple[int, int]:
    height = int(fmt.get("height") or 0)
    bitrate = int(fmt.get("bitrate") or 0)
    return height, bitrate


def _merge_player_stream_data(result: dict[str, Any], player_data: dict[str, Any]) -> bool:
    """Merge player response data into sniff result and report whether a progressive fallback is available."""
    if not player_data:
        return False

    if player_data.get("title"):
        result["title"] = player_data.get("title")
    if player_data.get("description"):
        result["description"] = player_data.get("description")
    if player_data.get("thumbnail"):
        result["thumbnail"] = player_data.get("thumbnail")
    if player_data.get("id"):
        result["id"] = player_data.get("id")
    if player_data.get("visitor_data"):
        result["visitor_data"] = player_data.get("visitor_data")
    if player_data.get("progressive_formats"):
        result["progressive_formats"] = player_data.get("progressive_formats") or []
    if player_data.get("adaptive_formats"):
        result["adaptive_formats"] = player_data.get("adaptive_formats") or []
    if player_data.get("hls_manifest_url"):
        result["hls_manifest_url"] = player_data.get("hls_manifest_url")
    if player_data.get("dash_manifest_url"):
        result["dash_manifest_url"] = player_data.get("dash_manifest_url")
    if player_data.get("server_abr_streaming_url"):
        result["server_abr_streaming_url"] = player_data.get("server_abr_streaming_url")
    if player_data.get("progressive_stream_url"):
        result["progressive_stream_url"] = player_data.get("progressive_stream_url")
        return True
    return False


async def _extract_player_stream_data(page: Page) -> dict[str, Any]:
    """Read player response data directly from the page when network sniffing only sees SABR control requests."""
    try:
        data = await page.evaluate("""
() => {
  const pr = window.ytInitialPlayerResponse
    || (window.ytplayer?.config?.args?.player_response
      ? JSON.parse(window.ytplayer.config.args.player_response)
      : null);
  const streaming = pr?.streamingData || {};
  const normalize = (items) => (items || []).map((item) => ({
    itag: item.itag || null,
    mimeType: item.mimeType || null,
    qualityLabel: item.qualityLabel || null,
    audioQuality: item.audioQuality || null,
    bitrate: item.bitrate || null,
    contentLength: item.contentLength || null,
    height: item.height || null,
    width: item.width || null,
    fps: item.fps || null,
    url: item.url || null,
    signatureCipher: item.signatureCipher || null,
  }));
  return {
    title: pr?.videoDetails?.title || document.title.replace(/ - YouTube$/, ''),
    description: pr?.videoDetails?.shortDescription || null,
    thumbnail: pr?.videoDetails?.thumbnail?.thumbnails?.slice(-1)?.[0]?.url || null,
    id: pr?.videoDetails?.videoId || null,
    visitorData: window.ytcfg?.get?.('VISITOR_DATA') || window.ytcfg?.data_?.VISITOR_DATA || null,
    progressiveFormats: normalize(streaming.formats),
    adaptiveFormats: normalize(streaming.adaptiveFormats),
    hlsManifestUrl: streaming.hlsManifestUrl || null,
    dashManifestUrl: streaming.dashManifestUrl || null,
    serverAbrStreamingUrl: streaming.serverAbrStreamingUrl || null,
  };
}
""")
    except Exception:
        return {}

    progressive_formats = [
        fmt for fmt in (data.get("progressiveFormats") or [])
        if fmt.get("url") and "googlevideo.com" in fmt.get("url", "")
    ]
    progressive_formats.sort(key=_score_progressive_format, reverse=True)
    best_progressive = progressive_formats[0] if progressive_formats else None

    adaptive_formats = data.get("adaptiveFormats") or []
    return {
        "title": data.get("title"),
        "description": data.get("description"),
        "thumbnail": data.get("thumbnail"),
        "id": data.get("id"),
        "visitor_data": data.get("visitorData"),
        "progressive_formats": progressive_formats,
        "adaptive_formats": adaptive_formats,
        "progressive_stream_url": best_progressive.get("url") if best_progressive else None,
        "hls_manifest_url": data.get("hlsManifestUrl"),
        "dash_manifest_url": data.get("dashManifestUrl"),
        "server_abr_streaming_url": data.get("serverAbrStreamingUrl"),
    }


def _pick_reusable_page(context):
    """Prefer an existing normal tab instead of always spawning a new one."""
    pages = [page for page in context.pages if not page.is_closed()]
    for page in reversed(pages):
        url = page.url or ""
        if not url or url == "about:blank":
            return page
        if url.startswith("chrome-extension://") or url.startswith("devtools://"):
            continue
        return page
    return None


def _get_browser_launch_config():
    config = load_config()
    profile_dir = resolve_browser_profile_dir(config=config)
    browser_key, executable, configured_profile = resolve_browser_app_info(
        browser=config.get('browser'),
        user_data_dir=profile_dir,
    )

    # For Chromium-based browsers, allow default system profile when browser_profile is null.
    if not configured_profile and browser_key in {"chrome", "chromium", "edge", "brave", "opera"}:
        if sys.platform == 'darwin':
            mac_default_profiles = {
                'chrome': os.path.expanduser("~/Library/Application Support/Google/Chrome"),
                'chromium': os.path.expanduser("~/Library/Application Support/Chromium"),
                'edge': os.path.expanduser("~/Library/Application Support/Microsoft Edge"),
                'brave': os.path.expanduser("~/Library/Application Support/BraveSoftware/Brave-Browser"),
                'opera': os.path.expanduser("~/Library/Application Support/com.operasoftware.Opera"),
            }
            configured_profile = mac_default_profiles.get(browser_key)

    return browser_key, executable, configured_profile


def _is_sabr_control_stream(url: str) -> bool:
    """
    YouTube SABR control endpoint (returns tiny application/vnd.yt-ump payload)
    should not be treated as a downloadable media stream.
    """
    try:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        has_sabr = query.get('sabr', ['0'])[-1] == '1' or 'sabr=1' in url
        has_mime = 'mime' in query or 'mime=' in url
        has_range = 'range' in query or 'range=' in url
        return has_sabr and not has_mime and not has_range
    except Exception:
        # Be conservative on parse failures
        return ('sabr=1' in url) and ('mime=' not in url) and ('range=' not in url)

def _is_port_open(port):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0


def _load_profile_cookies(browser_key: str, user_data_dir: Optional[str]) -> list[dict]:
    """Extract YouTube/Google cookies from the configured browser profile for Playwright."""
    cookies = []
    seen = set()
    for domain in (".youtube.com", ".google.com", "accounts.google.com"):
        jar = get_cookies_from_browser(domain, browser_key, user_data_dir=user_data_dir)
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


async def _launch_browser_with_profile_cookies(
    p,
    browser_key: str,
    browser_executable: Optional[str],
    user_data_dir: Optional[str],
    *,
    headless: bool,
):
    """Launch an isolated browser with injected profile cookies so we don't hijack the user's visible tab."""
    # 2026-08-08: 浏览器操作审计日志（定位窗口来源）
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.expanduser("~/.hermes/scripts"))
        import browser_activity_log
        browser_activity_log.log_browser(
            "launch_browser", browser_executable or browser_key,
            f"browser_downloader._launch_browser_with_profile_cookies headless={headless}",
            extra={"script": "BiYou browser_downloader.py"})
    except Exception:
        pass
    log.info("   🔄 Launching isolated browser context with injected profile cookies...")
    launch_kwargs = {
        "headless": headless,
        "args": [
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars",
            "--start-maximized",
            "--mute-audio",
        ],
    }
    if browser_executable:
        launch_kwargs["executable_path"] = browser_executable

    browser = await p.chromium.launch(**launch_kwargs)
    context = await browser.new_context(user_agent=get_user_agent())
    cookies = _load_profile_cookies(browser_key, user_data_dir)
    if cookies:
        await context.add_cookies(cookies)
        log.info(f"   🍪 Injected {len(cookies)} cookies into standalone browser context.")
    else:
        log.warning("   ⚠️ No profile cookies available for standalone browser fallback.")
    page = await context.new_page()
    return _BrowserPageSession(
        page,
        browser,
        context,
        close_page_on_exit=True,
        close_browser_on_exit=True,
    )

async def _get_browser_page(p) -> Optional[_BrowserPageSession]:
    """Prefer an isolated background browser; only touch the visible browser if we have no better option."""
    global DEBUG_PORT
    browser = None
    try:
        browser_key, browser_executable, user_data_dir = _get_browser_launch_config()
        if _load_profile_cookies(browser_key, user_data_dir):
            return await _launch_browser_with_profile_cookies(
                p,
                browser_key,
                browser_executable,
                user_data_dir,
                headless=True,
            )

        launch = prepare_browser_launch(
            browser_executable=browser_executable,
            profile_dir=user_data_dir,
            preferred_debug_port=DEBUG_PORT,
        )
        DEBUG_PORT = launch["debug_port"]
        if launch["port_changed"]:
            update_config("browser_debug_port", DEBUG_PORT)
        cdp_url = f"http://localhost:{DEBUG_PORT}"

        if launch["action"] == "launch":
            # 2026-08-10: 不再 Popen 启动新 CDP 实例（窗口堆积/进程爆炸根因）。
            # 直接走 _launch_browser_with_profile_cookies fallback（注入 cookie 的
            # headless 独立上下文，不碰用户浏览器）。下方 connect_over_cdp 会失败，
            # 然后进入该 fallback。
            log.info(f"   ⚠️ 无运行中的 {browser_key} CDP 实例 (port {DEBUG_PORT})，"
                     f"跳过自动启动，走 standalone cookie fallback...")
        else:
            log.info(f"   🔁 Reusing configured {browser_key} profile at {cdp_url}...")
        
        log.info(f"   🔗 Connecting to {browser_key} at {cdp_url}...")
        try:
            browser = await p.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            await asyncio.sleep(2)
            try:
                browser = await p.chromium.connect_over_cdp(cdp_url)
            except Exception:
                return await _launch_browser_with_profile_cookies(
                    p,
                    browser_key,
                    browser_executable,
                    user_data_dir,
                    headless=True,
                )

        # Get Context
        if len(browser.contexts) > 0:
            context = browser.contexts[0]
        else:
            context = await browser.new_context()

        page = _pick_reusable_page(context)
        close_page_on_exit = False
        if page is None:
            page = await context.new_page()
            close_page_on_exit = True
        return _BrowserPageSession(
            page,
            browser,
            context,
            close_page_on_exit=close_page_on_exit,
            close_browser_on_exit=launch["action"] == "launch",
        )
        
    except Exception as e:
        log.error(f"❌ Browser Connection Error: {e}")
        try:
            browser_key, browser_executable, user_data_dir = _get_browser_launch_config()
            return await _launch_browser_with_profile_cookies(
                p,
                browser_key,
                browser_executable,
                user_data_dir,
                headless=True,
            )
        except Exception as fallback_error:
            log.error(f"❌ Standalone Browser Fallback Error: {fallback_error}")
            return None

async def _sniff_logic(url: str) -> Optional[Dict[str, Any]]:
    """Async implementation of sniffing logic"""
    result = {
        "url": None,
        "headers": {},
        "ua": None,
        "title": None,
        "description": None,
        "thumbnail": None,
        "id": None,
        "cookies": [],
        "is_sabr_control": False,
        "playability_status": None,
        "playability_reason": None,
        "requires_login": False,
    }
    
    async with async_playwright() as p:
        session = await _get_browser_page(p)
        if not session or not session.page:
            return None
        page = session.page
        context = session.context
            
        try:
            # Capture network
            found_stream = False
            saw_sabr_control = False
            
            async def handle_request(request: Request):
                nonlocal found_stream, saw_sabr_control
                if found_stream: return
                
                u = request.url
                # Heuristic for video streams
                if ".googlevideo.com/videoplayback" in u or ".m3u8" in u:
                    # Filter headers: STRICT MODE
                    # yt-dlp/requests will add its own Host, Content-Length, etc.
                    # Passing 'Origin' or 'Authorization' often triggers 400/403 if they don't match the new request context.
                    # We ONLY need Cookies and UA for the download to work.
                    allowed_headers = ['cookie', 'referer', 'user-agent']
                    safe_headers = {k: v for k, v in request.headers.items() if k.lower() in allowed_headers}
                    if safe_headers and not result['headers']:
                        result['headers'] = safe_headers
                    if not result['ua']:
                        result['ua'] = request.headers.get('user-agent', get_user_agent())

                    # Remove 'range' parameter to request full file
                    import re
                    cleaned_url = re.sub(r'[?&]range=[^&]*', '', u)
                    if _is_sabr_control_stream(cleaned_url):
                        saw_sabr_control = True
                        result['is_sabr_control'] = True
                        log.info("   ⚠️ Detected SABR control stream (not a downloadable media stream), continuing sniff...")
                        return

                    log.info(f"   🎯 Sniffed Video Stream: {cleaned_url[:50]}...")
                    result['url'] = cleaned_url
                    result['is_sabr_control'] = False
                    found_stream = True
            
            page.on("request", handle_request)
            
            target_url = _with_youtube_no_autoplay(url)
            await _install_no_autoplay_hooks(page)
            log.info(f"   🌍 Navigating to {target_url}...")
            try:
                # Use a shorter timeout for navigation, we validly expect stream to sniff early
                await page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
            except Exception as e:
                log.warning(f"   ⚠️ Navigation timeout/error: {e}. Checking if we have what we need...")
            await _pause_page_media(page)
            
            # Wait for title to update from initial "YouTube"
            for _ in range(10):
                try:
                     title = await page.title()
                     if " - YouTube" in title and title.strip() != "YouTube":
                         break
                except: pass
                await asyncio.sleep(0.5)
            
            try:
                result['title'] = await page.title()
                result['title'] = result['title'].replace(" - YouTube", "")
            except:
                result['title'] = "Unknown Video"
            if not result['title'] or result['title'] == "YouTube":
                 # Fallback title from URL ID or timestamp if title detection fails
                 result['title'] = f"video_{int(time.time())}"

            try:
                player_state = await page.evaluate("""
() => {
  const pr = window.ytInitialPlayerResponse
    || (window.ytplayer?.config?.args?.player_response
      ? JSON.parse(window.ytplayer.config.args.player_response)
      : null);
  const status = pr?.playabilityStatus?.status || null;
  const reason = pr?.playabilityStatus?.reason
    || pr?.playabilityStatus?.errorScreen?.playerErrorMessageRenderer?.reason?.simpleText
    || null;
  return {status, reason};
}
""")
                if player_state:
                    result['playability_status'] = player_state.get('status')
                    result['playability_reason'] = player_state.get('reason')
                    result['requires_login'] = player_state.get('status') == 'LOGIN_REQUIRED'
            except Exception:
                pass
            
            # Wait for video player and maybe click play if needed
            # Usually YouTube autoplay handles it, or at least loads the manifest
            
            log.info("   ⏳ Waiting for stream (30s)...")
            # Wait loop with interaction
            start_time = time.time()
            last_player_probe = 0.0
            while time.time() - start_time < 30:
                if found_stream: break
                elapsed = time.time() - start_time
                
                # Periodically attempt to interact to wake up player
                if int(elapsed) % 3 == 0:
                    try:
                        # 1. Consensus/Cookie Popup?
                        buttons = page.locator('button[aria-label="Accept all"], button[aria-label="Reject all"], button:has-text("Accept all")')
                        if await buttons.count() > 0 and await buttons.first.is_visible():
                            await buttons.first.click()
                        
                        # 2. Large Play Button (Initial)
                        large_play = page.locator('.ytp-large-play-button')
                        if await large_play.count() > 0 and await large_play.is_visible():
                            await large_play.click()
                        
                        # 3. Standard Play Button (if paused)
                        # "Play (k)" or "Play"
                        play_btn = page.locator('.ytp-play-button:not([title^="Pause"])') 
                        if await play_btn.count() > 0 and await play_btn.is_visible():
                             await play_btn.click()
                    except Exception: pass

                if elapsed - last_player_probe >= 2.0:
                    last_player_probe = elapsed
                    try:
                        player_data = await _extract_player_stream_data(page)
                    except Exception:
                        player_data = {}
                    if _merge_player_stream_data(result, player_data) and not result.get('url'):
                        log.info("   ⚡️ Found progressive fallback stream from player response. Ending sniff wait early.")
                        break
                
                await asyncio.sleep(1)

            # Export authenticated cookies from the current browser context for yt-dlp fallback.
            try:
                raw_cookies = await context.cookies(["https://www.youtube.com", "https://www.google.com"])
                cookies = []
                for c in raw_cookies:
                    exp = c.get('expires', -1)
                    if exp in (None, -1):
                        exp = int(time.time() + 3600 * 24 * 30)
                    cookies.append({
                        'domain': c.get('domain', ''),
                        'name': c.get('name', ''),
                        'value': c.get('value', ''),
                        'path': c.get('path', '/'),
                        'secure': bool(c.get('secure', False)),
                        'expiry': int(exp),
                    })
                result['cookies'] = cookies
                if cookies:
                    log.info(f"   🍪 Exported {len(cookies)} browser cookies for fallback download.")
            except Exception as e:
                log.warning(f"   ⚠️ Failed to export browser cookies: {e}")

            player_data = await _extract_player_stream_data(page)
            _merge_player_stream_data(result, player_data)
                
            if result['url']:
                log.info("   ✅ Sniffing Successful!")
                # Double check headers are populated
                if not result['headers']:
                     log.warning("   ⚠️ No headers captured, might fail download.")
                return result
            else:
                if result.get('progressive_stream_url'):
                    log.info("   ✅ Extracted progressive fallback stream from player response.")
                    return result
                if saw_sabr_control:
                    log.warning("   ⚠️ Only SABR control stream detected. Will fallback using browser cookies/UA.")
                    if result.get('ua') or result.get('headers') or result.get('cookies'):
                        return result
                if result.get('requires_login'):
                    log.warning(
                        f"   ⚠️ YouTube player requires login: {result.get('playability_reason') or result.get('playability_status')}"
                    )
                    return result
                log.warning("   ⚠️ No usable stream detected after 30s. (Attempts to click Play failed)")
                # Debug: Take screenshot
                try:
                    await page.screenshot(path="debug_sniff_fail.png")
                    log.warning("   📸 Saved debug screenshot to debug_sniff_fail.png")
                except: pass
                return None
                
        except Exception as e:
            log.error(f"❌ Sniffing Error: {e}")
            return None
        finally:
            await session.close()

def sniff_video_info(url: str, cookie_file: str = None) -> Optional[Dict[str, Any]]:
    """
    Public entry point. Synchronous wrapper around async logic.
    """
    try:
        # Check if we have an event loop
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    if loop.is_running():
        # If we are already in async loop (unlikely for this specific tool architecture but possible)
        # We might need nest_asyncio or just run in thread. 
        # For this script context, usually it's blocking.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, _sniff_logic(url)).result()
    else:
        return loop.run_until_complete(_sniff_logic(url))

def get_youtube_cookies_via_browser(browser_name: str, domain: str = ".youtube.com") -> list:
    """Helper to extract cookies if needed explicitly"""
    # This might use browser_cookie3 in simple cases, 
    # but since we have this module, we could export Playwright cookies too.
    # For now, implemented via standard cookie.py
    from core.utils.cookie import get_cookies_from_browser
    cj = get_cookies_from_browser(domain, browser_name)
    if cj:
        return [{'name': c.name, 'value': c.value, 'domain': c.domain, 'path': c.path} for c in cj]
    return []

def convert_cookies_to_netscape(cookies_list: list) -> str:
    """Convert JSON cookies list to Netscape format string"""
    out = "# Netscape HTTP Cookie File\n# This file is generated by biyou\n\n"
    for c in cookies_list:
        domain = c.get('domain', '')
        # Basic Netscape format: domain, flag, path, secure, expiration, name, value
        flag = "TRUE" if domain.startswith('.') else "FALSE"
        path = c.get('path', '/')
        secure = "TRUE" if c.get('secure') else "FALSE"
        expiry = str(int(c.get('expiry', time.time() + 3600*24*30)))
        name = c.get('name', '')
        value = c.get('value', '')
        out += f"{domain}\t{flag}\t{path}\t{secure}\t{expiry}\t{name}\t{value}\n"
    return out

import json
import os
import sys
import asyncio
import inspect
import browser_cookie3
import webbrowser
from core.utils.browser_config import (
    resolve_browser_profile_dir,
)
from core.utils.i18n import print_t


def resolve_browser_app_info(browser=None, user_data_dir=None):
    """
    Resolve the configured browser executable and user-data-dir/profile path.
    Returns: (browser_key, executable_path_or_none, user_data_dir_or_none)
    """
    from core.utils.config import load_config

    config = load_config()
    browser_key = (browser or config.get('browser') or 'chrome').lower()
    if user_data_dir is not None:
        configured_profile = os.path.expanduser(user_data_dir)
    else:
        configured_profile = resolve_browser_profile_dir(config=config)

    app_paths = {}
    if sys.platform == 'darwin':
        app_paths = {
            'chrome': "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            'ego': "/Applications/ego lite.app/Contents/MacOS/ego lite",
            'chromium': "/Applications/Chromium.app/Contents/MacOS/Chromium",
            'edge': "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            'brave': "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            'opera': "/Applications/Opera.app/Contents/MacOS/Opera",
        }
    elif sys.platform == 'win32':
        local_appdata = os.environ.get('LOCALAPPDATA', '')
        program_files = os.environ.get('ProgramFiles', 'C:\\Program Files')
        program_files_x86 = os.environ.get('ProgramFiles(x86)', 'C:\\Program Files (x86)')
        app_paths = {
            'chrome': os.path.join(program_files, 'Google', 'Chrome', 'Application', 'chrome.exe'),
            'edge': os.path.join(program_files_x86, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
            'brave': os.path.join(program_files, 'BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe'),
            'opera': os.path.join(local_appdata, 'Programs', 'Opera', 'opera.exe'),
        }
    else:
        import shutil
        candidates = {
            'chrome': ['google-chrome', 'google-chrome-stable'],
            'chromium': ['chromium', 'chromium-browser'],
            'edge': ['microsoft-edge', 'microsoft-edge-stable'],
            'brave': ['brave-browser'],
            'opera': ['opera'],
        }
        executable = None
        for candidate in candidates.get(browser_key, []):
            executable = shutil.which(candidate)
            if executable:
                break
        return browser_key, executable, configured_profile

    executable = app_paths.get(browser_key)
    if executable and not os.path.exists(executable):
        executable = None

    return browser_key, executable, configured_profile

def open_browser_for_login(url="https://gemini.google.com", browser=None):
    """
    Open the browser for login. Supports opening Chrome with custom profile if configured.
    2026-08-09: 根治窗口堆积 bug — 旧版 subprocess.Popen 每次启动新 ego 进程，
    单实例 ego 下每调一次就开一个新窗口（曾堆积 23+ 窗口 / 40+ renderer 进程）。
    现在统一走 ego-browser CLI 原生通道复用既有实例（不开新进程/不新开窗口）。
    """
    try:
        import subprocess
        cli = os.path.expanduser("~/.local/bin/ego-browser")
        if os.path.exists(cli):
            # 2026-08-26 (4th cookie-loss 防护): 先确认 CDP 端口真正可达再调 CLI。
            # 否则 CLI 会拉起短命实例污染 cookie 库 → Reset。
            _cdp_ok = False
            try:
                sys.path.insert(0, os.path.expanduser("~/.hermes/scripts"))
                from browser_registry import ego_cli_reachable
                _cdp_ok = ego_cli_reachable()
            except Exception:
                _cdp_ok = False
            if not _cdp_ok:
                print("⏱️ ego CDP 不可达，跳过 CLI 打开登录页（不主动拉起）")
            else:
                # ego CLI 原生通道：在 agent 空间打开登录页，复用既有实例
                script = (
                    "await ego.helpers.useOrCreateTaskSpace('biyou-login')\n"
                    "await ego.helpers.switchTaskSpace('biyou-login')\n"
                    "await ego.helpers.takeOverTaskSpace()\n"
                    "const t = await ego.helpers.openOrReuseTab('" + url + "', {wait: false})\n"
                    "console.log('opened ok')\n"
                )
                try:
                    r = subprocess.run([cli, "nodejs"], input=script, capture_output=True,
                                       text=True, timeout=30)
                    out = (r.stdout + r.stderr)
                    if "opened ok" in out or r.returncode == 0:
                        print(f"🌍 Opened {url} in existing ego instance (CLI reuse)")
                        return True
                except Exception as e:
                    print(f"⚠️ ego CLI open failed ({e}), falling back")

        # Fallback to default behavior (last resort — 不启动新浏览器进程，
        # 用 macOS open 复用现有 app 实例)
        print(f"🌍 Opening default browser: {url}")
        try:
            if sys.platform == 'darwin':
                subprocess.Popen(["open", url])
            else:
                webbrowser.open(url)
        except Exception:
            webbrowser.open(url)
        return True
    except Exception as e:
        print(f"❌ Failed to open browser: {e}")
        return False

def detect_installed_browsers():
    """
    Detect installed browsers on the system (cross-platform).
    Returns a dict of {key: display_name}.
    """
    browsers = {}

    # 定义各平台的浏览器路径
    if sys.platform == 'darwin':
        # macOS
        paths = {
            'chrome': '/Applications/Google Chrome.app',
            'edge': '/Applications/Microsoft Edge.app',
            'firefox': '/Applications/Firefox.app',
            'safari': '/Applications/Safari.app',
            'chromium': '/Applications/Chromium.app',
            'brave': '/Applications/Brave Browser.app',
            'opera': '/Applications/Opera.app'
        }
        for key, path in paths.items():
            if os.path.exists(path):
                browsers[key] = path.split('/')[-1].replace('.app', '')

    elif sys.platform == 'win32':
        # Windows
        program_files = os.environ.get('ProgramFiles', 'C:\\Program Files')
        program_files_x86 = os.environ.get('ProgramFiles(x86)', 'C:\\Program Files (x86)')
        local_appdata = os.environ.get('LOCALAPPDATA', '')

        paths = {
            'chrome': [
                os.path.join(program_files, 'Google', 'Chrome', 'Application', 'chrome.exe'),
                os.path.join(program_files_x86, 'Google', 'Chrome', 'Application', 'chrome.exe'),
                os.path.join(local_appdata, 'Google', 'Chrome', 'Application', 'chrome.exe'),
            ],
            'edge': [
                os.path.join(program_files, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
                os.path.join(program_files_x86, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
            ],
            'firefox': [
                os.path.join(program_files, 'Mozilla Firefox', 'firefox.exe'),
                os.path.join(program_files_x86, 'Mozilla Firefox', 'firefox.exe'),
            ],
            'brave': [
                os.path.join(program_files, 'BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe'),
                os.path.join(local_appdata, 'BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe'),
            ],
            'opera': [
                os.path.join(local_appdata, 'Programs', 'Opera', 'opera.exe'),
            ],
        }

        display_names = {
            'chrome': 'Google Chrome',
            'edge': 'Microsoft Edge',
            'firefox': 'Firefox',
            'brave': 'Brave',
            'opera': 'Opera',
        }

        for key, path_list in paths.items():
            for path in path_list:
                if os.path.exists(path):
                    browsers[key] = display_names.get(key, key.title())
                    break

    else:
        # Linux
        # 在 Linux 上检查命令是否存在
        import shutil

        linux_browsers = {
            'chrome': ['google-chrome', 'google-chrome-stable'],
            'chromium': ['chromium', 'chromium-browser'],
            'firefox': ['firefox'],
            'brave': ['brave-browser'],
            'edge': ['microsoft-edge', 'microsoft-edge-stable'],
            'opera': ['opera'],
        }

        display_names = {
            'chrome': 'Google Chrome',
            'chromium': 'Chromium',
            'firefox': 'Firefox',
            'brave': 'Brave',
            'edge': 'Microsoft Edge',
            'opera': 'Opera',
        }

        for key, commands in linux_browsers.items():
            for cmd in commands:
                if shutil.which(cmd):
                    browsers[key] = display_names.get(key, key.title())
                    break

    # Fallback: 如果检测不到，返回常用浏览器
    if not browsers:
        browsers = {'chrome': 'Chrome', 'firefox': 'Firefox'}

    return browsers


def get_user_agent() -> str:
    """
    根据当前平台返回适当的 User-Agent 字符串
    """
    if sys.platform == 'darwin':
        return 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36'
    elif sys.platform == 'win32':
        return 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36'
    else:
        # Linux
        return 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36'

def select_browser(prompt="请选择要提取 Cookie 的浏览器：", auto_confirm=False):
    """
    Interactive menu to select a browser.
    Returns: str (browser key: 'chrome', 'edge', etc.) or None if cancelled.
    """
    browsers = detect_installed_browsers()
    sorted_keys = sorted(browsers.keys())

    if auto_confirm:
        # Default to Chrome if available, else first available
        if 'chrome' in browsers:
            print_t('info', f"⚡️ [Auto] Selecting browser: {browsers['chrome']}")
            return 'chrome'
        elif sorted_keys:
            print_t('info', f"⚡️ [Auto] Selecting browser: {browsers[sorted_keys[0]]}")
            return sorted_keys[0]
        return None

    print(f"\n🔄 {prompt}")
    for idx, key in enumerate(sorted_keys, 1):
        print(f"   [{idx}] {browsers[key]}")
    print("   [0] 取消")

    sys.stdout.flush()
    choice = input(f"请选择 [0-{len(browsers)}]: ").strip()

    if choice == '0':
        return None

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(sorted_keys):
            return sorted_keys[idx]
    except ValueError:
        pass

    print_t('error', "❌ 无效的选择")
    return None

def _get_cookies_via_ego_cli(domain):
    """Extract cookies from the ego browser via ego-browser CLI native channel.

    2026-08-08: ego 常以普通 app 模式启动（--startup-ego-browser-service），不带
    --remote-debugging-port。ego-browser CLI 的 nodejs runtime 原生提供 cdp()
    helper（走自己的内部通道），无需外部 CDP 端口即可拿 cookie。
    返回 http.cookiejar.Cookie 列表，与 _get_cookies_via_cdp 一致。
    """
    import subprocess
    import shutil
    import re as _re
    import json as _json
    from http.cookiejar import Cookie

    cli = shutil.which("ego-browser") or os.path.expanduser("~/.local/bin/ego-browser")
    if not os.path.exists(cli):
        return None

    # 2026-08-26 (4th cookie-loss): 守卫必须先确认 ego 实例健康，不能只看进程在跑。
    # 进程在跑但服务没起时，ego-browser CLI 会自己拉起短命实例 → 污染 cookie 库
    # mmap → 下次启动 Reset 清空登录。
    # 2026-09-14 校正（ego-browser skill v2.0.0）: ego_cli_reachable() 检查的是
    # CLI 原生通道（跑一次 `ego-browser nodejs -e`），**不是** TCP 39222 ——
    # v2.0.0 的 ego 本来就没有对外 CDP 端口，端口检查会误判健康实例。
    try:
        sys.path.insert(0, os.path.expanduser("~/.hermes/scripts"))
        from browser_registry import ego_cli_reachable as _r
        if not _r():
            print_t('warning', "   ⏱️ ego CDP 不可达 (端口未监听)，跳过 CLI 提取（不主动拉起，避免 cookie Reset）")
            return None
    except Exception:
        pass

    MARKER_START = "BILI_COOKIE_JSON_START"
    MARKER_END = "BILI_COOKIE_JSON_END"
    # 2026-08-08: 复用已有空间拿 cookie，不新建空间、不打开新 tab（零窗口/不抢焦点）
    # Network.getAllCookies 拿整个 profile 的 cookie，与当前 tab 无关。
    # 2026-08-08: 浏览器操作审计日志
    try:
        sys.path.insert(0, os.path.expanduser("~/.hermes/scripts"))
        import browser_activity_log
        browser_activity_log.log_browser(
            "get_cookies", domain,
            "BiYou cookie.py _get_cookies_via_ego_cli CLI 提取", extra={"script": "BiYou cookie.py"})
    except Exception:
        pass
    script = (
        "const sp = await ego.helpers.useOrCreateTaskSpace('biyou-cookie-export')\n"
        "await ego.helpers.switchTaskSpace('biyou-cookie-export')\n"
        "await ego.helpers.takeOverTaskSpace()\n"
        "const cookies = await ego.helpers.cdp('Network.getAllCookies', {})\n"
        f"  const wanted = (cookies.cookies || []).filter(c => c.domain.includes('{domain}'))\n"
        f"  cliLog('{MARKER_START}' + JSON.stringify(wanted) + '{MARKER_END}')\n"
    )
    try:
        result = subprocess.run([cli, "nodejs"], input=script,
                                capture_output=True, text=True, timeout=60)
    except Exception as e:
        print_t('warning', f"   ⏱️ ego-cli cookie extraction failed: {e}")
        return None
    if result.returncode != 0:
        print_t('warning', f"   ⏱️ ego-cli exit {result.returncode}: {result.stderr.strip()[-200:]}")
        return None
    m = _re.search(rf"{MARKER_START}(.*?){MARKER_END}", result.stderr, _re.S)
    if not m:
        return None
    try:
        raw_cookies = _json.loads(m.group(1))
    except Exception:
        return None
    if not raw_cookies:
        return None

    out = []
    for c in raw_cookies:
        d = c.get('domain', '')
        out.append(Cookie(
            version=0, name=c['name'], value=c['value'],
            port=None, port_specified=False,
            domain=d, domain_specified=True, domain_initial_dot=d.startswith('.'),
            path=c.get('path', '/'), path_specified=True,
            secure=bool(c.get('secure')), expires=c.get('expires') or None,
            discard=False, comment=None, comment_url=None, rest={}
        ))
    return out


def _get_cookies_via_cdp(domain, debug_port=39222):
    """Extract cookies from a running browser via CDP (works for ego, whose
    cookie store uses the 'ego safe storage' keychain secret that
    browser_cookie3 cannot decrypt; CDP returns already-decrypted values)."""
    import urllib.request
    import json as _json
    import asyncio
    import websockets
    from http.cookiejar import Cookie

    # Resolve the actual debug port: prefer config value, fall back to the
    # caller-provided port, and finally probe a small range (BiYou's
    # prepare_browser_launch may shift the port if 39222 is busy).
    try:
        from core.utils.browser_config import get_browser_debug_port
        from core.utils.config import load_config
        cfg_port = get_browser_debug_port(load_config())
        if cfg_port:
            debug_port = cfg_port
    except Exception:
        pass

    def _probe(port):
        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3)
            return _json.loads(resp.read())
        except Exception:
            return None

    tabs = _probe(debug_port)
    if tabs is None:
        # Port may have shifted (prepare_browser_launch auto-selection).
        for alt in range(debug_port, debug_port + 6):
            if alt == debug_port:
                continue
            tabs = _probe(alt)
            if tabs is not None:
                debug_port = alt
                break
    if tabs is None:
        print_t('warning', f"   ⏱️ CDP cookie extraction failed (no browser on ports {debug_port}-{debug_port+5})")
        return None
    target = next((t for t in tabs if t.get('type') == 'page'), None)
    if not target:
        print_t('warning', "   ⏱️ CDP: no page target")
        return None

    async def _get():
        async with websockets.connect(target['webSocketDebuggerUrl']) as ws:
            await ws.send(_json.dumps({"id": 1, "method": "Network.getAllCookies", "params": {}}))
            resp_data = await asyncio.wait_for(ws.recv(), timeout=8)
            result = _json.loads(resp_data)
            cookies = result.get('result', {}).get('cookies', [])
            out = []
            for c in cookies:
                d = c.get('domain', '')
                if domain and domain not in d:
                    continue
                out.append(Cookie(
                    version=0, name=c['name'], value=c['value'],
                    port=None, port_specified=False,
                    domain=d, domain_specified=True, domain_initial_dot=d.startswith('.'),
                    path=c.get('path', '/'), path_specified=True,
                    secure=bool(c.get('secure')), expires=c.get('expires') or None,
                    discard=False, comment=None, comment_url=None, rest={}
                ))
            return out

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # Already inside an async context (e.g. Playwright/browser path):
        # run the websocket round-trip in a dedicated thread with its own loop
        # so we never collide with the caller's running loop.
        import threading
        result_holder = {}

        def _run_in_thread():
            th_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(th_loop)
            try:
                result_holder['result'] = th_loop.run_until_complete(_get())
            except Exception as e:
                result_holder['error'] = e
            finally:
                th_loop.close()

        t = threading.Thread(target=_run_in_thread)
        t.start()
        t.join(timeout=12)
        if 'error' in result_holder:
            print_t('warning', f"   ⏱️ CDP getAllCookies failed: {result_holder['error']}")
            return None
        return result_holder.get('result')
    else:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_get())
        except Exception as e:
            print_t('warning', f"   ⏱️ CDP getAllCookies failed: {e}")
            return None
        finally:
            loop.close()


def get_cookies_from_browser(domain, browser='chrome', user_data_dir=None):
    """Generic cookie extractor"""
    try:
        if browser == 'ego':
            # ego cookie store uses its own keychain secret; only CDP can read it.
            # 2026-08-08: CLI 原生通道优先（无需外部 CDP 端口），失败再走 CDP。
            cli_cookies = _get_cookies_via_ego_cli(domain)
            if cli_cookies:
                return cli_cookies
            return _get_cookies_via_cdp(domain)
        if user_data_dir:
            # 2026-08-30: 红线硬化 —— Agent 只用 ego。cookie 一律走上面的 ego CLI/CDP 通道，
            # 禁止再用 browser_cookie3 解 profile 文件（Chrome 150+ ABE 加密会触发
            # keychain 授权弹窗永久卡死；曾经硬编码读 Chrome Profile 1 越界）。
            # 若走到这里（browser != 'ego'），说明调用方配置错误，直接拒绝。
            print(f"⛔ 拒绝从浏览器 profile 文件读取 cookie (红线 2026-08-30, browser={browser})")
            return None

            # Custom Profile Mode
            # 1. Default/Network/Cookies (Newer)
            # 2. Default/Cookies (Older/Some variants)
            # 3. Profile 1/..., etc. (Assuming 'Default' profile for auto-browser)

            possible_paths = [
                os.path.join(user_data_dir, "Default", "Network", "Cookies"),
                os.path.join(user_data_dir, "Default", "Cookies"),
                os.path.join(user_data_dir, "Network", "Cookies"),
                os.path.join(user_data_dir, "Cookies"),
            ]

            cookie_file = None
            for p in possible_paths:
                if os.path.exists(p):
                    cookie_file = p
                    break

            if not cookie_file:
                # Warning but maybe try default loader if file not found? NO, strict in custom mode.
                print_t('error', f"⚠️ Custom profile cookie file not found in {user_data_dir}")
                print_t('error', f"   Checked: {possible_paths}")
                return None

            print_t('info', f"   🍪 Loading cookies from custom profile: {cookie_file}")

            # Use subprocess with kill-timeout for ABE-encrypted Chrome 150+ cookies
            _loader_code = f"""
import sys, json
sys.path.insert(0, '{os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))}')
import browser_cookie3
try:
    cj = browser_cookie3.chrome(cookie_file=r'{cookie_file}', domain_name='{domain}')
    result = [{{'name':c.name, 'value':c.value, 'domain':c.domain, 'path':c.path}} for c in cj]
    print(json.dumps(result))
except Exception as e:
    print(json.dumps({{'error': str(e)}}))
"""
            import subprocess
            try:
                proc = subprocess.run(
                    ['/opt/homebrew/bin/python3.11', '-c', _loader_code],
                    capture_output=True, text=True, timeout=12
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    import json as _json
                    data = _json.loads(proc.stdout.strip())
                    if isinstance(data, list):
                        from http.cookiejar import Cookie
                        from http import cookiejar as _cj2
                        cookies = []
                        for c in data:
                            cookies.append(Cookie(
                                version=0, name=c['name'], value=c['value'],
                                port=None, port_specified=False,
                                domain=c.get('domain',''), domain_specified=True, domain_initial_dot=c.get('domain','').startswith('.'),
                                path=c.get('path','/'), path_specified=True,
                                secure=False, expires=None, discard=True,
                                comment=None, comment_url=None, rest={}
                            ))
                        return cookies
            except subprocess.TimeoutExpired:
                print_t('warning', "   ⏱️ Cookie loading timed out (12s), falling back")
                return None
            except Exception as e:
                print_t('warning', f"   ⏱️ Cookie loader error: {e}")
                return None

        # Standard Mode (System Browser)
        loader = getattr(browser_cookie3, browser.lower(), None)
        if not loader:
            return browser_cookie3.load(domain_name=domain)

        return loader(domain_name=domain)
    except Exception as e:
        print(f"❌ Failed to extract cookies from {browser} (Custom path: {user_data_dir}): {e}")
        return None

def get_bilibili_cookies(browser='ego'):   # 2026-08-30: 原默认 'chrome' 会触发 Chrome keychain; 只允许 ego
    """
    Extract Bilibili cookies from browser and return in biliup-compatible format.
    Returns: {"cookie_info": {"cookies": [...]}}
    """
    try:
        from core.utils.config import load_config
        config = load_config()
        custom_profile = resolve_browser_profile_dir(config=config)

        cj = get_cookies_from_browser('.bilibili.com', browser, user_data_dir=custom_profile)
        if not cj:
            return None

        cookies_list = []
        required_keys = ['bili_jct', 'SESSDATA', 'DedeUserID']
        found_keys = set()

        for cookie in cj:
            # Standardize for biliup
            c_data = {
                'name': cookie.name,
                'value': cookie.value,
                'domain': cookie.domain,
                'path': cookie.path
            }
            cookies_list.append(c_data)

            if cookie.name in required_keys:
                found_keys.add(cookie.name)

        # Check if we have critical cookies
        if not all(k in found_keys for k in required_keys):
            print(f"⚠️ Warning: Missing critical Bilibili cookies (SESSDATA, bili_jct, DedeUserID).")
            print("Please ensure you are logged in to bilibili.com in your browser.")
            return None

        # Return in biliup-compatible format
        return {
            "cookie_info": {
                "cookies": cookies_list
            }
        }

    except Exception as e:
        print(f"❌ Error processing cookies: {e}")
        return None


from typing import Union

def validate_bilibili_cookies(cookies_list: Union[list, dict] = None, cookie_file: str = None, verbose: bool = False) -> tuple[bool, Union[str, None], Union[dict, None]]:
    """
    验证 Bilibili cookies 是否仍然有效
    """
    import requests

    # 从文件或列表获取 cookies
    if cookie_file:
        try:
            with open(cookie_file, 'r') as f:
                data = json.load(f)
                # 处理 biliup 格式
                if 'cookie_data' in data: # Some biliup versions
                    cookies_list = data['cookie_data']
                elif 'cookie_info' in data:
                    cookies_list = data['cookie_info'].get('cookies', [])
                elif isinstance(data, list):
                    cookies_list = data
                elif isinstance(data, dict):
                    cookies_list = data
                else:
                    cookies_list = {}
        except Exception as e:
            return False, f"读取 Cookie 文件失败: {e}", None

    if not cookies_list:
        return False, "Cookie 列表为空", None

    # Handle nested biliup format: {"cookie_info": {"cookies": [...]}}
    if isinstance(cookies_list, dict) and 'cookie_info' in cookies_list:
        cookies_list = cookies_list['cookie_info'].get('cookies', [])

    # 转换为 requests 格式
    cookies_dict = {}
    if isinstance(cookies_list, list):
        for c in cookies_list:
            name = c.get('name') or c.get('key')
            value = c.get('value')
            if name and value:
                cookies_dict[name] = value
    elif isinstance(cookies_list, dict):
        cookies_dict = cookies_list

    # 检查必要字段
    required = ['SESSDATA', 'bili_jct', 'DedeUserID']
    missing = [k for k in required if k not in cookies_dict]
    if missing:
        return False, f"缺少必要的 Cookie 字段: {', '.join(missing)}", None

    try:
        # 使用 B站 导航栏 API 验证登录状态
        response = requests.get(
            "https://api.bilibili.com/x/web-interface/nav",
            cookies=cookies_dict,
            headers={
                'User-Agent': get_user_agent()
            },
            timeout=10
        )

        data = response.json()

        if data.get('code') == 0:
            # 登录有效
            user_data = data.get('data', {})
            user_info = {
                'uid': user_data.get('mid'),
                'uname': user_data.get('uname'),
                'vip_type': user_data.get('vipType', 0),
                'level': user_data.get('level_info', {}).get('current_level', 0),
            }
            if verbose:
                print_t('workflow_bili_ok', user_info['uname'])
            return True, None, user_info
        elif data.get('code') == -101:
            return False, "Cookie 已过期，请重新登录 bilibili.com", None
        else:
            return False, f"验证失败: {data.get('message', '未知错误')}", None

    except requests.exceptions.Timeout:
        return False, "验证请求超时，请检查网络连接", None
    except requests.exceptions.RequestException as e:
        return False, f"验证请求失败: {e}", None
    except requests.exceptions.ConnectionError as e:
        return False, f"验证请求连接错误: {e}", None

def save_cookies_to_file(cookies_list, filepath):
    """Save cookies to a JSON file"""
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(cookies_list, f, indent=4)
        print_t('info', f"✅ Cookies saved to: {filepath}")
        return True
    except Exception as e:
        print_t('error', f"❌ Failed to save cookies: {e}")
        return False


# ============================================================
# Gemini Cookie 管理 (用于 AI 翻译/封面/字幕功能)
# ============================================================

# Cache for Gemini Client to prevent re-initialization
_GEMINI_CLIENT_INSTANCE = None
# gemini_webapi 1.0.1 GeminiClient uses __slots__ (no arbitrary attrs),
# so the creation event loop is tracked here instead of on the instance.
_GEMINI_CLIENT_CREATION_LOOP = None


async def _close_gemini_client_instance():
    global _GEMINI_CLIENT_INSTANCE, _GEMINI_CLIENT_CREATION_LOOP
    client = _GEMINI_CLIENT_INSTANCE
    _GEMINI_CLIENT_INSTANCE = None
    _GEMINI_CLIENT_CREATION_LOOP = None
    if not client:
        return

    close_fn = getattr(client, 'close', None)
    if not close_fn:
        return

    try:
        result = close_fn()
        if inspect.isawaitable(result):
            await result
    except Exception:
        pass

def get_gemini_cookies(browser: str = None) -> dict:
    """
    从浏览器提取 Gemini 所需的 cookies
    """
    from core.utils.config import load_config
    config = load_config()

    if browser is None:
        browser = config.get('browser', 'chrome')

    # 2026-08-09: 优先读 config/gemini_cookies.json（凭据真源，免探测浏览器/免开窗口）。
    # 旧版从未读这个文件——cookie 失效时 health_check 反复走 open_browser_for_login
    # 开新浏览器窗口（曾堆积 23+ 窗口）。文件新鲜（mtime < 7 天）直接用；
    # 过期则自动从 ego 浏览器 CLI 刷新（refresh_gemini_cookies.sh），不开窗口。
    try:
        gemini_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            'config', 'gemini_cookies.json'
        )
        if os.path.exists(gemini_file):
            import time as _time
            age = _time.time() - os.path.getmtime(gemini_file)
            if age < 7 * 86400:  # 7 天内
                with open(gemini_file, 'r') as f:
                    cached = json.load(f)
                sid = cached.get('secure_1psid', '') or cached.get('__Secure-1PSID', '')
                sidts = cached.get('secure_1psidts', '') or cached.get('__Secure-1PSIDTS', '')
                if len(sid) >= 20 and len(sidts) >= 20:
                    return {'secure_1psid': sid, 'secure_1psidts': sidts}
            # 文件过期（>7 天）→ 自动刷新：从 ego 浏览器 CLI 提取新 cookie
            try:
                import subprocess as _sp
                refresh_script = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    '..', '.hermes', 'scripts', 'refresh_gemini_cookies.sh'
                )
                if not os.path.exists(refresh_script):
                    refresh_script = os.path.expanduser('~/.hermes/scripts/refresh_gemini_cookies.sh')
                if os.path.exists(refresh_script):
                    _sp.run(['bash', refresh_script], capture_output=True, text=True, timeout=90)
                    if os.path.exists(gemini_file):
                        with open(gemini_file, 'r') as f:
                            refreshed = json.load(f)
                        sid = refreshed.get('secure_1psid', '')
                        sidts = refreshed.get('secure_1psidts', '')
                        if len(sid) >= 20 and len(sidts) >= 20:
                            return {'secure_1psid': sid, 'secure_1psidts': sidts}
            except Exception:
                pass
    except Exception:
        pass

    custom_profile = resolve_browser_profile_dir(config=config)

    cj = get_cookies_from_browser('.google.com', browser, user_data_dir=custom_profile)

    # 2026-08-30: 删除 Chrome Profile 1 fallback —— 红线：Agent 只用 ego，Chrome 是用户个人浏览器。
    # 这里曾硬编码读 Chrome Profile 1 cookie，导致 keychain 授权卡死 + 越界。ego 缺失就不取，回退 cookies.json。
    if not cj:
        # Fallback: try cookies.json (CDP-extracted fallback file)
        cookie_file_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            'cookies.json'
        )
        if os.path.exists(cookie_file_path):
            try:
                with open(cookie_file_path, 'r') as f:
                    cached = json.load(f)
                cached_cookies = cached.get('cookie_info', {}).get('cookies', [])
                secure_1psid = None
                secure_1psidts = None
                for c in cached_cookies:
                    if c['name'] == '__Secure-1PSID' and '.google.com' in c.get('domain', ''):
                        secure_1psid = c['value']
                    elif c['name'] == '__Secure-1PSIDTS':
                        secure_1psidts = c['value']
                if secure_1psid:
                    print("⚠️ 浏览器 Cookie 获取失败，使用 cookies.json 中的 Gemini cookies")
                    return {
                        'secure_1psid': secure_1psid,
                        'secure_1psidts': secure_1psidts or ''
                    }
            except Exception:
                pass

        # Fallback: CDP cookie extraction (Chrome 150+ ABE encryption bypass)
        # browser_cookie3 can't decrypt Chrome 150+ ABE-encrypted cookies,
        # but CDP via the running Chrome instance returns decrypted values.
        try:
            debug_port = config.get('browser_debug_port', 39222)
            import urllib.request, json as _json
            resp = urllib.request.urlopen(f"http://127.0.0.1:{debug_port}/json", timeout=5)
            tabs = _json.loads(resp.read())
            # Find a page target for CDP websocket
            target = next((t for t in tabs if t.get('type') == 'page'), None)
            if target:
                ws_url = target['webSocketDebuggerUrl']
                # Use websockets to call Network.getAllCookies via CDP
                try:
                    import asyncio, websockets
                    async def _cdp_get_cookies():
                        async with websockets.connect(ws_url) as ws:
                            cmd = _json.dumps({"id": 1, "method": "Network.getAllCookies", "params": {}})
                            await ws.send(cmd)
                            resp_data = await asyncio.wait_for(ws.recv(), timeout=5)
                            result = _json.loads(resp_data)
                            cookies_list = result.get('result', {}).get('cookies', [])
                            secure_1psid = None
                            secure_1psidts = None
                            for c in cookies_list:
                                if c['name'] == '__Secure-1PSID' and '.google.com' in c.get('domain', ''):
                                    secure_1psid = c['value']
                                elif c['name'] == '__Secure-1PSIDTS':
                                    secure_1psidts = c['value']
                            return secure_1psid, secure_1psidts
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    cdp_sid, cdp_sidts = loop.run_until_complete(_cdp_get_cookies())
                    loop.close()
                    if cdp_sid:
                        print(f"✅ 通过 CDP (端口 {debug_port}) 获取 Gemini 凭证成功")
                        return {
                            'secure_1psid': cdp_sid,
                            'secure_1psidts': cdp_sidts or ''
                        }
                except ImportError:
                    pass  # websockets not installed
                except Exception as e:
                    print(f"⚠️ CDP cookie extraction failed: {e}")
        except Exception:
            pass

        msg = f"❌ 无法从 {browser} 浏览器加载 cookies"
        if custom_profile:
            msg += f" (Profile: {custom_profile})"
        raise ValueError(msg)

    secure_1psid = None
    secure_1psidts = None

    for cookie in cj:
        if cookie.name == '__Secure-1PSID':
            secure_1psid = cookie.value
        elif cookie.name == '__Secure-1PSIDTS':
            secure_1psidts = cookie.value

    if not secure_1psid:
        raise ValueError(
            f"❌ 未找到 __Secure-1PSID cookie。\n"
            f"   请确保已在 {browser} 浏览器中登录 Gemini (https://gemini.google.com)"
        )

    return {
        'secure_1psid': secure_1psid,
        'secure_1psidts': secure_1psidts
    }


from typing import Union

def validate_gemini_cookies(cookies: dict, verbose: bool = False, proxy: str = None,
                            fallback_probe: bool = True) -> tuple[bool, Union[str, None], Union[str, None]]:
    """
    验证 Gemini cookies 是否仍然有效，并尝试获取用户信息
    Returns: (is_valid, error_msg, email)
    """
    import requests
    import re

    if not cookies or not cookies.get('secure_1psid'):
        return False, "Cookie 为空或缺少 __Secure-1PSID", None

    if proxy is None:
        try:
            from core.utils.config import load_config
            proxy = load_config().get('proxy')
        except ImportError:
            pass

    proxies = None
    if proxy:
        proxies = {
            'http': proxy,
            'https': proxy
        }

    header_base = {
        'User-Agent': get_user_agent(),
    }

    cookie_dict = {
        '__Secure-1PSID': cookies['secure_1psid'],
        '__Secure-1PSIDTS': cookies.get('secure_1psidts', ''),
    }

    try:
        if verbose:
            print("🔐 正在验证 Gemini Cookie...")

        # 1. 验证有效性 (尝试访问主页)
        # 之前使用 batchexecute 容易出现 405 或参数错误，改用模拟浏览器访问主页
        response = requests.get(
            "https://gemini.google.com/app",
            cookies=cookie_dict,
            headers=header_base,
            timeout=20,
            proxies=proxies,
            allow_redirects=True
        )

        # 风险检测 / CAPTCHA 拦截
        if '/sorry/index' in response.url:
             return False, "触发 Google 风险检测 (CAPTCHA)，请在浏览器中完成验证", None

        if response.status_code != 200:
             return False, f"验证失败: HTTP {response.status_code}", None

        # 检查是否跳转到了登录页
        if 'accounts.google.com' in response.url or 'ServiceLogin' in response.url:
             return False, "Cookie 已过期，请重新登录 Gemini", None

        # 检查 SNlM0e token 是否存在（200 但未登录的页面不含此 token）
        import re
        if not re.search(r'"SNlM0e":"', response.text) and fallback_probe:
            # 2026-08-17: Google 改版后初始 HTML 经常不返回 SNlM0e（requests TLS 指纹 vs 真实浏览器差异），
            # 但 gemini_webapi 走 batchexecute API 认证独立于 HTML token，完全可用（今早 07:45 init 成功实证）。
            # 因此 HTML 查不到时不能直接判死 —— 用 gemini_webapi 真实初始化作 fallback 验证。
            try:
                import asyncio
                from core.utils.gemini_compat import build_gemini_client_kwargs, apply_gemini_compat
                from gemini_webapi import GeminiClient

                async def _probe():
                    apply_gemini_compat(GeminiClient)
                    kwargs = build_gemini_client_kwargs(GeminiClient, cookies, proxy)
                    client = GeminiClient(**kwargs)
                    await asyncio.wait_for(client.init(auto_refresh=False), timeout=30)
                    return True

                probe_ok = asyncio.run(_probe())
                if probe_ok:
                    return True, None, None
            except Exception as probe_err:
                # 2026-09-01: 打印完整异常（类型+信息），避免"probe failed:" 空信息无法排查
                print(f"⚠️ Gemini webapi probe failed: [{type(probe_err).__name__}] {probe_err}")
            return False, "Cookie 已过期（页面无 SNlM0e token），请重新登录 Gemini", None

        # 2. 尝试获取邮箱
        email = None
        try:
            html = response.text
            # 尝试 data-email
            match = re.search(r'data-email="([^"]+)"', html)
            if match:
                email = match.group(1)
            else:
                # 尝试通用 Gmail 匹配
                emails = re.findall(r'"([a-zA-Z0-9._%+-]+@gmail\.com)"', html)
                if emails:
                    email = emails[0]
        except Exception:
            pass # 获取邮箱是非核心功能，失败不影响验证结果

        return True, None, email

    except requests.exceptions.SSLError as e:
         # SSL 错误通常意味着被 reset 或拦截
         err_str = str(e)
         if "UNEXPECTED_EOF_WHILE_READING" in err_str or "certificate verify failed" in err_str:
             return False, "SSL 连接被拦截/中断 (可能触发了 Google 风险检测)，请检查浏览器是否也需要验证", None
         return False, f"SSL 验证失败: {e}", None

    except requests.exceptions.Timeout:
        return False, "验证请求超时，请检查网络连接", None
    except requests.exceptions.RequestException as e:
        # 捕获 Redirect 导致的 Max retries exceeded (通常是跳转到 /sorry/index 无限循环或阻断)
        if "/sorry/index" in str(e):
             return False, "触发 Google 风险检测 (CAPTCHA)，请在浏览器中完成验证", None
        return False, f"验证请求失败: {e}", None


async def get_gemini_client(validate: bool = True, verbose: bool = True, force_refresh: bool = False):
    """
    获取已初始化的 GeminiClient 实例 (Singleton)

    Args:
        validate: 是否验证 Cookie (仅首次初始化时有效)
        verbose: 是否打印状态信息
        force_refresh: 是否强制重新创建实例
    """
    global _GEMINI_CLIENT_INSTANCE, _GEMINI_CLIENT_CREATION_LOOP

    # 1. 检查单例是否存在且在当前 Event Loop 中有效
    if _GEMINI_CLIENT_INSTANCE and not force_refresh:
        try:
            current_loop = asyncio.get_running_loop()
            creation_loop = _GEMINI_CLIENT_CREATION_LOOP

            if creation_loop and (creation_loop.is_closed() or creation_loop is not current_loop):
                 if verbose:
                     print("🔄 Gemini Client Event Loop changed/closed. Refreshing instance...")
                 _GEMINI_CLIENT_INSTANCE = None
            else:
                # 实例有效，直接返回，跳过后续所有初始化和验证逻辑
                return _GEMINI_CLIENT_INSTANCE
        except Exception:
            _GEMINI_CLIENT_INSTANCE = None

    from gemini_webapi import GeminiClient
    from core.utils.gemini_compat import apply_gemini_compat, build_gemini_client_kwargs
    from core.utils.config import CONFIG

    # Ensure httpx 0.28 proxy= compat shim is applied even when entrypoint is
    # NOT main.py (e.g. ai-daily subprocess run_upload). Idempotent.
    try:
        apply_gemini_compat(GeminiClient)
    except Exception:
        pass

    proxy = CONFIG.get('proxy')
    cookies = get_gemini_cookies()

    # 2. 只有在没有实例或强制刷新时才进行验证
    if validate:
        # 将验证超时从 20s 降到 8s，防止初始化阶段卡死
        # fallback_probe=False: 异步上下文中 probe 的 asyncio.run 会与当前 loop 冲突，
        # 真正的认证由下方 GeminiClient.init() 完成（HTML SNlM0e 检查自 2026-08-17 起经常误报）。
        is_valid, error, _ = validate_gemini_cookies(cookies, verbose=verbose, proxy=proxy,
                                                     fallback_probe=False)
        if not is_valid:
            # 2026-08-14 修复: cookie 失效 → 自动刷新一次再验证。
            # 背景: config/gemini_cookies.json 7 天内"新鲜"就直接用, 但 SNlM0e token
            # 可能提前失效 → Web API 翻译/生成静默失败 → 英文标题被当作"翻译成功"直接上传。
            # 刷新脚本 (refresh_gemini_cookies.sh) 走 Chrome 默认 profile (browser_cookie3),
            # 失败 fallback ego CLI, 均无需人工。刷新后仍失败才抛错。
            import subprocess as _sp
            refresh_script = os.path.expanduser('~/.hermes/scripts/refresh_gemini_cookies.sh')
            if os.path.exists(refresh_script):
                try:
                    _sp.run(['bash', refresh_script], capture_output=True, text=True, timeout=90)
                    cookies = get_gemini_cookies()
                    is_valid, error, _ = validate_gemini_cookies(cookies, verbose=verbose, proxy=proxy,
                                                                 fallback_probe=False)
                except Exception as e:
                    print(f"⚠️ Gemini cookie 自动刷新失败: {e}")
            if not is_valid:
                # 2026-08-17: validate 的 HTML 检查可能因 Google 改版误报（requests TLS 指纹拿不到
                # 完整页面 token），但 gemini_webapi 的 batchexecute 认证独立于 HTML token。
                # 先尝试真实初始化，成功即 cookie 有效（今早 07:45 实证路径）。
                try:
                    from core.utils.gemini_compat import build_gemini_client_kwargs, apply_gemini_compat
                    from gemini_webapi import GeminiClient
                    apply_gemini_compat(GeminiClient)
                    kwargs = build_gemini_client_kwargs(GeminiClient, cookies, proxy)
                    _probe_client = GeminiClient(**kwargs)
                    await asyncio.wait_for(_probe_client.init(auto_refresh=False), timeout=30)
                    _GEMINI_CLIENT_INSTANCE = _probe_client
                    _GEMINI_CLIENT_CREATION_LOOP = asyncio.get_running_loop()
                    if verbose:
                        print("✅ Gemini 客户端初始化成功 (HTML 校验误报绕过)")
                    return _probe_client
                except Exception as probe_error:
                    print(f"⚠️ Gemini probe init failed: {probe_error}")
                # 如果验证失败，抛出错误前尝试清除单例
                _GEMINI_CLIENT_INSTANCE = None
                raise ValueError(f"❌ Gemini Cookie 无效: {error}")

    # 3. 初始化新实例
    try:
        if verbose:
            print_t('info', "🔐 初始化 Gemini 客户端...")

        client = GeminiClient(
            **build_gemini_client_kwargs(GeminiClient, cookies, proxy)
        )

        # 增加 init 超时保护，防止无限等待
        try:
            await asyncio.wait_for(client.init(auto_refresh=False), timeout=30.0)
        except asyncio.TimeoutError:
            raise ValueError("Gemini 客户端初始化请求超时 (30s)，请检查网络或代理设置")

        # 标记实例所属的 Loop
        _GEMINI_CLIENT_CREATION_LOOP = asyncio.get_running_loop()

        _GEMINI_CLIENT_INSTANCE = client
        return client

    except Exception as e:
        _GEMINI_CLIENT_INSTANCE = None
        raise ValueError(f"❌ Gemini 初始化失败: {e}")


def close_gemini_client_sync():
    """Close the shared Gemini client from sync cleanup paths."""
    try:
        asyncio.run(_close_gemini_client_instance())
    except RuntimeError:
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(_close_gemini_client_instance())
            loop.close()
        except Exception:
            pass

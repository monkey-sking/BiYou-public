#!/usr/bin/env python3
"""
B站/YouTube/Gemini/NotebookLM 全方位健康检查 + 自动修复 + 静默报警。

模式:
  python3 scripts/health_check.py          仅检查，有问题推送到 Telegram
  python3 scripts/health_check.py --fix    检查 + 自动修复可修问题
  python3 scripts/health_check.py --list   只列出状态，不报不修

完全本地执行，零 token 消耗。
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ── 时区 ──
TZ = timezone(timedelta(hours=8))

# ── 路径 ──
HOME = Path.home()
BIYOU = Path("${HOME}/biyou")
BIYOU_LOG = BIYOU / "logs" / "biyou.log"
DOWNLOADS = Path("${HOME}/Downloads/Videos")
AI_DAILY_OUTPUT = Path("${HOME}/ai-daily/output")
TRACKER_DB = HOME / ".hermes" / "biyou" / "biyou_tracker.db"
ACTIVE_MISSIONS = BIYOU / "data" / "active_missions.json"
BYWORKFLOW_PROFILE = Path.home() / "Library/Application Support/Chrome/Profile 1"  # yt-dlp YouTube cookie 源
EGO_PROFILE = Path.home() / "Library/Application Support/Citro Labs/ego lite"  # BiYou 浏览器本体 (ego)
# 2026-08-08: 路径/端口统一从 browser_registry 读（单一真源），上面保留兼容旧引用
try:
    import sys as _sys
    _sys.path.insert(0, str(Path.home() / ".hermes" / "scripts"))
    import browser_registry as _br
    EGO_PROFILE = Path(_br.PROFILE)
    EGO_PORT = _br.DEBUG_PORT
except Exception:
    EGO_PORT = 39222
STATE_FILE = HOME / ".hermes" / "tmp" / "health_check_state.json"
CLASH_API = "http://127.0.0.1:9090"
PROXY = "http://127.0.0.1:7890"
MAX_CONSECUTIVE_FAILURES = 3

_AUTO_FIXED = []   # [(category, action)]
_NEED_USER = []    # [(category, msg, guide)]
_PROBLEMS = []     # [(category, msg, count)]


# ═══════════════════════════════════════════════════════════════
#  状态管理
# ═══════════════════════════════════════════════════════════════

def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def fail(category: str, msg: str):
    state = _load_state()
    key = f"{category}:{msg[:50]}"
    count = state.get(key, 0) + 1
    state[key] = count
    _save_state(state)
    if count >= MAX_CONSECUTIVE_FAILURES:
        _PROBLEMS.append((category, msg, count))

def ok(category: str):
    state = _load_state()
    keys = [k for k in state if k.startswith(f"{category}:")]
    for k in keys:
        del state[k]
    _save_state(state)

def auto_fixed(category: str, action: str):
    _AUTO_FIXED.append((category, action))

def need_user(category: str, msg: str, guide: str):
    _NEED_USER.append((category, msg, guide))


# ═══════════════════════════════════════════════════════════════
#  修复工具
# ═══════════════════════════════════════════════════════════════

def _clash_api(method: str, path: str, data: dict = None) -> dict:
    """调 Clash API。"""
    url = f"{CLASH_API}{path}"
    headers = {"Content-Type": "application/json"}
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=5)
        elif method == "PUT":
            r = requests.put(url, headers=headers, json=data, timeout=5)
        else:
            return {}
        return r.json() if r.text else {}
    except Exception:
        return {}

def _switch_clash_node(group: str = "美国节点") -> bool:
    """切到下一个可用美国节点。"""
    try:
        proxies = _clash_api("GET", f"/proxies/{group}")
        now = proxies.get("now", "")
        all_nodes = proxies.get("all", [])
        if not all_nodes:
            return False
        idx = all_nodes.index(now) + 1 if now in all_nodes else 0
        if idx >= len(all_nodes):
            idx = 0
        _clash_api("PUT", f"/proxies/{group}", {"name": all_nodes[idx]})
        time.sleep(2)
        auto_fixed("Clash节点", f"从 {now} 切换到 {all_nodes[idx]}")
        return True
    except Exception:
        return False

def _test_google_reachable() -> bool:
    """测试能不能连上 Google。"""
    try:
        r = requests.get("https://www.google.com", proxies={"http": PROXY, "https": PROXY}, timeout=8)
        return r.status_code == 200
    except Exception:
        return False

def _retry(fn, max_attempts=3, delay=5):
    """通用重试：每次间隔 delay 秒。"""
    for i in range(max_attempts):
        try:
            result = fn()
            if result:
                return True
        except Exception:
            pass
        if i < max_attempts - 1:
            time.sleep(delay)
    return False


# ═══════════════════════════════════════════════════════════════
#  检查项
# ═══════════════════════════════════════════════════════════════

def check_bilibili_cookies(fix=False):
    category = "B站cookie"
    try:
        sys.path.insert(0, str(BIYOU))
        from core.services.bili_api import get_bilibili_credentials
        creds = get_bilibili_credentials()
        if not creds:
            fail(category, "get_bilibili_credentials 返回空")
            return
        # creds 是 biliup 嵌套格式: {"cookie_info": {"cookies": [...]}}
        # 也可能直接是 requests cookie dict
        sess = ""
        cookie_dict = {}
        if isinstance(creds, dict):
            ci = creds.get("cookie_info", {})
            if isinstance(ci, dict):
                cookies = ci.get("cookies", [])
                if isinstance(cookies, list):
                    for c in cookies:
                        if isinstance(c, dict):
                            cookie_dict[c.get("name", "")] = c.get("value", "")
            else:
                # 已经是扁平 dict
                cookie_dict = creds
            sess = cookie_dict.get("SESSDATA", "")
        if not sess:
            fail(category, "SESSDATA 为空 (检查嵌套格式)")
            if fix:
                need_user(category, "B站登录过期",
                    "1. 启动 byworkflow Chrome\n"
                    "2. 打开 https://passport.bilibili.com/login\n"
                    "3. 扫码登录\n"
                    "4. 再跑一次 health_check.py --fix 确认")
            return
        r = requests.get(
            "https://api.bilibili.com/x/web-interface/nav",
            cookies=cookie_dict,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                "Referer": "https://www.bilibili.com/",
            },
            timeout=10,
        )
        data = r.json()
        if data.get("code") != 0 or not data.get("data", {}).get("isLogin", False):
            fail(category, "未登录")
            if fix:
                need_user(category, "B站登录过期",
                    "打开 https://passport.bilibili.com/login 扫码登录")
            return
        ok(category)
    except Exception as e:
        fail(category, f"异常: {e}")


def check_gemini_cookies(fix=False):
    category = "Gemini cookie"
    try:
        sys.path.insert(0, str(BIYOU))
        from core.utils.cookie import get_gemini_cookies
        # 2026-08-08: 不硬编码 browser，走配置 (ego) — 规范见 browser_registry.py
        gc = get_gemini_cookies()
        sid = gc.get("secure_1psid", "")
        sidts = gc.get("secure_1psidts", "")
        if not sid or not sidts:
            fail(category, "缺少必要Gemini cookie")
            if fix:
                need_user(category, "Gemini 登录过期",
                    "打开 https://gemini.google.com 重新登录")
            return
        # Google 的 cookie 验证不能依赖 SNlM0e HTTP 请求——
        # 即使 cookie 有效，页面也可能显示为未登录，点登录按钮才触发客户端检测
        # 所以只需要确认 cookie 值存在、看起来合法即可
        if len(sid) < 20 or len(sidts) < 20:
            fail(category, f"cookie 值异常 (sid={len(sid)}ch, sidts={len(sidts)}ch)")
            if fix:
                need_user(category, "Gemini 登录过期",
                    "打开 https://gemini.google.com 重新登录")
            return
        # 2026-08-24 增强: ego 运行时验证共享 Google 会话活性（Gemini/NotebookLM 共用）。
        # 不用 SNlM0e HTTP 校验（迁移后不可靠），走 refresh_gemini_cookies.sh 的 ego 提取链路。
        # 输出含 "✅ refreshed from" = ego 会话真实存在; "复用现有缓存" = ego 里已无登录态。
        try:
            sys.path.insert(0, str(Path.home() / ".hermes" / "scripts"))
            from browser_registry import ego_is_running
            if ego_is_running():
                r = subprocess.run(
                    ["bash", str(Path.home() / ".hermes" / "scripts" / "refresh_gemini_cookies.sh")],
                    capture_output=True, text=True, timeout=120)
                out = (r.stdout + r.stderr)
                if "✅ refreshed from" in out:
                    ok(category)
                    return
                if "复用现有缓存" in out:
                    fail(category, "ego 无 Google 登录会话(Gemini cookie 走缓存,需重新登录)")
                    if fix:
                        need_user(category, "Gemini 会话丢失",
                            "打开 ego 浏览器访问 https://gemini.google.com 重新登录")
                    return
                fail(category, "ego Google 会话提取失败")
                if fix:
                    need_user(category, "Gemini 会话检查失败",
                        "打开 ego 浏览器访问 https://gemini.google.com 确认登录态")
                return
        except Exception as e:
            fail(category, f"ego 会话检查异常: {e}")
            return
        ok(category)
    except Exception as e:
        fail(category, f"异常: {e}")


def check_youtube_connectivity(fix=False):
    category = "YouTube连通性"
    for attempt in range(3 if fix else 1):
        try:
            # 2026-08-08: 改用 ego CDP 导出的 cookie 文件（Chrome Profile 1 已退役，
            # 规范见 ~/.hermes/scripts/browser_registry.py 单一真源）
            yt_cookies = Path.home() / ".hermes" / "biyou" / "yt_cookies.txt"
            cookies_args = ["--cookies", str(yt_cookies)] if yt_cookies.exists() else []
            if not cookies_args:
                # fallback: 从 ego CLI 刷新（refresh_yt_cookies.sh，2026-08-08 改 CLI 零窗口版）
                # 2026-08-08: 浏览器操作审计日志
                try:
                    import sys as _sys
                    _sys.path.insert(0, str(Path.home() / ".hermes" / "scripts"))
                    import browser_activity_log
                    browser_activity_log.log_browser(
                        "get_cookies", "youtube.com",
                        "health_check.check_youtube_connectivity 触发 refresh_yt_cookies.sh",
                        extra={"script": "BiYou health_check.py"})
                except Exception:
                    pass
                subprocess.run(
                    ["bash", str(Path.home() / ".hermes" / "scripts" / "refresh_yt_cookies.sh")],
                    capture_output=True, text=True, timeout=60,
                )
                cookies_args = ["--cookies", str(yt_cookies)] if yt_cookies.exists() else []
            result = subprocess.run(
                ["/opt/homebrew/bin/yt-dlp", "--proxy", PROXY] + cookies_args +
                 ["--dump-json", "--no-download", "--playlist-items", "1",
                  "https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                ok(category)
                if attempt > 0:
                    auto_fixed(category, f"第{attempt+1}次重试成功")
                return
            # 失败→切节点再试
            if fix and attempt < 2:
                _switch_clash_node()
                time.sleep(3)
        except subprocess.TimeoutExpired:
            if fix and attempt < 2:
                _switch_clash_node()
                time.sleep(3)
    fail(category, "yt-dlp 全部重试失败")
    if fix:
        need_user(category, "YouTube 不可达",
            "Clash 节点可能全部失效，手动检查代理状态:\n"
            "python3 ~/.hermes/scripts/clash_manager.py test")


def check_notebooklm_cookies(fix=False):
    category = "NotebookLM cookie"
    for attempt in range(2 if fix else 1):
        try:
            result = subprocess.run(
                ["/opt/homebrew/bin/python3.11",
                 str(HOME / ".hermes/scripts/export_nblm_cookies.py"),
                 "--source", "auto", "--dry-run"],
                capture_output=True, text=True, timeout=30,
            )
            output = result.stdout + result.stderr
            # 2026-08-08: export_nblm_cookies.py 成功输出是 "✅ Valid cookies" / "All key cookies present"
            if "Would save" in output or "OK" in output or "Valid cookies" in output or "All key cookies present" in output:
                ok(category)
                if attempt > 0:
                    auto_fixed(category, f"第{attempt+1}次尝试成功")
                return
            if fix and attempt == 0:
                # 可能浏览器没开，启动重试（2026-08-08: 从 browser_registry 读配置，不硬编码）
                try:
                    import sys as _sys
                    _sys.path.insert(0, str(Path.home() / ".hermes" / "scripts"))
                    import browser_registry as _br
                    subprocess.Popen(
                        [_br.BIN,
                         f"--remote-debugging-port={_br.DEBUG_PORT}",
                         "--no-first-run", "--no-default-browser-check",
                         "about:blank"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                except Exception:
                    subprocess.Popen(
                        ["/Applications/ego lite.app/Contents/MacOS/ego lite",
                         "--remote-debugging-port=39222",
                         "--no-first-run", "--no-default-browser-check",
                         "about:blank"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                time.sleep(8)
        except Exception:
            pass
    fail(category, "NotebookLM cookie 获取失败")


def check_biyou_pipeline(fix=False):
    category = "BiYou管道卡住"
    try:
        result = subprocess.run(
            ["pgrep", "-f", "run-by.sh|main.py"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            pids = result.stdout.strip().split("\n")
            for pid in pids:
                if not pid.strip():
                    continue
                ps = subprocess.run(
                    ["ps", "-o", "etime=", "-p", pid.strip()],
                    capture_output=True, text=True, timeout=5,
                )
                etime = ps.stdout.strip()
                if etime and ("-" in etime or etime > "30:00"):
                    if fix:
                        subprocess.run(["kill", pid.strip()], timeout=5)
                        auto_fixed(category, f"杀掉卡住进程 PID {pid} ({etime})")
                    else:
                        fail(category, f"PID {pid} 卡住 {etime}")
                        return
        ok(category)
    except Exception:
        pass


def check_disk_usage(fix=False):
    category = "磁盘"
    try:
        result = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5)
        lines = result.stdout.strip().split("\n")
        if len(lines) >= 2:
            used_pct = int(lines[1].split()[4].replace("%", ""))
            if used_pct >= 90:
                fail(category, f"用量 {used_pct}%")
            else:
                ok(category)
    except Exception:
        pass


def check_recent_biyou_errors(fix=False):
    category = "BiYou日志错误"
    try:
        if not BIYOU_LOG.exists():
            fail(category, "日志文件不存在")
            return
        # 2026-08-08: 只统计最近 1000 行（避免历史累计 ERROR 误报）
        result = subprocess.run(
            ["bash", "-c", f"tail -1000 '{BIYOU_LOG}' | grep -c ERROR"],
            capture_output=True, text=True, timeout=5,
        )
        error_count = int(result.stdout.strip() or 0)
        if error_count > 10:
            fail(category, f"最近 {error_count} 个 ERROR")
        else:
            ok(category)
    except Exception:
        pass


def check_active_missions(fix=False):
    category = "活动过期"
    try:
        if not ACTIVE_MISSIONS.exists():
            fail(category, "active_missions.json 不存在")
            if fix:
                subprocess.run(
                    ["/opt/homebrew/bin/python3.11", "scripts/sync_bili_activities.py"],
                    cwd=str(BIYOU), timeout=30,
                )
                if ACTIVE_MISSIONS.exists():
                    auto_fixed(category, "重新同步活动列表")
                    return
            return
        with open(ACTIVE_MISSIONS) as f:
            missions = json.load(f)
        # 只报那些原本有活动但突然变成0的，从无到无的跳过
        expired = [k for k, v in missions.items()
                   if v.get("mission_id", 0) == 0
                   and v.get("status") not in ("no_activity", "inactive")]
        changed = False
        for k in missions:
            if missions[k].get("mission_id", 0) == 0 and missions[k].get("status") not in ("no_activity", "inactive"):
                missions[k]["status"] = "no_activity"
                changed = True
        # 2026-08-10: 修 bug —— 旧代码只改内存不写回文件，导致同一个
        # "无活动"每次检查都报（累计20次）。现在把状态写回，从无到无不再报。
        if changed:
            try:
                with open(ACTIVE_MISSIONS, "w") as f:
                    json.dump(missions, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
        if expired:
            fail(category, f"无活动: {', '.join(expired)}")
            if fix:
                subprocess.run(
                    ["/opt/homebrew/bin/python3.11", "scripts/sync_bili_activities.py"],
                    cwd=str(BIYOU), timeout=30,
                )
                auto_fixed(category, "重新同步活动列表")
            return
        ok(category)
    except Exception as e:
        fail(category, f"异常: {e}")


def check_tracker_stale(fix=False):
    category = "处理卡住"
    try:
        if not TRACKER_DB.exists():
            return
        conn = sqlite3.connect(str(TRACKER_DB))
        cur = conn.execute(
            "SELECT video_id, title, processing_at FROM videos "
            "WHERE status='processing' AND processing_at IS NOT NULL"
        )
        stale = []
        for row in cur.fetchall():
            vid, title, proc_at = row
            if proc_at:
                try:
                    proc_time = datetime.fromisoformat(proc_at.replace("Z", "+00:00"))
                    if (datetime.now(timezone.utc) - proc_time).total_seconds() > 7200:
                        stale.append((vid, title[:30]))
                except ValueError:
                    pass
        conn.close()
        if stale:
            fail(category, f"{len(stale)}个视频卡>2h")
            if fix:
                conn = sqlite3.connect(str(TRACKER_DB))
                for vid, title in stale:
                    conn.execute(
                        "UPDATE videos SET status='discovered', error='auto_clean_timeout' WHERE video_id=?",
                        (vid,),
                    )
                conn.commit()
                conn.close()
                auto_fixed(category, f"释放 {len(stale)} 个卡住视频为 discovered")
            return
        ok(category)
    except Exception:
        pass


def check_daily_artifact(fix=False):
    category = "AI日报产出"
    today = datetime.now(TZ).strftime("%m%d")
    expected = [
        f"AI日报-{today}.mp4",
        f"AI日报-{today}.mp3",
        f"AI日报-{today}_metadata.json",
    ]
    missing = [f for f in expected if not (AI_DAILY_OUTPUT / f).exists()]
    if len(missing) >= 2:
        fail(category, f"今天 {today} 缺少: {', '.join(missing)}")
    else:
        ok(category)


# ═══════════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="BiYou 健康检查 + 自动修复")
    parser.add_argument("--fix", action="store_true", help="检查后自动修复可修问题")
    parser.add_argument("--list", action="store_true", help="只列出状态，不检查不修")
    args = parser.parse_args()

    if args.list:
        print("📋 健康检查项目清单:")
        checks = [
            "🔑 B站 cookie       — 调 API 检查登录态  | 需用户扫码修复",
            "🔑 Gemini cookie     — SNlM0e token 检查  | 需用户浏览器登录",
            "🔑 YouTube 连通性    — yt-dlp 测试        | 自动切 Clash 节点",
            "🔑 NotebookLM cookie — CDP 提取测试       | 自动重试+启Chrome",
            "📹 BiYou 管道卡住    — 进程运行时间       | 自动杀掉",
            "💾 磁盘用量          — df -h /             | 仅报告",
            "❌ 日志错误数        — grep ERROR          | 仅报告",
            "🏷️ B站活动状态       — mission_id >0 检查  | 自动重新同步",
            "⏳ tracker 卡住       — processing>2h      | 自动释放为discovered",
            "📰 AI日报产出        — 检查当天文件       | 仅报告",
        ]
        for c in checks:
            print(f"  {c}")
        return

    mode = "检查+修复" if args.fix else "仅检查"
    print(f"🧪 BiYou 健康检查 ({mode}) — {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}")
    print("=" * 55)

    check_fns = [
        ("🔑 B站 cookie", check_bilibili_cookies),
        ("🔑 Gemini cookie", check_gemini_cookies),
        ("🔑 YouTube 连通性", check_youtube_connectivity),
        ("🔑 NotebookLM cookie", check_notebooklm_cookies),
        ("📹 BiYou 管道", check_biyou_pipeline),
        ("💾 磁盘用量", check_disk_usage),
        ("❌ BiYou 日志错误", check_recent_biyou_errors),
        ("🏷️ B站活动状态", check_active_missions),
        ("⏳ 处理卡住", check_tracker_stale),
        # 2026-08-24: AI日报停更，产出检查停用（check_daily_artifact 保留备用）
    ]

    for name, fn in check_fns:
        fn(fix=args.fix)

    # ── 输出 ──
    lines = []
    if _AUTO_FIXED:
        lines.append(f"\n✅ 自动修复 {len(_AUTO_FIXED)} 项:")
        for cat, action in _AUTO_FIXED:
            lines.append(f"  🔧 [{cat}] {action}")

    if _NEED_USER:
        lines.append(f"\n⚠️  需你操作 {len(_NEED_USER)} 项:")
        for cat, msg, guide in _NEED_USER:
            lines.append(f"  🧑 [{cat}] {msg}")
            for g in guide.strip().split("\n"):
                lines.append(f"     {g}")

    if _PROBLEMS:
        # 未自动修复也未标记为需用户的问题（仅检查模式或不可修）
        user_problem_keys = {p[0] for p in _NEED_USER}
        extra = [(c, m, n) for c, m, n in _PROBLEMS if c not in user_problem_keys]
        if extra:
            lines.append(f"\n❌ 其他问题 {len(extra)} 项（连续{MAX_CONSECUTIVE_FAILURES}次才报）:")
            for cat, msg, count in extra:
                lines.append(f"  ❌ [{cat}] {msg} (第{count}次)")

    if not lines and not _PROBLEMS:
        lines.append("\n✅ 全部正常，无需关注")

    output = "\n".join(lines)
    print(output)

    if _NEED_USER:
        sys.exit(2)
    elif _PROBLEMS:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()

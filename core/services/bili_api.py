#!/usr/bin/env python3
"""
Bilibili API Service
Handles Authentication, CID fetching, and Subtitle uploading.
Extracted from uploader.py
"""
import json
import os
import time
import re
import requests
from typing import Optional
from core.utils.logger import log
from core.utils.config import load_config
from core.utils.cookie import get_bilibili_cookies

CONFIG = load_config()
COOKIE_FILE = CONFIG.get('cookie_file')
VERBOSE_CONSOLE_LOGS = bool(CONFIG.get("verbose_console_logs", False))


def _console(msg):
    print(msg)


def _verbose(msg):
    if VERBOSE_CONSOLE_LOGS:
        print(msg)

def get_bilibili_credentials():
    """
    Get Bilibili credentials directly from browser.
    Always prefers fresh browser cookies over cached files to avoid stale cookie issues.
    Returns: dict (biliup formatted cookie data) or None
    """
    config = CONFIG
    browser = config.get('browser', 'chrome')
    
    # 1. Always try browser first (fresh cookies)
    _console(f"🔄 正在从 {browser.title()} 浏览器获取 Bilibili 凭证...")
    cookies = get_bilibili_cookies(browser)
    
    if cookies:
        log.info(f"成功从 {browser.title()} 获取 Bilibili 凭证")
        return cookies
    
    # 2. Fallback to cached file only if browser extraction fails
    if COOKIE_FILE and os.path.exists(COOKIE_FILE):
        _console("⚠️ 浏览器 Cookie 获取失败，尝试使用缓存文件...")
        try:
            with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            _console("✅ 已加载缓存凭证 (可能已过期)")
            return cached
        except Exception as e:
            _console(f"❌ 缓存文件读取失败: {e}")
    
    _console(f"❌ 未登录且无法从 {browser} 获取 Cookie。")
    _console("   请确保：")
    _console(f"   1. 已在 {browser} 登录 bilibili.com")
    return None

def create_session(cookie_data):
    """
    Create a requests Session with the provided cookies.
    """
    session = requests.Session()
    
    # Hack: Inject headers to fix -101 error
    from core.utils.cookie import get_user_agent
    ua = get_user_agent()
    session.headers.update({
        "User-Agent": ua,
        "Referer": "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com"
    })
    
    # Sanitize and extract cookies
    safe_cookies = {}
    
    # 1. Handle "cookie_info" wrapper (biliup style)
    if isinstance(cookie_data, dict) and 'cookie_info' in cookie_data:
        cookie_data = cookie_data['cookie_info']
        
    # 2. Handle "cookies" list (biliup/browser_cookie3 style)
    if isinstance(cookie_data, dict) and 'cookies' in cookie_data and isinstance(cookie_data['cookies'], list):
        for cookie in cookie_data['cookies']:
            if isinstance(cookie, dict) and 'name' in cookie and 'value' in cookie:
                safe_cookies[cookie['name']] = str(cookie['value'])
    
    # 3. Handle flat dict (simple key-value)
    elif isinstance(cookie_data, dict):
        for k, v in cookie_data.items():
            if k == 'cookie_info': continue # Already handled or invalid
            
            # If value is a dict, try to extract 'value'
            if isinstance(v, dict) and 'value' in v:
                safe_cookies[str(k)] = str(v['value'])
            elif isinstance(v, dict):
                continue 
            else:
                safe_cookies[str(k)] = str(v)
                
    # 4. Fallback
    else:
        safe_cookies = cookie_data

    session.cookies.update(safe_cookies)
    return session

def convert_vtt_to_bcc(vtt_path, lang='zh-CN', max_duration=None):
    """
    Simple VTT/SRT to BCC converter.
    """
    body = []
    try:
        with open(vtt_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # Basic parser for VTT/SRT
        # Regex for timestamp: 00:00:00.000
        # time_pattern = re.compile(r'(\d{2}):(\d{2}):(\d{2})[.,](\d{3})')
        
        current_item = {}
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if 'WEBVTT' in line:
                continue
            if '-->' in line:
                # Parse timestamp
                parts = line.split('-->')
                if len(parts) == 2:
                    start_str = parts[0].strip()
                    end_str = parts[1].strip()
                    
                    def time_to_seconds(t_str):
                        t_str = t_str.strip().split(' ')[0] # Handle align:start etc
                        t_str = t_str.replace(',', '.')
                        try:
                            if '.' in t_str:
                                base, ms = t_str.split('.')
                                h, m, s = map(int, base.split(':'))
                                return h * 3600 + m * 60 + s + int(ms) / 1000.0
                            else:
                                h, m, s = map(int, t_str.split(':'))
                                return h * 3600 + m * 60 + s
                        except:
                            return 0.0

                    start_sec = time_to_seconds(start_str)
                    end_sec = time_to_seconds(end_str)
                    
                    # Logic: Max Duration Clamping
                    if max_duration:
                        if start_sec >= max_duration:
                            # Subtitle starts after video ends, skip completely
                            current_item = {'skip': True} 
                            continue
                        if end_sec > max_duration:
                            end_sec = max_duration
                    
                    # Save previous item if valid
                    if current_item.get('from') is not None and current_item.get('content') and not current_item.get('skip'):
                        parts = current_item['content'].split('\n')
                        fmt_lines = []
                        for p in parts:
                            fmt_lines.extend([p[i:i+40] for i in range(0, len(p), 40)])
                        current_item['content'] = '\n'.join(fmt_lines[:3])
                        body.append(current_item)
                    
                    # Start new item
                    current_item = {}
                    
                    # Filter short segments (duplicates/artifacts)
                    if (end_sec - start_sec) < 0.1:
                         current_item['skip'] = True
                         continue

                    current_item['from'] = start_sec
                    current_item['to'] = end_sec
                    current_item['location'] = 2 # Bottom center
            elif current_item.get('from') is not None and not current_item.get('skip'):
                # Clean content: remove tags like <c.white>, <00:01>
                clean_line = re.sub(r'<[^>]+>', '', line).strip()
                if not clean_line:
                    continue
                    
                if 'content' in current_item:
                    current_item['content'] += '\n' + clean_line
                else:
                    current_item['content'] = clean_line
            elif current_item.get('skip'):
                continue
            
        # Append last item if exists
        if current_item.get('from') is not None and not current_item.get('skip') and current_item.get('content'):
            parts = current_item['content'].split('\n')
            fmt_lines = []
            for p in parts:
                fmt_lines.extend([p[i:i+40] for i in range(0, len(p), 40)])
            current_item['content'] = '\n'.join(fmt_lines[:3])
            body.append(current_item)

    except Exception as e:
        _console(f"⚠️ 字幕转换失败: {e}")
        return None

    return {
        "font_size": 0.4,
        "font_color": "#FFFFFF",
        "background_alpha": 0.5,
        "background_color": "#9C27B0",
        "Stroke": "none",
        "body": body
    }

def get_video_info(bvid, session):
    """
    Get video info (aid, cid, duration) from BVID.
    """
    try:
        url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
        resp = session.get(url, timeout=10).json()
        if resp.get('code') == 0:
            data = resp.get('data', {})
            aid = data.get('aid')
            cid = data.get('cid')
            duration = data.get('duration')
            
            # Handle multipage?
            pages = data.get('pages', [])
            if pages and not cid:
                cid = pages[0].get('cid')
                # Also try to get precise duration from page
                page_duration = pages[0].get('duration')
                if page_duration:
                    duration = page_duration
            
            return {'aid': aid, 'cid': cid, 'duration': duration}
    except Exception as e:
        log.warning(f"Failed to get video info for {bvid}: {e}")
    return None


def fetch_cid(bvid: str, session, max_retries: int = 12, verbose: bool = False, max_wait_seconds: int = 90) -> Optional[int]:
    """Fetch the CID (OID) for a given BVID with robust fallbacks."""
    delay = 5  # initial delay in seconds
    start_ts = time.time()
    
    # Try multiple APIs
    endpoints = [
        (f"https://api.bilibili.com/x/player/pagelist?bvid={bvid}", 'data', 0, 'cid'),
        (f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", 'data', None, 'cid'), # data['cid'] or data['pages'][0]['cid']
        (f"https://member.bilibili.com/x/v2/archive/view?bvid={bvid}", 'data', None, 'cid')
    ]

    if verbose:
        _console("📡 正在尝试从 Bilibili 获取 CID (OID)...")
    for attempt in range(1, max_retries + 1):
        for url, data_key, list_idx, id_key in endpoints:
            try:
                # Add headers to mimic browser
                headers = {
                    'Referer': f'https://www.bilibili.com/video/{bvid}',
                    'User-Agent': session.headers.get('User-Agent', '')
                }
                
                resp = session.get(url, headers=headers, timeout=10)
                if resp.status_code != 200:
                    continue
                    
                data = resp.json()
                if data.get('code') != 0:
                    continue

                res_data = data.get(data_key)
                if not res_data:
                    continue
                
                cid = None
                # Handle special structure for view/archive
                if 'pages' in res_data and res_data['pages']:
                    cid = res_data['pages'][0].get('cid')
                elif list_idx is not None and isinstance(res_data, list) and len(res_data) > list_idx:
                    cid = res_data[list_idx].get(id_key)
                elif isinstance(res_data, dict):
                    cid = res_data.get(id_key)
                
                if cid:
                    if verbose:
                        _console(f"✅ 成功获取 CID: {cid}")
                    return cid

            except Exception as e:
                # log.warning(f"Error checking {url}: {e}")
                pass
        
        elapsed = time.time() - start_ts
        if elapsed >= max_wait_seconds:
            if verbose:
                _console(f"⏱️ CID 获取超时（>{max_wait_seconds}s），停止重试。")
            break

        sleep_for = min(delay, max_wait_seconds - elapsed)
        if verbose and (attempt == 1 or attempt % 3 == 0 or attempt == max_retries):
            _console(f"⏳ CID 尚未就绪, {int(sleep_for)}s 后重试... ({attempt}/{max_retries})")
        time.sleep(max(0, sleep_for))
        delay = min(delay * 2, 30)
        
    if verbose:
        _console(f"❌ 获取 CID 失败: {bvid}")
    return None

def get_recommended_activities(tid, session):
    """
    Fetch officially recommended activities for a given category (TID).
    Endpoint: https://member.bilibili.com/x/v2/activity/recommend?tid={tid}
    """
    try:
        url = "https://member.bilibili.com/x/v2/activity/recommend"
        params = {"tid": tid}
        headers = {
            "Referer": "https://member.bilibili.com/platform/upload-manager/article",
            "User-Agent": session.headers.get("User-Agent", "")
        }
        resp = session.get(url, params=params, headers=headers)
        data = resp.json()
        
        if data.get("code") == 0:
            return data.get("data", [])
        else:
            log.warning(f"Failed to fetch recommended activities: {data}")
    except Exception as e:
        log.warning(f"Error fetching recommended activities: {e}")
        if 'resp' in locals():
            log.warning(f"Response Content: {resp.text[:500]}")
    return []

def search_mission_activities(keyword, session, offset=0):
    """
    Search for activities/topics using the internal web endpoint.
    Endpoint: https://member.bilibili.com/x/vupre/web/topic/search
    2026-08-10: 加 offset 参数支持翻页（旧版固定 offset=0 导致结果漂移）。
    """
    try:
        url = "https://member.bilibili.com/x/vupre/web/topic/search"
        params = {
            "keywords": keyword,
            "page_size": 10,
            "offset": offset
        }
        headers = {
            "Referer": "https://member.bilibili.com/platform/upload-manager/article",
            "User-Agent": session.headers.get("User-Agent", "")
        }
        resp = session.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()
        
        if data.get("code") == 0:
            topics = data.get("data", {}).get("result", {}).get("topics", [])
            # Filter for items with valid mission_id
            valid_missions = [t for t in topics if t.get('mission_id', 0) > 0]
            if valid_missions:
                return valid_missions
            return topics # Return all if none have mission_id (fallback)
        else:
            log.warning(f"Failed to search mission activities: {data}")
    except Exception as e:
        log.warning(f"Error searching mission activities: {e}")
    return []

def upload_subtitle(bvid, subtitle_path, session, lang='zh-CN', aid=None, metadata_oid=None, verbose: bool = False):
    """
    Upload subtitle to Bilibili.
    """
    if not os.path.exists(subtitle_path):
        return False

    if verbose:
        _console(f"🔍 处理字幕: {os.path.basename(subtitle_path)} ({lang})")

    # Step 1: Get OID (CID) and Duration
    oid = metadata_oid
    duration = None
    
    # Needs info anyway for duration or aid
    info = get_video_info(bvid, session)
    if info:
        if not oid: oid = info.get('cid')
        if not aid: aid = info.get('aid')
        duration = info.get('duration')
    
    # If still missing OID, run robust fetch (but duration might be lost if we don't update robust fetch)
    # Ideally get_video_info is robust enough.
    if not oid:
        oid = fetch_cid(bvid, session, verbose=verbose)

    if not oid:
        return False
        
    # Step 2: Convert (with duration clamping)
    bcc_data = convert_vtt_to_bcc(subtitle_path, max_duration=duration)
    if not bcc_data:
        return False
    
    # Step 3: Upload
    # Step 3: Upload with Language Code Retry
    upload_url = "https://api.bilibili.com/x/v2/dm/subtitle/draft/save"
    csrf = session.cookies.get('bili_jct')
    
    # Try multiple common language codes for Simplified Chinese
    # Bilibili API is inconsistent with documentation
    lang_candidates = [lang]
    if lang.lower() in ['zh-cn', 'cn', 'zh']:
        # Put 'zh' first as it is verified working (Step 1818)
        lang_candidates = ['zh', 'zh-Hans', 'zh-CN', 'zh-CHS', 'cn']
    elif lang.lower() in ['zh-tw', 'tw']:
        lang_candidates = ['zh-TW', 'zh-Hant']
    
    for try_lang in lang_candidates:
        payload = {
            'aid': aid,
            'bvid': bvid,
            'type': 1,
            'oid': oid,
            'lan': try_lang,
            'submit': 'true',
            'sign': 'false',
            'csrf': csrf,
            'data': json.dumps(bcc_data, ensure_ascii=False)
        }
    
        try:
            r = session.post(upload_url, data=payload, timeout=20)
            res = r.json()
            if res.get('code') == 0:
                if verbose:
                    _console(f"✅ 字幕上传成功 (Lang: {try_lang})")
                return True
            elif res.get('code') == 79011:
                # Common error, silence unless debug
                # log.warning(f"Language code '{try_lang}' rejected (79011), trying next...")
                continue
            else:
                msg = res.get('message') or str(res)
                _console(f"⚠️ 字幕上传失败: code={res.get('code')} message={msg}")
                return False
        except json.JSONDecodeError:
            _console(f"❌ 字幕上传异常: 响应非JSON格式。")
            _console(f"   - Status Code: {r.status_code}")
            _console(f"   - Response Text: {r.text[:500]}")
            return False
        except Exception as e:
            _console(f"❌ 字幕上传异常: {e}")
            return False
            
    _console(f"❌ 字幕上传失败：语言代码尝试完毕 ({lang})")
    return False

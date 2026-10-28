import sys
import os
import requests
import json
import time

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.utils.cookie import get_bilibili_cookies
from core.utils.config import load_config
from core.services.bili_api import create_session

def probe_search():
    config = load_config()
    browser = config.get('browser', 'edge')
    cookies_data = get_bilibili_cookies(browser)
    session = create_session(cookies_data)
    
    # Common endpoints to try
    endpoints = [
        "https://member.bilibili.com/x/v2/activity/search",
        "https://member.bilibili.com/x/web/activity/search",
        "https://api.bilibili.com/x/activity/search",
        "https://member.bilibili.com/x/vupre/web/mission/search",
        "https://member.bilibili.com/x/activity/web/search" # potential
    ]
    
    keyword = "UE5"
    
    print(f"🔍 Probing Mission Search APIs with keyword: {keyword}...")
    
    headers = {
        "Referer": "https://member.bilibili.com/platform/upload-manager/article",
        "User-Agent": session.headers.get("User-Agent", ""),
        "Origin": "https://member.bilibili.com"
    }
    
    for url in endpoints:
        print(f"\nProbing: {url}")
        try:
            params = {"keyword": keyword}
            resp = session.get(url, params=params, headers=headers, timeout=5)
            print(f"  Status: {resp.status_code}")
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    print(f"  Code: {data.get('code')}")
                    if data.get('code') == 0:
                        print("  ✅ SUCCESS! Data found:")
                        print(json.dumps(data.get('data'), indent=2, ensure_ascii=False)[:500])
                    else:
                        print(f"  Msg: {data.get('message')}")
                except:
                    print(f"  Response (Not JSON): {resp.text[:200]}")
        except Exception as e:
            print(f"  Error: {e}")
        time.sleep(1)

if __name__ == "__main__":
    probe_search()

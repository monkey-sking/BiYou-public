import sys
import os
import requests
import json
import time
import asyncio
from functools import reduce
from hashlib import md5
import urllib.parse

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.utils.cookie import get_bilibili_cookies
from core.utils.config import load_config
from core.modules.translator import recommend_activity_keyword
from core.services.bili_activity import search_activity

# --- WBI Helper Functions ---
mixin_key_enc_tab = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52
]

def get_mixin_key(orig: str):
    return reduce(lambda s, i: s + orig[i], mixin_key_enc_tab, '')[:32]

def enc_wbi(params: dict, img_key: str, sub_key: str):
    mixin_key = get_mixin_key(img_key + sub_key)
    curr_time = round(time.time())
    params['wts'] = curr_time
    params = dict(sorted(params.items()))
    params = {
        k: ''.join(filter(lambda chr: chr not in "!'()*", str(v)))
        for k, v in params.items()
    }
    query = urllib.parse.urlencode(params)
    wbi_sign = md5((query + mixin_key).encode()).hexdigest()
    params['w_rid'] = wbi_sign
    return params

# --- Main Logic ---

config = load_config()
browser = config.get('browser', 'edge')
cookies_data = get_bilibili_cookies(browser)
cookies_list = cookies_data['cookie_info']['cookies']

session = requests.Session()
for cookie in cookies_list:
    session.cookies.set(cookie['name'], cookie['value'], domain=cookie.get('domain', '.bilibili.com'))

headers = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://space.bilibili.com/',
}

async def setup_wbi():
    try:
        nav_url = "https://api.bilibili.com/x/web-interface/nav"
        resp = session.get(nav_url, headers=headers)
        data = resp.json()
        if data['code'] != 0:
            return None, None
        wbi_img = data['data']['wbi_img']
        img_key = wbi_img['img_url'].split('/')[-1].split('.')[0]
        sub_key = wbi_img['sub_url'].split('/')[-1].split('.')[0]
        return img_key, sub_key
    except:
        return None, None

def get_my_mid():
    try:
        url = "https://api.bilibili.com/x/web-interface/nav"
        resp = session.get(url, headers=headers)
        data = resp.json()
        if data['code'] == 0:
            return data['data']['mid']
    except:
        pass
    return None

async def process_video(video):
    title = video['title']
    desc = video.get('description', '') or video.get('desc', '') or title # Fallback
    bvid = video['bvid']
    
    print(f"🎬 Processing: {title[:40]}...")
    
    # Check if we have an ID (assuming None for now based on probe)
    # If we want to be sure, we might need a detail call, but let's assume missing for the audit goal
    
    # AI Recommendation with timeout
    try:
        keyword = await asyncio.wait_for(recommend_activity_keyword(title, desc), timeout=25.0)
    except asyncio.TimeoutError:
        print(f"⚠️ AI Timeout for: {title[:20]}...")
        keyword = ""
    except Exception as e:
        print(f"⚠️ AI Error: {e}")
        keyword = ""
    
    res = {
        'bvid': bvid,
        'title': title,
        'keyword': keyword,
        'suggestion': None
    }
    
    if keyword:
        # Search (Sync call) - Run in executor if blocking, but it's fast usually
        try:
             # search_activity uses requests (sync). It wraps it in try-except log.
             activity = search_activity(keyword)
             if activity:
                 res['suggestion'] = activity
        except Exception as e:
             print(f"Search Error: {e}")
            
    return res

async def main():
    print("🔑 Authenticating & Setting up WBI...")
    img_key, sub_key = await setup_wbi()
    mid = get_my_mid()
    
    if not img_key or not mid:
        print("❌ Failed to init WBI or get MID.")
        return

    print(f"📡 Fetching latest videos for MID: {mid}...")
    
    url = "https://api.bilibili.com/x/space/wbi/arc/search"
    raw_params = {
        "mid": mid,
        "ps": 10,  # Process last 10 videos
        "tid": 0,
        "pn": 1,
        "keyword": "",
        "order": "pubdate"
    }
    signed_params = enc_wbi(raw_params, img_key, sub_key)
    
    try:
        resp = session.get(url, params=signed_params, headers=headers)
        data = resp.json()
    except Exception as e:
        print(f"❌ API Error: {e}")
        return

    if data['code'] != 0:
        print(f"❌ API Error Code: {data['code']} - {data['message']}")
        return
        
    vlist = data['data']['list']['vlist']
    print(f"✅ Found {len(vlist)} videos. Starting Audit...\n")
    print("-" * 60)
    
    suggestions = []
    
    for v in vlist:
        res = await process_video(v)
        
        if res['suggestion']:
            sugg = res['suggestion']
            print(f"   💡 [Opportunity] {res['title']}")
            print(f"      Matched Keyword: '{res['keyword']}'")
            print(f"      Suggested Mission: {sugg['title']} (ID: {sugg['id']})")
            print(f"      Topic URL: {sugg['url']}")
            suggestions.append(res)
        elif res['keyword']:
             print(f"   🔸 [No Mission] {res['title']}")
             print(f"      Keyword '{res['keyword']}' found no active missions.")
        else:
             print(f"   🔹 [No Keyword] {res['title']}")
             
        print("-" * 60)
        
    print(f"\nAudit Complete. Found {len(suggestions)} videos with emission opportunities.")

if __name__ == "__main__":
    asyncio.run(main())

import sys
import os
import requests
import json
import time
import asyncio
import copy
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

# --- Setup ---
config = load_config()
browser = config.get('browser', 'edge')
cookies_data = get_bilibili_cookies(browser)
cookies_list = cookies_data['cookie_info']['cookies']
bili_jct = next((c['value'] for c in cookies_list if c['name'] == 'bili_jct'), None)

session = requests.Session()
for cookie in cookies_list:
    session.cookies.set(cookie['name'], cookie['value'], domain=cookie.get('domain', '.bilibili.com'))

headers = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://member.bilibili.com/platform/upload-manager/article',
    'Origin': 'https://member.bilibili.com'
}

# --- Functions ---

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

def perform_edit(bvid, new_mission_id, mission_title):
    try:
        # 1. Fetch Edit View
        view_url = "https://member.bilibili.com/x/vupre/web/archive/view"
        resp = session.get(view_url, params={"bvid": bvid}, headers=headers)
        data = resp.json()
        
        if data['code'] != 0:
            print(f"❌ Fetch view failed: {data.get('message')}")
            return False

        archive = data['data']['archive']
        videos = data['data']['videos']
        
        # Check if already set
        current_mission = archive.get('mission_id')
        if current_mission == new_mission_id:
            print(f"🔹 Already has correct mission ID {new_mission_id}. Skipping.")
            return True

        # 2. Construct Payload
        allowed_keys = [
            'aid', 'title', 'desc', 'tid', 'tag', 'cover', 'mission_id', 
            'copyright', 'no_reprint', 'dynamic', 'desc_format_id', 
            'videos', 'source', 'dtime', 'interactive', 'act_reserve_create', 
            'up_close_danmaku', 'up_close_reply', 'dolby', 'lossless_music'
        ]
        payload = {k: archive.get(k) for k in allowed_keys if k in archive}
        payload['mission_id'] = new_mission_id
        
        clean_videos = []
        for v in videos:
            clean_v = {
                'cid': v.get('cid'),
                'title': v.get('title'),
                'desc': v.get('desc', ''),
                'filename': v.get('filename', v.get('bvid', ''))
            }
            clean_videos.append(clean_v)
        payload['videos'] = clean_videos
        
        # 3. Submit
        print(f"📝 Updating {bvid} -> Mission: {mission_title} ({new_mission_id})...")
        edit_url = f"https://member.bilibili.com/x/vu/web/edit?csrf={bili_jct}"
        resp = session.post(edit_url, json=payload, headers=headers)
        res = resp.json()
        
        if res['code'] == 0:
            print(f"✅ Success!")
            return True
        else:
            print(f"❌ Failed: {res}")
            return False
            
    except Exception as e:
        print(f"❌ Error during edit: {e}")
        return False

async def main():
    print("🚀 Starting Batch Topic Update...")
    img_key, sub_key = await setup_wbi()
    mid = get_my_mid()
    
    if not img_key or not mid:
        print("❌ Auth failed.")
        return
        
    print(f"📡 Fetching videos for MID {mid}...")
    url = "https://api.bilibili.com/x/space/wbi/arc/search"
    
    all_videos = []
    page = 1
    page_size = 50 
    max_pages = 5 # Cover up to 250 videos, enough for ~80

    while page <= max_pages:
        print(f"   Getting page {page}...")
        raw_params = {
            "mid": mid, 
            "ps": page_size, 
            "tid": 0, 
            "pn": page, 
            "keyword": "", 
            "order": "pubdate"
        }
        signed_params = enc_wbi(raw_params, img_key, sub_key)
        
        try:
            resp = session.get(url, params=signed_params, headers=headers)
            data = resp.json()
            vlist = data.get('data', {}).get('list', {}).get('vlist', [])
            if not vlist:
                break
            all_videos.extend(vlist)
            if len(vlist) < page_size:
                break
            page += 1
            time.sleep(1) # Be nice
        except Exception as e:
            print(f"❌ Failed to list videos on page {page}: {e}")
            break
            
    print(f"📋 Found {len(all_videos)} videos total. Scanning...")
    
    for i, v in enumerate(all_videos):
        title = v['title']
        bvid = v['bvid']
        desc = v.get('description', '')
        
        print(f"\n[{i+1}/{len(all_videos)}] Processing: {title[:40]}...")
        
        # Determine Topic
        suggestion = None
        
        # 1. AI Recommendation (with timeout)
        keyword = ""
        try:
            keyword = await asyncio.wait_for(recommend_activity_keyword(title, desc), timeout=15.0)
        except Exception:
            pass
            
        if keyword:
            try:
                suggestion = search_activity(keyword)
            except:
                pass
                
        if suggestion:
            print(f"💡 Suggest: {suggestion['title']} (ID: {suggestion['id']})")
            
            # Perform Edit
            perform_edit(bvid, suggestion['id'], suggestion['title'])
            
            # Sleep to be nice
            time.sleep(2) 
        else:
            print("🔹 No suitable topic found.")

if __name__ == "__main__":
    asyncio.run(main())

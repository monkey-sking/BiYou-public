import sys
import os
import requests
import json
import time

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.utils.cookie import get_bilibili_cookies
from core.utils.config import load_config

# Import Wbi manually implemented below to avoid async loop issues with biliup
pass


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

# Need to update Wbi key first
async def setup_wbi():
    # We can use the Wbi instance from biliup if we can configure it
    # Or just manually fetch keys and sign.
    # Manual might be safer to avoid biliup dependency complexity for a script.
    
    # 1. Get Nav to get wbi_img
    nav_url = "https://api.bilibili.com/x/web-interface/nav"
    resp = session.get(nav_url, headers=headers)
    data = resp.json()
    if data['code'] != 0:
        print("Failed to get Nav:", data)
        return None, None
        
    wbi_img = data['data']['wbi_img']
    img_url = wbi_img['img_url']
    sub_url = wbi_img['sub_url']
    
    img_key = img_url.split('/')[-1].split('.')[0]
    sub_key = sub_url.split('/')[-1].split('.')[0]
    
    return img_key, sub_key

# Manual Wbi Implementation to be standalone/reliable
from functools import reduce
from hashlib import md5
import urllib.parse

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

def get_my_mid():
    url = "https://api.bilibili.com/x/web-interface/nav"
    resp = session.get(url, headers=headers)
    data = resp.json()
    if data['code'] == 0:
        return data['data']['mid']
    return None

import asyncio

async def probe_space_archives_wbi():
    # 0. Get Keys
    img_key, sub_key = await setup_wbi()
    if not img_key:
        return

    mid = get_my_mid()
    if not mid:
        print("No MID")
        return
        
    print(f"MID: {mid}")
    
    url = "https://api.bilibili.com/x/space/wbi/arc/search"
    
    raw_params = {
        "mid": mid,
        "ps": 10,
        "tid": 0,
        "pn": 1,
        "keyword": "",
        "order": "pubdate"
    }
    
    signed_params = enc_wbi(raw_params, img_key, sub_key)
    
    print(f"Probing: {url}")
    resp = session.get(url, params=signed_params, headers=headers)
    data = resp.json()
    
    if data['code'] == 0:
        vlist = data['data']['list']['vlist']
        print(f"Found {len(vlist)} videos.")
        for v in vlist:
            print(f"\nTitle: {v['title']}")
            print(f"BVID: {v['bvid']}")
            # Check for mission_id (might be in 'meta' or top level?)
            # Usually strict public view doesn't show mission_id unfortunately.
            # But let's check.
            print(f"Keys: {v.keys()}")
            print(f"Mission ID: {v.get('mission_id')}")
    else:
        print(f"Error: {data}")

if __name__ == "__main__":
    asyncio.run(probe_space_archives_wbi())

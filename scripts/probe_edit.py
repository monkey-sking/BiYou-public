import sys
import os
import requests
import json

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.utils.cookie import get_bilibili_cookies
from core.utils.config import load_config

config = load_config()
browser = config.get('browser', 'edge')
cookies_data = get_bilibili_cookies(browser)
cookies_list = cookies_data['cookie_info']['cookies']

session = requests.Session()
for cookie in cookies_list:
    session.cookies.set(cookie['name'], cookie['value'], domain=cookie.get('domain', '.bilibili.com'))

headers = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://member.bilibili.com/platform/upload-manager/article',
}

def probe_edit_view(bvid):
    url = "https://member.bilibili.com/x/vupre/web/archive/view"
    params = {"bvid": bvid}
    
    print(f"Probing Edit View: {url}?bvid={bvid}")
    resp = session.get(url, params=params, headers=headers)
    data = resp.json()
    
    if data['code'] == 0:
        arc = data['data']['archive']
        videos = data['data']['videos']
        print(f"Title: {arc['title']}")
        print(f"Mission ID: {arc.get('mission_id')}")
        print(f"Activity ID: {arc.get('activity_id')}") # sometimes called this
        # Print keys to confirm structure for re-submission
        print(f"Archive Keys: {arc.keys()}")
    else:
        print(f"Error: {data}")

if __name__ == "__main__":
    # Use one of the BVIDs from the audit
    # e.g., 【UE5】全场$25？白嫖价值￥9000+... -> BV12fzNBREFy
    probe_edit_view("BV12fzNBREFy")

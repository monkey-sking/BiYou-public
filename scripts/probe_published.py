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

def probe_published():
    # Attempt to fetch list of published videos
    # URL guessed from common Member API usage
    url = "https://member.bilibili.com/x/web/archives" 
    params = {
        "status": "is_pub", # or "" for all
        "pn": 1,
        "ps": 10,
        "coop": 1,
        "interactive": 1
    }
    
    print(f"Probing {url}...")
    try:
        resp = session.get(url, params=params, headers=headers)
        data = resp.json()
        
        if data.get('code') == 0:
            archives = data.get('data', {}).get('arc_audit', []) 
            # Note: structure might be different, let's print keys
            print("Response Keys:", data.get('data', {}).keys())
            
            archives = data.get('data', {}).get('archives', []) 
            if not archives:
                # Some APIs use arc_audits for unreviewed text? But let's check both or print
                archives = data.get('data', {}).get('arc_audits', [])

            if archives is None:
                archives = []

            print(f"Found {len(archives)} videos.")
            
            for item in archives:
                # Handle both structure types if necessary, but arc_audits usually returns items wrapped in 'Archive'
                arc = item.get('Archive', item) 
                
                title = arc.get('title')
                bvid = arc.get('bvid')
                mission_id = arc.get('mission_id')
                print(f"\nTitle: {title}")
                print(f"BVID: {bvid}")
                print(f"Mission ID: {mission_id}")
                
                # Check Extended data
                if mission_id is None:
                     # Try to see if it's in stat or others
                     print(f"Snippet: {str(arc)[:200]}...")
        else:
            print(f"Error: {data}")
            
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    probe_published()

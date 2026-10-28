#!/usr/bin/env python3
"""
Bilibili Video Data Analysis Script
Fetches all videos for the logged-in user and exports detailed statistics to Excel.

Usage:
    python scripts/analyze_bilibili_data.py
"""

import sys
import os
import asyncio

try:
    import pandas as pd
except ImportError:
    print("❌ Missing required library: pandas")
    print("👉 Please run: pip install pandas openpyxl bilibili-api-python")
    sys.exit(1)

import datetime
from pathlib import Path

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    from bilibili_api import user, video, Credential
    from bilibili_api.user import User
except ImportError:
    print("❌ Missing required library: bilibili-api-python")
    print("👉 Please run: pip install bilibili-api-python pandas openpyxl")
    sys.exit(1)

from core.services.bili_api import get_bilibili_credentials
from core.utils.logger import log

def parse_cookies_to_credential(cookie_data):
    """
    Convert local cookie format (dict or biliup) to bilibili_api.Credential
    """
    if not cookie_data:
        return None

    cookies = {}
    
    # Handle biliup style {"cookie_info": {"cookies": [...]}}
    if isinstance(cookie_data, dict) and 'cookie_info' in cookie_data:
        c_list = cookie_data['cookie_info'].get('cookies', [])
        for c in c_list:
            if isinstance(c, dict):
                cookies[c.get('name')] = c.get('value')
    # Handle direct list
    elif isinstance(cookie_data, list):
        for c in cookie_data:
            if isinstance(c, dict):
                cookies[c.get('name', c.get('key'))] = c.get('value')
    # Handle direct dict
    elif isinstance(cookie_data, dict):
        # Filter out random keys, keep mostly what looks like cookies if it's flat
        if 'SESSDATA' in cookie_data:
            cookies = cookie_data
        else:
            # Maybe it's a dict of name:value?
            cookies = cookie_data

    sessdata = cookies.get('SESSDATA')
    bili_jct = cookies.get('bili_jct')
    buvid3 = cookies.get('buvid3')
    dedeuserid = cookies.get('DedeUserID')
    
    if not sessdata or not bili_jct:
        print("⚠️ Warning: SESSDATA or bili_jct missing from cookies.")
        return None
        
    return Credential(
        sessdata=sessdata,
        bili_jct=bili_jct,
        buvid3=buvid3,
        dedeuserid=dedeuserid
    )

async def fetch_all_videos(u: User):
    """
    Fetch all videos for the user, handling pagination.
    """
    all_videos = []
    page = 1
    page_size = 30 # Default API page size
    
    print(f"📥 Starting to fetch video list...")
    
    while True:
        print(f"   Fetching page {page}...")
        try:
            # get_videos returns {'list': {'vlist': [...], 'tlist': ...}, 'page': ...}
            # or sometimes just the dict.
            res = await u.get_videos(pn=page, ps=page_size)
            
            vlist = res.get('list', {}).get('vlist', [])
            if not vlist:
                break
                
            all_videos.extend(vlist)
            
            # Check pagination
            page_info = res.get('page', {})
            count = page_info.get('count', 0)
            
            if len(all_videos) >= count:
                break
                
            page += 1
            await asyncio.sleep(0.5) # Be nice to API
            
        except Exception as e:
            print(f"❌ Error fetching page {page}: {e}")
            break
            
    print(f"✅ Found total {len(all_videos)} videos.")
    return all_videos

async def get_video_stats_batch(video_list, credential):
    """
    Get detailed stats for each video.
    """
    detailed_data = []
    total = len(video_list)
    
    print(f"📊 Fetching detailed statistics for {total} videos...")
    
    for idx, v_summary in enumerate(video_list, 1):
        bvid = v_summary['bvid']
        title = v_summary['title']
        
        # Basic progress
        print(f"   [{idx}/{total}] Analyzing: {title[:20]}...")
        
        try:
            v = video.Video(bvid=bvid, credential=credential)
            info = await v.get_info()
            
            # Info structure: https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/docs/video/info.md
            stat = info.get('stat', {})
            
            video_data = {
                'Title': info.get('title'),
                'BVID': bvid,
                'AID': info.get('aid'),
                'Duration (s)': info.get('duration'),
                'Publish Time': datetime.datetime.fromtimestamp(info.get('pubdate')),
                'Views (播放)': stat.get('view'),
                'Danmaku (弹幕)': stat.get('danmaku'),
                'Replies (评论)': stat.get('reply'),
                'Likes (点赞)': stat.get('like'),
                'Coins (硬币)': stat.get('coin'),
                'Favorites (收藏)': stat.get('favorite'),
                'Shares (分享)': stat.get('share'),
                'Rank (最高全站日排行)': stat.get('his_rank', 0),
                'URL': f"https://www.bilibili.com/video/{bvid}",
                'Cover': info.get('pic')
            }
            detailed_data.append(video_data)
            
            # Rate limit handling
            await asyncio.sleep(0.2)
            
        except Exception as e:
            print(f"❌ Failed to fetch info for {bvid}: {e}")
            
    return detailed_data

async def main():
    print("🚀 Bilibili Video Data Analyzer")
    print("===============================")
    
    # 1. Login / Load Cookies
    cookie_data = get_bilibili_credentials()
    if not cookie_data:
        print("❌ Login failed. Please login in your browser first.")
        return

    cred = parse_cookies_to_credential(cookie_data)
    if not cred:
        print("❌ Failed to parse credentials (missing SESSDATA?).")
        return
        
    # 2. Get Self Info (UID)
    try:
        # We need to instantiate a temp User or just call an API to get self info
        # Currently bilibili-api verify credential by checking getting self info?
        # Let's try to get 'my' info.
        # There isn't a direct "get_self" on User class without UID, 
        # but we can use 'user.get_self_info(cred)'
        print("🔐 Verifying credentials...")
        my_info = await user.get_self_info(cred)
        my_mid = my_info['mid']
        my_name = my_info['name']
        print(f"👤 Authenticated as: {my_name} (UID: {my_mid})")
        
    except Exception as e:
        print(f"❌ Authentication failed: {e}")
        return

    # 3. Fetch all videos
    u = User(uid=my_mid, credential=cred)
    videos = await fetch_all_videos(u)
    
    if not videos:
        print("⚠️ No videos found.")
        return

    # 4. Fetch detailed stats
    data = await get_video_stats_batch(videos, cred)
    
    # 5. Export
    if data:
        df = pd.DataFrame(data)
        
        # Sort by Publish Time Desc
        df = df.sort_values(by='Publish Time', ascending=False)
        
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = f"bilibili_data_{my_mid}_{timestamp}.xlsx"
        
        print(f"💾 Saving data to {output_file}...")

        try:
            df.to_excel(output_file, index=False)
            print(f"✅ Success! Data exported to: {os.path.abspath(output_file)}")
        except ImportError:
            print("⚠️ openpyxl not found or error, skipping Excel export.")

        # Always save CSV as well
        csv_file = output_file.replace('.xlsx', '.csv')
        df.to_csv(csv_file, index=False)
        print(f"✅ Success! Data exported to: {os.path.abspath(csv_file)}")

             
    else:
        print("❌ No data collected.")

if __name__ == '__main__':
    asyncio.run(main())

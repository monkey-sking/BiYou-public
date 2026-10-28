#!/usr/bin/env python3
"""
Audit and Update Video Activity Topics (Mission IDs)
"""
import sys
import os
import argparse
import time
# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core.utils.logger import log
from core.utils.config import load_config
from core.services.bili_api import create_session, get_bilibili_credentials
from core.modules.organize_collections import classify_video, COLLECTION_RULES, DEFAULT_COLLECTION

# ==========================================
# CONFIGURATION: ACTIVITY IDs (MISSION IDs)
# Will be populated at runtime
# ==========================================
ACTIVITY_MAP = {}
# ==========================================


def get_video_details(session, bvid):
    """Fetch video details including mission_id"""
    try:
        url = "https://member.bilibili.com/x/vupre/web/archive/view"
        params = {"bvid": bvid}
        headers = {
            'User-Agent': session.headers.get('User-Agent', ''),
            'Referer': 'https://member.bilibili.com/platform/upload-manager/article',
        }
        resp = session.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()
        if data.get('code') == 0:
            return data.get('data', {}).get('archive', {})
        else:
            log.warning(f"Failed to fetch details for {bvid}: {data.get('message')}")
    except Exception as e:
        log.error(f"Error fetching {bvid}: {e}")
        # If it's a JSON decode error, log the response text if available
        if isinstance(e, ValueError) or 'Expecting value' in str(e): # JSONDecodeError is a ValueError
             try:
                 log.error(f"Response Content: {resp.text[:500]}")
             except:
                 pass
        time.sleep(2.0) # Additional sleep on error
    return {}

def update_mission_id(session, bvid, new_mission_id, current_data):
    """
    Update the mission_id of a video.
    """
    try:
        csrf = session.cookies.get('bili_jct')
        if not csrf:
            # Try to find in cookie jar (sometimes name differs)
            for c in session.cookies:
                if c.name == 'bili_jct':
                    csrf = c.value
                    break
        
        if not csrf:
            log.error(f"❌ CSRF token (bili_jct) not found in cookies! Cannot update.")
            return False
            
        edit_url = f"https://member.bilibili.com/x/vu/web/edit?csrf={csrf}"
        
        # Prepare payload (mimic test_edit_mission.py)
        # We must include essential fields to avoid clearing them
        payload = {
            "aid": current_data.get('aid'),
            "title": current_data.get('title'),
            "desc": current_data.get('desc'),
            "tid": current_data.get('tid'),
            "tag": current_data.get('tag'),
            "cover": current_data.get('cover'),
            "copyright": current_data.get('copyright'),
            "no_reprint": current_data.get('no_reprint', 0),
            "mission_id": new_mission_id,
            "csrf": csrf
        }
        
        # Videos list is required usually
        # We assume single p or we need to fetch 'videos' from view response
        # The view response structure in 'get_video_details' (archive) doesn't have 'videos' list directly?
        # Actually x/vupre/web/archive/view returns {archive: {...}, videos: [...]}
        # We need the full response to construct payload correctly.
        pass # To be implemented fully if we had the full response
        
        # Re-fetching full data to comprise payload
        view_url = "https://member.bilibili.com/x/vupre/web/archive/view"
        headers = {
            'User-Agent': session.headers.get('User-Agent', ''),
            'Referer': 'https://member.bilibili.com/platform/upload-manager/article',
        }
        resp = session.get(view_url, params={"bvid": bvid}, headers=headers).json()
        if resp['code'] != 0:
            return False
            
        full_data = resp['data']
        archive = full_data['archive']
        videos = full_data['videos']
        
        payload = {
            "aid": archive.get('aid'),
            "title": archive.get('title'),
            "desc": archive.get('desc'),
            "tid": archive.get('tid'),
            "tag": archive.get('tag'),
            "cover": archive.get('cover'),
            "copyright": archive.get('copyright', 1),
            "no_reprint": archive.get('no_reprint', 0),
            "mission_id": new_mission_id,
            "source": archive.get('source', ''),
            "csrf": csrf
        }
        
        # Clean videos
        clean_videos = []
        for v in videos:
            clean_videos.append({
                'cid': v.get('cid'),
                'title': v.get('title'),
                'desc': v.get('desc', ''),
                'filename': v.get('filename', v.get('bvid', ''))
            })
        payload['videos'] = clean_videos
        
        # Submit
        resp = session.post(edit_url, json=payload, headers=headers)
        res = resp.json()
        
        if res.get('code') == 0:
            log.info(f"✅ Updated {bvid} to Mission ID {new_mission_id}")
            return True
        else:
            log.error(f"❌ Failed to update {bvid}: {res}")
            return False
            
    except Exception as e:
        log.error(f"Exception updating {bvid}: {e}")
        return False

def audit_topics(dry_run=True):
    print("🚀 Starting Video Topic Audit...")
    
    # Init Session
    creds = get_bilibili_credentials()
    if not creds:
        print("❌ Please login first.")
        return
    session = create_session(creds)
    
    # Get Videos (Reuse organize_collections logic)
    # We can rely on probe_published list or fetch again
    from core.modules.organize_collections import get_my_videos, BiliCollectionAPI
    
    # Reuse API just for convenience of 'get_my_videos'
    api = BiliCollectionAPI(session) 
    print("🔍 Fetching videos...")
    
    # Fetch ample videos
    all_videos = []
    page = 1
    while True:
        videos, total = get_my_videos(api.session, page, 50)
        all_videos.extend(videos)
        if len(all_videos) >= total or not videos:
            break
        page += 1
        time.sleep(0.5)
        
    print(f"📊 Found {len(all_videos)} videos.")
    
    # Iterate ALL videos individually
    print("\n" + "="*50)
    print("🔍 Auditing all videos for missing topics...")
    print("="*50)
    
    from core.services.bili_api import search_mission_activities
    
    updates_needed = 0
    
    for v in all_videos:
        title = v['title']
        bvid = v['bvid']
        
        # Check current status
        # Note: 'v' from get_my_videos might not have mission_id populated correctly?
        # get_my_videos uses x/web/archives which returns 'mission_id' in archive
        # Let's check 'v' keys. Assuming get_my_videos returns dict with mission_id if available.
        # But looking at organize_collections.py:get_my_videos, it returns formatted dict.
        # It does NOT verify/include mission_id.
        # So we MUST fetch details or trust we need to update if we want to be sure.
        
        # Optimization: Fetch details only if we suspect update needed?
        # Or blindly trust it?
        # Let's fetch details to be safe since we are in audit mode.
        details = get_video_details(session, bvid)
        current_mission_id = details.get('mission_id', 0)
        
        if current_mission_id > 0:
            # Already has a topic
            print(f"✅ {title[:30]}... (Mission: {current_mission_id})")
            continue
            
        print(f"\n🎥 Missing Topic: {title[:40]}...")
        
        # Determine Keyword
        # 1. Use Category Rule first ?
        collection_name = classify_video(title, COLLECTION_RULES)
        
        search_keyword = ""
        fallback_keyword = None
        
        if "UE5" in collection_name or "UE5" in title.upper(): 
            search_keyword = "UE5"
            fallback_keyword = "UE" # Fallback to broader term
        elif "健身" in collection_name or "健身" in title: search_keyword = "健身"
        elif "AI" in collection_name or "AI" in title.upper(): search_keyword = "AI"
        else: 
            search_keyword = title[:5]
            
        print(f"   Searching for keyword: '{search_keyword}'...")
        results = search_mission_activities(search_keyword, session)
        
        # Fallback logic
        if not results and fallback_keyword:
             print(f"   ⚠️ No results for '{search_keyword}', trying fallback: '{fallback_keyword}'...")
             results = search_mission_activities(fallback_keyword, session)
        
        target_mission_id = 0
        if results:
            best = results[0]
            target_mission_id = best.get('mission_id')
            print(f"   🎯 Found Match: {best.get('name')} (ID: {target_mission_id})")
        else:
            print("   ⚠️ No mission found.")
            
        if target_mission_id > 0 and target_mission_id != current_mission_id:
             updates_needed += 1
             if not dry_run:
                 success = update_mission_id(session, bvid, target_mission_id, details)
                 if success:
                     time.sleep(3.0) # Increased delay to be safer
                 else:
                     print("   ❌ Update failed, cooling down...")
                     time.sleep(5.0)
             else:
                 print(f"   [Dry Run] Would update to {target_mission_id}")
    
    print("\n" + "="*30)
    print(f"Audit Complete. Updates Proposed/Done: {updates_needed}")
    if dry_run and updates_needed > 0:
        print("Run with --execute to apply changes.")
    print("="*30)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    
    audit_topics(dry_run=not args.execute)

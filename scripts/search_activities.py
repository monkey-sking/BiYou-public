import sys
import os
import requests
import json
import time

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.utils.cookie import get_bilibili_cookies
from core.utils.config import load_config
from core.services.bili_api import create_session, get_recommended_activities

def search_activities():
    config = load_config()
    browser = config.get('browser', 'edge')
    cookies_data = get_bilibili_cookies(browser)
    session = create_session(cookies_data)
    
    # Candidate TIDs
    # 17: Gaming
    # 171: Electronic Sports
    # 172: Mobile Games
    # 65: Online Games
    # 121: GMV
    # 136: Life -> Daily?
    # 234: Life -> Sports/Fitness? (Check actual TID map)
    # 36: Tech -> Science
    # 188: Tech -> Digital
    # 230: Tech -> AI? (Maybe)
    
    # Let's try a broad range or specific known ones
    tids = {
        "Gaming (General)": 17,
        "Mobile Games": 172,
        "Tech (Digital)": 188,
        "Tech (Science)": 36,
        "Life (Sports)": 234, # Guess
        "Life (Daily)": 21
    }
    
    print("🔍 Searching for recommended activities...")
    
    for name, tid in tids.items():
        print(f"\n--- Category: {name} (TID: {tid}) ---")
        activities = get_recommended_activities(tid, session)
        if activities:
            for act in activities:
                print(f"  Activity: {act.get('name')} (ID: {act.get('id')})")
                print(f"    Desc: {act.get('desc', '')[:50]}")
        else:
            print("  No recommendations found.")
        time.sleep(1)

if __name__ == "__main__":
    search_activities()

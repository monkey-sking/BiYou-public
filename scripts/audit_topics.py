import sys
import os
import json
import asyncio

# Set up path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.modules.translator import recommend_activity_keyword_sync
from core.services.bili_activity import search_activity
from core.utils.config import load_config

HISTORY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'history')

async def audit_video(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            meta = json.load(f)
            
        title = meta.get('title', 'Unknown')
        mission_id = meta.get('mission_id') or meta.get('topic_id')
        
        result = {
            'file': filepath,
            'title': title,
            'mission_id': mission_id,
            'suggestion': None
        }
        
        if not mission_id:
            desc = meta.get('description', '')
            keyword = await recommend_activity_keyword_sync_async(title, desc)
            if keyword:
                activity = search_activity(keyword) # This is sync, but fast enough for script
                if activity:
                    result['suggestion'] = activity
                    result['keyword'] = keyword
        
        return result
    except Exception as e:
        print(f"Error processing {filepath}: {e}")
        return None

# Wrapper because translator function is actually sync wrapping async, 
# but we want to run multiple in parallel maybe? 
# Actually translator.py defines recommend_activity_keyword (async) and _sync.
# Let's import the async one to be clean if we want parallel, or just sync for simplicity.
# The user asked for "process", so sequential is fine for output clarity.

from core.modules.translator import recommend_activity_keyword

async def main():
    print(f"Scanning {HISTORY_DIR}...")
    files = [f for f in os.listdir(HISTORY_DIR) if f.endswith('.json')]
    files.sort()
    
    print(f"Found {len(files)} history files.\n")
    
    tasks = []
    
    for filename in files:
        filepath = os.path.join(HISTORY_DIR, filename)
        # Process sequentially to avoid rate limits
        res = await audit_video_logic(filepath)
        print_result(res)

async def audit_video_logic(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            meta = json.load(f)
            
        title = meta.get('title', 'Unknown Title')
        desc = meta.get('description', '')
        mission_id = meta.get('mission_id') or meta.get('topic_id')
        
        if mission_id:
            return {'status': 'ok', 'title': title, 'id': mission_id}
            
        # Missing ID, run AI
        keyword = await recommend_activity_keyword(title, desc)
        if not keyword:
            return {'status': 'no_keyword', 'title': title}
            
        activity = search_activity(keyword)
        if activity:
            return {'status': 'suggested', 'title': title, 'suggestion': activity, 'keyword': keyword}
        else:
            return {'status': 'no_activity', 'title': title, 'keyword': keyword}
            
    except Exception as e:
        return {'status': 'error', 'title': filepath, 'error': str(e)}

def print_result(res):
    if not res: return
    
    status = res['status']
    title = res['title']
    
    if status == 'ok':
        # print(f"✅ [Has Topic] {title[:30]}... (ID: {res['id']})")
        pass # Skip showing good ones to reduce noise? Or show concise?
    elif status == 'suggested':
        sugg = res['suggestion']
        print(f"💡 [Suggest] {title[:40]}...")
        print(f"   Key: {res['keyword']} -> Act: {sugg['title']} (ID: {sugg['id']})")
        print("-" * 40)
    elif status == 'no_keyword':
        print(f"🔸 [No Key] {title[:40]}... (AI couldn't suggest keyword)")
    elif status == 'no_activity':
        print(f"🔸 [No Act] {title[:40]}... (Key: {res['keyword']} -> No search results)")
    elif status == 'error':
        print(f"❌ [Error] {res['error']}")

if __name__ == "__main__":
    asyncio.run(main())

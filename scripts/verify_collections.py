import sys
import os

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.services.bili_collection_api import BiliCollectionAPI

def verify_collections():
    api = BiliCollectionAPI()
    collections = api.get_my_collections()
    
    print(f"Found {len(collections)} collections.")
    
    for c in collections:
        print(f"\n📁 Collection: {c['title']} (ID: {c['id']})")
        videos = api.get_collection_videos(c['id'])
        print(f"   Actual Video Count: {len(videos)}")
        for v in videos[:5]:
            print(f"   - {v['title']}")
        if len(videos) > 5:
            print(f"   ... and {len(videos)-5} more")

if __name__ == "__main__":
    verify_collections()

import json
import os
import time
from core.utils.config import PROJECT_ROOT
from core.utils.logger import log

class BiliCacheService:
    """Service to cache Bilibili mission/topic data locally."""
    
    def __init__(self, cache_file: str = None):
        if cache_file:
            self.cache_file = cache_file
        else:
            # Default to data/bili_cache.json
            data_dir = os.path.join(PROJECT_ROOT, "data")
            os.makedirs(data_dir, exist_ok=True)
            self.cache_file = os.path.join(data_dir, "bili_cache.json")
        
        self.cache = self._load_cache()

    def _load_cache(self) -> dict:
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                log.warning(f"⚠️ Failed to load Bilibili cache: {e}")
        return {"missions": {}, "categories": {}}

    def save_cache(self):
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=4)
        except Exception as e:
            log.error(f"❌ Failed to save Bilibili cache: {e}")

    def get_mission(self, keyword: str, max_age_hours: int = None) -> dict:
        """Get mission by keyword from cache."""
        mission = self.cache.get("missions", {}).get(keyword)
        if not mission:
            return None

        if max_age_hours is not None:
            updated_at = mission.get("updated_at", 0) or 0
            if updated_at <= 0 or (time.time() - updated_at) > max_age_hours * 3600:
                return None
        return mission

    def update_mission(self, keyword: str, mission_id: int, mission_name: str):
        """Update mission in cache."""
        if "missions" not in self.cache:
            self.cache["missions"] = {}
        
        self.cache["missions"][keyword] = {
            "id": mission_id,
            "name": mission_name,
            "updated_at": time.time()
        }
        self.save_cache()

    def remove_mission(self, keyword: str = None, mission_id: int = None):
        """Remove cached missions by keyword and/or mission_id."""
        missions = self.cache.get("missions", {})
        changed = False

        if keyword and keyword in missions:
            missions.pop(keyword, None)
            changed = True

        if mission_id is not None:
            for key in list(missions.keys()):
                if missions.get(key, {}).get("id") == mission_id:
                    missions.pop(key, None)
                    changed = True

        if changed:
            self.save_cache()

    def get_category_tid(self, category_name: str) -> int:
        """Get Bilibili TID for a category name."""
        return self.cache.get("categories", {}).get(category_name)

    def update_category(self, name: str, tid: int):
        """Update category TID in cache."""
        if "categories" not in self.cache:
            self.cache["categories"] = {}
        self.cache["categories"][name] = tid
        self.save_cache()

# Global instance
_bili_cache = None

def get_bili_cache() -> BiliCacheService:
    global _bili_cache
    if _bili_cache is None:
        _bili_cache = BiliCacheService()
    return _bili_cache

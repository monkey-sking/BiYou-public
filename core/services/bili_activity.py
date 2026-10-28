import re
from typing import Optional
from core.utils.logger import log
from core.utils.cookie import get_bilibili_cookies
from core.utils.config import load_config

KEYWORD_ALIASES = {
    "健身": ["运动", "训练", "燃脂", "减脂", "居家健身", "力量训练"],
    "运动": ["健身", "训练", "燃脂", "减脂"],
    "科技": ["AI", "人工智能", "数码", "智能"],
    "科普": ["科技", "知识", "AI"],
    "知识": ["科技", "科普", "AI"],
    "UE5": ["虚幻引擎", "UnrealEngine", "游戏开发"],
    "游戏开发": ["UE5", "虚幻引擎", "Unity", "Godot"],
}

# 活动缓存有效期: 30天
CACHE_TTL = 30 * 86400

RISK_TERMS = {
    "萌宠": ["萌宠", "宠物", "猫", "狗", "吃播"],
    "汽车": ["新车", "汽车", "车", "智驾", "京牌"],
    "赛事": ["巴黎", "运动会", "奥运"],
    "亲子": ["娃", "宝妈", "儿童"],
    "游戏": ["游戏", "原神", "王者", "Steam"],
    "清洁": ["清洁", "洗护", "家电", "家居", "扫地"],
    "珠宝": ["珠宝", "首饰", "饰品"],
    "音乐": ["音乐", "歌曲", "唱歌", "配乐", "音频"],
    "杂项": ["训练营", "训练场", "大赏", "翻唱", "洗护", "玩具"],
}

LOW_TRUST_CACHE_KEYWORDS = {"科技", "技术", "知识", "科普", "AI", "人工智能"}
GENERIC_ACTIVITY_KEYWORDS = {
    "科技",
    "技术",
    "知识",
    "科普",
    "ai",
    "人工智能",
    "智能",
    "数码",
    "日常",
    "生活",
}


def _normalize_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        value = " ".join(str(v) for v in value)
    return str(value).lower()

def _is_plain_keyword(keyword: str) -> bool:
    if not keyword:
        return False
    keyword = str(keyword).strip()
    if not keyword or "\n" in keyword or "\r" in keyword:
        return False
    return re.match(r"^[\u4e00-\u9fffA-Za-z0-9]{2,8}$", keyword) is not None


def _extract_terms(text: str) -> list[str]:
    raw = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", text or "")
    seen = set()
    terms = []
    for item in raw:
        token = item.lower()
        if token in seen:
            continue
        seen.add(token)
        terms.append(token)
    return terms


def _is_generic_activity_keyword(keyword: str) -> bool:
    return _normalize_text(keyword).strip() in {item.lower() for item in LOW_TRUST_CACHE_KEYWORDS}


def _build_source_signal_text(metadata: Optional[dict] = None, category: str = "") -> str:
    metadata = metadata or {}
    content = " ".join(
        _normalize_text(metadata.get(field, ""))
        for field in ("title", "description", "original_title", "original_description")
    )
    return f"{content} {_normalize_text(metadata.get('tags', []))} {_normalize_text(category)}".strip()


def should_reuse_cached_mission(keyword: str, metadata: Optional[dict] = None) -> bool:
    """Broad tech keywords are cheap to search and too noisy to trust from cache."""
    del metadata
    return not _is_generic_activity_keyword(keyword)


def should_refresh_existing_mission(metadata: Optional[dict] = None) -> bool:
    metadata = metadata or {}
    mission_id = metadata.get("mission_id") or metadata.get("topic_id") or metadata.get("activity_id")
    mission_name = metadata.get("mission_name") or ""
    if not mission_id or not mission_name:
        return False

    # 检查缓存是否过期
    cached_at = metadata.get("cached_at", 0)
    import time
    if cached_at and (time.time() - cached_at) > CACHE_TTL:
        log.info(f"⏰ 活动缓存已过期 (cached_at={cached_at}), 将重新搜索")
        return True

    keyword = metadata.get("topic_keyword") or mission_name
    category = metadata.get("category", "")
    return not is_activity_relevant(mission_name, keyword=keyword, category=category, metadata=metadata)


def expand_activity_keywords(keyword: str, category: str = "", metadata: Optional[dict] = None) -> list[str]:
    """Expand a seed keyword into more searchable activity phrases."""
    metadata = metadata or {}
    seeds = []
    if keyword:
        seeds.append(keyword)

    if category:
        sub_cat = category.split('-')[-1].split('·')[0]
        if sub_cat:
            seeds.append(sub_cat)

    for tag in metadata.get("tags", [])[:5]:
        if isinstance(tag, str) and len(tag.strip()) >= 2:
            seeds.append(tag.strip())

    for title_key in ("topic_keyword", "title", "original_title"):
        seeds.extend(_extract_terms(_normalize_text(metadata.get(title_key, "")))[:8])

    ordered = []
    seen = set()
    for seed in seeds:
        if not seed:
            continue
        seed = str(seed).strip()
        if len(seed) < 2 or seed in seen:
            continue
        seen.add(seed)
        ordered.append(seed)
        for alias in KEYWORD_ALIASES.get(seed, []):
            if alias not in seen:
                seen.add(alias)
                ordered.append(alias)
    return ordered


def is_activity_relevant(activity_title: str, keyword: str = "", category: str = "", metadata: Optional[dict] = None) -> bool:
    """Cheap relevance guard to avoid obviously unrelated active campaigns."""
    metadata = metadata or {}
    title_text = _normalize_text(activity_title)
    content = _build_source_signal_text(metadata, category)

    for _, risky_words in RISK_TERMS.items():
        hit_risky = any(word.lower() in title_text for word in risky_words)
        has_context = any(word.lower() in content for word in risky_words)
        if hit_risky and not has_context:
            return False

    shared = 0
    expanded = expand_activity_keywords(keyword, category, metadata)
    for token in expanded:
        if token.lower() in title_text:
            shared += 1

    if shared <= 0:
        return False

    if _is_generic_activity_keyword(keyword):
        specific_shared = 0
        for token in expanded:
            normalized = token.lower()
            if normalized in GENERIC_ACTIVITY_KEYWORDS:
                continue
            if normalized in title_text:
                specific_shared += 1

        if specific_shared <= 0:
            for token in _extract_terms(activity_title):
                if token in GENERIC_ACTIVITY_KEYWORDS:
                    continue
                if token not in content:
                    return False

    if category and any(word in category for word in ("健身", "运动")):
        fitness_terms = ["健身", "燃脂", "减脂", "力量", "哑铃", "训练"]
        if not any(term.lower() in title_text for term in fitness_terms):
            return False
        if "运动" in title_text and not any(term.lower() in title_text for term in fitness_terms if term != "训练"):
            return False

    return True


def score_activity_candidate(candidate: dict, keyword: str = "", category: str = "", metadata: Optional[dict] = None) -> int:
    metadata = metadata or {}
    title = candidate.get("title", "")
    title_text = _normalize_text(title)
    matched_keyword = str(candidate.get("matched_keyword") or "").strip()
    source_text = _build_source_signal_text(metadata, category)
    score = 0

    if matched_keyword:
        normalized_match = matched_keyword.lower()
        if normalized_match == _normalize_text(keyword).strip():
            score += 8
        if normalized_match in source_text:
            score += 30 if normalized_match not in GENERIC_ACTIVITY_KEYWORDS else 6
        if normalized_match in title_text:
            score += 12 if normalized_match not in GENERIC_ACTIVITY_KEYWORDS else 3

    for token in expand_activity_keywords(keyword, category, metadata):
        normalized = token.lower().strip()
        if len(normalized) < 2 or normalized == matched_keyword.lower():
            continue
        if normalized in title_text:
            score += 8 if normalized not in GENERIC_ACTIVITY_KEYWORDS else 2

    if not is_activity_relevant(title, keyword=keyword, category=category, metadata=metadata):
        score -= 100

    return score


def choose_best_activity(candidates, keyword: str = "", category: str = "", metadata: Optional[dict] = None):
    if not candidates:
        return None

    ranked = sorted(
        candidates,
        key=lambda item: score_activity_candidate(item, keyword=keyword, category=category, metadata=metadata),
        reverse=True,
    )
    return ranked[0]


def search_activities(keyword: str, excluded_ids=None, category: str = "", metadata: Optional[dict] = None):
    """Search Bilibili activities/topics and return ordered candidates."""
    excluded = {int(x) for x in (excluded_ids or []) if x}
    if not keyword or not _is_plain_keyword(keyword):
        return []

    from core.services.bili_api import get_bilibili_credentials, search_mission_activities, create_session

    # Use get_bilibili_credentials() which tries browser first, then falls back to cookies.json
    cookies_data = get_bilibili_credentials()
    if not cookies_data or 'cookie_info' not in cookies_data:
        log.warning("无法获取 Bilibili 凭证（浏览器 + cookies.json 均失败），活动搜索跳过")
        return []

    try:
        session = create_session(cookies_data)
        candidates = []
        seen_ids = set()

        for query in expand_activity_keywords(keyword, category, metadata):
            log.info(f"🔍 正在搜索 B站活动: {query}")
            results = search_mission_activities(query, session)
            for item in results or []:
                mission_id = item.get('mission_id', 0)
                if not mission_id:
                    continue
                mission_id = int(mission_id)
                if mission_id in excluded or mission_id in seen_ids:
                    continue
                title = item.get('name', '')
                if not is_activity_relevant(title, keyword=query, category=category, metadata=metadata):
                    continue
                seen_ids.add(mission_id)
                candidates.append({
                    "id": mission_id,
                    "title": title,
                    "url": f"https://www.bilibili.com/blackboard/dynamic/{mission_id}",
                    "matched_keyword": query,
                })

        if candidates:
            candidates = sorted(
                candidates,
                key=lambda item: score_activity_candidate(item, keyword=keyword, category=category, metadata=metadata),
                reverse=True,
            )
            log.info(
                "✅ 找到活动候选: " +
                ", ".join(f"{c['title']} (ID: {c['id']})" for c in candidates[:3])
            )
        else:
            log.warning(f"❌ 未找到与 '{keyword}' 相关的可用活动 (Internal API)")
        return candidates

    except Exception as e:
        log.error(f"活动搜索出错: {e}")
        return []


def search_activity(keyword: str, excluded_ids=None, category: str = "", metadata: Optional[dict] = None):
    """
    Search for Bilibili activities/topics by keyword.
    Returns the first matching activity found, or None.
    
    Returns dict:
    {
        "id": int,       # Mission ID
        "title": str,    # Activity Title
        "url": str       # Activity URL
    }
    """
    candidates = search_activities(keyword, excluded_ids=excluded_ids, category=category, metadata=metadata)
    return choose_best_activity(candidates, keyword=keyword, category=category, metadata=metadata)

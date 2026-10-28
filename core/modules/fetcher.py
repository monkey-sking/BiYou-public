#!/usr/bin/env python3
"""
获取最新视频信息并翻译
从本地元数据服务获取最新视频，翻译标题和描述为中文
"""
import json
import io
import os
import sys
import copy
import time
import urllib.request

from core.utils.config import load_config
from core.utils.i18n import print_t
from core.utils.logger import log
from core.services.bili_cache import get_bili_cache

# Load configuration
CONFIG = load_config()
METADATA_FILE = CONFIG['metadata_file']


VIDEO_DIR = CONFIG.get('video_source_dir')

def ensure_dir(path):
    if not path:
        return
    if not os.path.exists(path):
        os.makedirs(path)

def get_latest_video(target_path=None):
    """
    获取最新视频信息
    如果提供了 target_path，直接使用该文件
    否则优先查找本地 video_source_dir 下的最新视频文件和对应的 .info.json
    """
    try:
        # History Directory
        history_dir = os.path.join(os.path.dirname(METADATA_FILE), 'history')
        if not os.path.exists(history_dir):
            os.makedirs(history_dir)
            
        # 1. 尝试从本地目录查找
        target_video = None
        if target_path:
            print_t('fetch_used_arg', os.path.basename(target_path))
            target_video = os.path.basename(target_path)
            # Ensure we are working with the absolute path correctly if needed, 
            # but logic below expects filename relative to VIDEO_DIR usually, or we adapt.
            # Actually code below joins VIDEO_DIR with target_video. 
            # If target_path is absolute, we should handle it.
            if os.path.isabs(target_path):
                 # If it's absolute, we use it directly
                 pass
            
        if not target_video and os.path.exists(VIDEO_DIR):
            files = [f for f in os.listdir(VIDEO_DIR) if f.endswith(('.mp4', '.mkv', '.webm'))]
            if files:
                # 按修改时间排序
                files.sort(key=lambda x: os.path.getmtime(os.path.join(VIDEO_DIR, x)), reverse=True)
                
                # 遍历寻找未处理的视频
                
                for video_file in files:
                    # check if in history
                    base = os.path.splitext(video_file)[0]
                    history_path = os.path.join(history_dir, f"{base}.json")
                    if os.path.exists(history_path):
                        # Skip processed video
                        continue
                    
                    target_video = video_file
                    break
        
        if not target_video:
            from core.utils.i18n import i18n
            log.info(i18n.get('fetch_none'))
            return None

        if target_path and os.path.isabs(target_path):
             video_path = target_path
             target_video = os.path.basename(target_path)
        else:
             video_path = os.path.join(VIDEO_DIR, target_video)
             
        print_t('fetch_found', target_video)
                
        # 尝试读取对应的 metadata json (yt-dlp 生成)
        # Video file: /path/to/Video.mp4 -> /path/to/Video.info.json
        base_name_full = os.path.splitext(video_path)[0]
        json_path = f"{base_name_full}.info.json"
        
        info = {}
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    info = json.load(f)
                print_t('fetch_info_loaded')
            except Exception as e:
                print_t('fetch_info_fail', e)

        # Fallback title must NEVER be a filesystem path. base_name_full = absolute
        # path-without-ext (e.g. ${HOME}/Downloads/Videos/Recipe.mp4 -> "/.../Recipe"),
        # which leaks the directory into the title and gets "translated"/uploaded as-is.
        # Use a bare basename-only fallback so a missing .info.json degrades to the
        # filename, never a path. (2026-08-19: BV1cN876ZEuR / BV1pc876hEBx 因此发布成文件路径标题)
        fallback_title = os.path.basename(base_name_full)
        return {
            "title": info.get('title', fallback_title),
            "description": info.get('description', ''),
            "video_path": video_path,
            "id": info.get('id'),
            "tags": info.get('tags', []),
            "info_dict": info
        }

    except Exception as e:
        print_t('fetch_fail', e)
        return None

def preserve_original_cover(video_path: str, info: dict) -> str:
    """
    Ensures the original thumbnail from the source is preserved as 'orig_cover.jpg'.
    """
    import shutil
    base_name_full = os.path.splitext(video_path)[0]
    target_orig = f"{base_name_full}_orig_cover.jpg"
    
    if os.path.exists(target_orig):
        return target_orig
        
    # Potential source thumbnails from yt-dlp
    sources = [
        f"{base_name_full}.webp",
        f"{base_name_full}.jpg",
        f"{base_name_full}.png"
    ]
    
    for s in sources:
        if os.path.exists(s):
            try:
                # If it's already a jpg, just copy. If webp/png, convert to jpg.
                from PIL import Image
                img = Image.open(s)
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                img.save(target_orig, "JPEG", quality=95)
                log.info(f"📸 Preserved original cover: {target_orig}")
                return target_orig
            except Exception as e:
                log.warning(f"⚠️ Failed to preserve cover {s}: {e}")
                
    return None


def download_original_cover_from_url(video_path: str, thumb_url: str) -> str:
    """
    Download a remote thumbnail and normalize it into *_orig_cover.jpg.
    This keeps the cover workflow working even when metadata is available
    before yt-dlp writes any local thumbnail files.
    """
    if not video_path or not thumb_url:
        return None

    base_name_full = os.path.splitext(video_path)[0]
    target_orig = f"{base_name_full}_orig_cover.jpg"
    if os.path.exists(target_orig):
        return target_orig

    try:
        from PIL import Image

        req = urllib.request.Request(
            thumb_url,
            headers={
                "User-Agent": CONFIG.get(
                    "user_agent",
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
                )
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
        if not raw:
            return None

        img = Image.open(io.BytesIO(raw))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(target_orig, "JPEG", quality=95)
        log.info(f"📸 Downloaded original cover from remote thumbnail: {target_orig}")
        return target_orig
    except Exception as e:
        log.warning(f"⚠️ Failed to download remote thumbnail {thumb_url}: {e}")
        return None


def translate_text(text, target="zh"):
    """
    使用 Gemini API 翻译文本
    """
    try:
        from core.modules.translator import translate_text as gemini_translate
        return gemini_translate(text, target, context="fitness video")
    except ImportError:
        print_t('trans_module_missing')
        return text
    except Exception as e:
        print_t('fetch_trans_fail', e)
        return text


def auto_translate_metadata(title: str, description: str, source_lang: str = "en", target_lang: str = "zh") -> tuple[str, str]:
    """
    自动翻译视频标题和描述
    使用 Gemini API 一次性翻译，效果比逐句翻译更好
    
    Args:
        title: 原标题
        description: 原描述
        source_lang: 源语言 (en/zh)
        target_lang: 目标语言 (en/zh)
    """
    try:
        from core.modules.translator import translate_metadata_sync
        print_t('fetch_translating')
        translated_title, translated_desc = translate_metadata_sync(title, description, source_lang, target_lang)
        print_t('fetch_trans_done')
        return translated_title, translated_desc
    except ImportError:
        print_t('trans_module_missing')
        return title, description
    except Exception as e:
        print_t('fetch_trans_fail', e)
        return title, description


def is_english_title(title: str) -> bool:
    """
    检测标题是否为英文
    如果标题中英文字符占比超过 50%，认为是英文
    """
    import re
    if not title:
        return False
    english_chars = len(re.findall(r'[a-zA-Z]', title))
    total_chars = len(title.replace(' ', ''))
    if total_chars == 0:
        return False
    return english_chars / total_chars > 0.5


def generate_ai_tags(title, description):
    """
    使用 Gemini 生成更精准的标签
    """
    try:
        from core.modules.translator import get_gemini_client
        from core.utils.config import load_config
        
        config = load_config()
        # Use a faster model for tags if possible, but existing client logic is fine
        
        # Simple synchronous wrapper logic since we are inside sync function workflow
        # Ideally we should make the whole fetcher async, but for now we can rely on heuristic fallback
        # or use the translate module's infrastructure if it supports arbitrary prompts.
        # Let's reuse translate_with_gemini infrastructure but with a custom prompt.
        
        import asyncio
        from core.modules.translator import translate_with_gemini
        
        # We cheat a bit by using the translate function's plumbing but ignoring the 'translate' part of the prompt
        # Actually, let's just make a new simple function in translator or do it here.
        # For simplicity and speed in this fix, let's sticking to a better keyword list for now
        # AND invoking a new helper if I can modify translator.py.
        # But to avoid breaking changes, I'll significantly expand the keyword list and logic first.
        # If user wants AI tags, we should add that to translator.py properly.
        pass
    except:
        pass

    # Enhanced Keyword Matching Strategy
    # 扩大关键词库
    fitness_keywords = {
        "hiit": ["HIIT", "燃脂", "高强度间歇", "有氧"],
        "tabata": ["Tabata", "燃脂", "高强度"],
        "abs": ["腹肌", "马甲线", "核心力量", "虐腹"],
        "core": ["核心", "腹肌", "稳定性"],
        "leg": ["瘦腿", "腿部训练", "美腿", "臀腿"],
        "glute": ["蜜桃臀", "臀部训练", "练臀", "提臀"],
        "arm": ["瘦手臂", "手臂训练", "拜拜肉", "天鹅臂"],
        "upper body": ["上肢训练", "美背", "直角肩"],
        "lower body": ["下肢训练", "瘦腿", "臀腿"],
        "full body": ["全身燃脂", "全身运动", "居家健身", "全身塑形"],
        "dumbbell": ["哑铃", "负重训练", "力量训练"],
        "kettlebell": ["壶铃", "力量训练"],
        "resistance band": ["弹力带", "小阻力带"],
        "no equipment": ["无器械", "徒手健身", "居家运动"],
        "home": ["居家健身", "跟练"],
        "beginner": ["新手友好", "入门"],
        "intermediate": ["进阶"],
        "advanced": ["挑战"],
        "stretch": ["拉伸", "放松", "柔韧性"],
        "yoga": ["瑜伽", "塑形"],
        "pilates": ["普拉提", "塑形", "核心"],
        "warm up": ["热身", "激活"],
        "cool down": ["拉伸", "冷身"],
        "challenge": ["挑战", "打卡"],
        "grow games": ["Grow Games", "健身挑战"], # Specific for current channel
    }
    
    content = (title + " " + description).lower()

    fitness_evidence_terms = [
        "fitness", "workout", "hiit", "yoga", "pilates", "dumbbell",
        "strength", "cardio", "training", "fat burn", "burn", "calorie",
        "健身", "训练", "燃脂", "减脂", "力量", "拉伸", "瑜伽", "普拉提", "有氧",
    ]
    has_fitness_evidence = any(term in content for term in fitness_evidence_terms)

    tags = []
    if has_fitness_evidence:
        tags = ["健身", "运动", "跟练"]  # Base tags only when evidence exists
        for keyword, tag_list in fitness_keywords.items():
            if keyword in content:
                tags.extend(tag_list)
    
    # Deduplicate and limit
    unique_tags = list(dict.fromkeys(tags))
    
    # Final Fallback: If still empty (e.g. title has no english keywords), split title
    if len(unique_tags) < 3:
        # Simple segmentation for Chinese title if needed, or just split by space
        # For now, just add some generic ones if completely empty
        if not unique_tags:
            unique_tags = ["生活", "日常", "视频"]
            
        # Add parts of title as tags if they are long enough (simple heuristic)
        # clean title
        import re
        clean_title = re.sub(r'[^\w\s]', ' ', title)
        parts = clean_title.split()
        for p in parts:
             if len(p) > 1 and p not in unique_tags:
                 unique_tags.append(p)

    return unique_tags[:15] # Increase limit to 15

# 导入统一的分类规则
try:
    from core.modules.organize_collections import COLLECTION_RULES, classify_video, DEFAULT_COLLECTION
except ImportError:
    # Fallback if module issue
    COLLECTION_RULES = []
    DEFAULT_COLLECTION = "精选合辑（多源）"
    def classify_video(title, rules): return DEFAULT_COLLECTION



def suggest_collection(title: str, description: str) -> str:
    """
    推荐合集: 规则优先，规则没匹配到再用 Gemini 查线上合集
    """
    # 1. 规则分类优先
    rule_based = classify_video(title, COLLECTION_RULES)
    if rule_based != DEFAULT_COLLECTION:
        log.info(f"📏 规则匹配合集: {rule_based}")
        return rule_based

    # 2. 频道未命中 CHANNEL_COLLECTIONS 且规则未命中 → 统一进收容合集。
    #    合集已改为「按搬运源 UP 主」划分（2026-09-26），不再按话题/AI 新建合集，
    #    否则会把已删除的话题合集（UE5 游戏开发教程 / AI 黑科技前沿 / 其他精选 …）重新建出来。
    #    下面的 Gemini 兜底保留但默认不启用；如需恢复，删掉这行 return 即可。
    log.info(f"📂 未命中频道映射，归入收容合集: {DEFAULT_COLLECTION}")
    return DEFAULT_COLLECTION

    # 2b. (已停用) 规则没匹配到，尝试调 Gemini + 线上合集
    try:
        import google.generativeai as genai
        from core.utils.config import get_gemini_key

        genai.configure(api_key=get_gemini_key())
        model = genai.GenerativeModel('gemini-2.5-flash')

        # 尝试获取 B站现有合集
        existing_names = []
        try:
            from core.services.bili_collection_api import BiliCollectionAPI
            api = BiliCollectionAPI()
            my_collections = api.get_my_collections()
            existing_names = [c['title'] for c in my_collections]
            log.info(f"📚 获取到 {len(existing_names)} 个现有合集")
        except Exception as e:
            log.warning(f"无法获取线上合集列表: {e}")

        if not existing_names:
            return DEFAULT_COLLECTION

        # 3. 构建候选列表: 线上合集 + 规则合集, 但排除"其他精选"
        rule_candidates = [r['name'] for r in COLLECTION_RULES]
        candidates = list(dict.fromkeys(existing_names + rule_candidates))
        existing_list_str = chr(10).join(f"- {name}" for name in candidates)

        prompts = CONFIG.get('prompts', {})
        prompt_template = prompts.get('suggest_collection')

        if not prompt_template:
            return DEFAULT_COLLECTION

        prompt = prompt_template.format(
            existing_collections=existing_list_str,
            title=title,
            description=description[:500] + "..."
        )

        response = model.generate_content(prompt)
        suggested = response.text.strip()

        # 验证: Gemini 必须从候选列表中选一个
        for name in candidates:
            if name in suggested or suggested in name:
                log.info(f"🤖 AI 推荐合集: {name}")
                return name

        # 如果 Gemini 返回了新名字且合法，接受
        if suggested and len(suggested) < 20:
            log.info(f"🤖 AI 新合集名: {suggested}")
            return suggested

        return DEFAULT_COLLECTION

    except Exception as e:
        log.warning(f"AI 合集推荐失败: {e}")
        return DEFAULT_COLLECTION


def should_refresh_existing_collection(existing_meta: dict) -> bool:
    """
    判断现有 metadata 中的合集推荐是否需要刷新。
    当线上新增了更合适的合集，旧的 suggested_collection 可能已经过时。
    """
    current_collection = (
        existing_meta.get("season")
        or existing_meta.get("suggested_collection")
        or existing_meta.get("collection")
        or ""
    ).strip()

    # 合集改为「按搬运源 UP 主」划分后，判定依据是频道映射，
    # 而不是按标题关键词/AI 推荐（那套已废弃，会把话题合集重新建出来）。
    try:
        info = existing_meta.get("info_dict") or {}
        channel_id = (info.get("channel_id") or info.get("uploader_id") or "").strip()
        from core.modules.organize_collections import CHANNEL_COLLECTIONS
        expected_collection = CHANNEL_COLLECTIONS.get(channel_id, DEFAULT_COLLECTION)
    except Exception as e:
        log.warning(f"⚠️ 刷新合集推荐时失败，跳过本次判定: {e}")
        return False

    if not current_collection:
        return bool(expected_collection)

    return bool(expected_collection and expected_collection != current_collection)


def prepare_metadata(video_info, cn_title, cn_description, category="生活-日常"):
    """准备上传所需的元数据"""
    # 0. 按频道映射优先（channel_id > 关键词 > AI）
    collection = ""
    info_dict = video_info.get("info_dict", {}) or {}
    channel_id = info_dict.get("channel_id", "") or info_dict.get("uploader_id", "")
    channel_name = info_dict.get("channel", "") or info_dict.get("uploader", "")
    if channel_id:
        from core.modules.organize_collections import CHANNEL_COLLECTIONS
        if channel_id in CHANNEL_COLLECTIONS:
            collection = CHANNEL_COLLECTIONS[channel_id]
            log.info(f"📺 频道匹配合集 [{channel_name}]: {collection}")

    # 1. 生成标签
    tags = generate_ai_tags(cn_title, cn_description)

    # 2. 推荐合集（频道未匹配时才走关键词/AI）
    if not collection:
        collection = suggest_collection(cn_title, cn_description)
        log.info(f"📂 AI 推荐合集: {collection}")

    # 3. 推荐系列（按内容主题；与合集互为补充，一条稿件可同时属于两者）
    try:
        from core.modules.series_rules import suggest_series
        series = suggest_series(cn_title, channel_id)
        log.info(f"🗂 内容系列: {series}")
    except Exception as e:
        log.warning(f"系列推荐失败，跳过: {e}")
        series = ""

    # Safety check for tags
    if not tags:
        tags = ["生活", "日常", "视频"]

    metadata = {
        "original_title": video_info.get("title", ""),
        "title": cn_title,
        "description": cn_description,
        "tags": tags,
        "category": category,
        "video_path": video_info.get("video_path", ""),
        "cover_path_16_9": "",
        "cover_path_4_3": "",
        "suggested_collection": collection,  # 合集 = 按搬运源 UP 主
        "suggested_series": series,          # 系列 = 按内容主题
    }
    return metadata


HISTORY_FILE = os.path.join(CONFIG.get('project_root', os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'history')


def load_metadata(path=None):
    """从 JSON 文件加载元数据"""
    target = path if path else METADATA_FILE
    if os.path.exists(target):
        with open(target, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_metadata(metadata, path=None):
    """保存元数据到 JSON 文件"""
    target = path if path else METADATA_FILE
    # ensure dir exists
    ensure_dir(os.path.dirname(target))
    
    with open(target, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False, indent=4)
        
    print_t('fetch_metadata_saved', target)


def preserve_existing_processing_artifacts(existing_metadata, refreshed_metadata):
    merged = dict(refreshed_metadata or {})
    if not isinstance(existing_metadata, dict):
        return merged

    preserved_keys = (
        "publish_records",
        "uploaded_bvid",
        "uploaded_aid",
        "resource_links",
        "cover_path",
        "cover_path_16_9",
        "cover_path_4_3",
        "cover_assets",
        "platform_payloads",
        "orig_cover_path",
    )
    for key in preserved_keys:
        value = existing_metadata.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (dict, list)):
            merged[key] = copy.deepcopy(value)
        else:
            merged[key] = value

    return merged


def process_metadata(video_info):
    """
    Core metadata processing logic: Translate -> AI Analyze -> Save
    
    Args:
        video_info: Video information dict
        
    Returns: path to saved metadata json
    """
    original_title = video_info.get('title', 'N/A')
    original_desc = video_info.get('description', 'N/A')
    
    # Default direction: y2b (En -> Zh)
    source_lang, target_lang = "en", "zh"
    from core.utils.i18n import i18n
    print(i18n.get('fetch_trans_direction_y2b'))
    
    # 2. 自动翻译 (无需用户确认)
    print("\n" + "-"*50)
    print_t('trans_auto')
    print("-"*50)
    
    translated_title, translated_description = auto_translate_metadata(
        original_title, original_desc, source_lang, target_lang
    )
    
    print_t('fetch_cn_title', translated_title)
    print_t('fetch_cn_desc', translated_description[:100])
    
    # Ensure we have valid values
    if not translated_title:
        translated_title = original_title
    if not translated_description:
        translated_description = original_desc
    # 2026-09-01 (Shorts/无描述视频修复): 源描述为空时用标题生成 fallback 描述，
    # 否则 translated_description 为空 → 上传资产预检(Missing: Description)失败。
    if not translated_description:
        translated_description = (
            f"🎬 {translated_title or original_title or '视频搬运'}\n\n"
            "搬运自 YouTube，感谢原作者的精彩内容！觉得有用记得点赞收藏关注～"
        )
    
    
    # 3. AI 分析内容 (分区 & 标签)
    print("\n" + "-"*50)
    print_t('ai_analyzing')
    try:
        from core.modules.translator import analyze_video_content_sync
        ai_category, ai_tags = analyze_video_content_sync(translated_title, translated_description)
        print_t('ai_cat', ai_category)
        
        # Join tags manually for display to avoid confusing format string
        tags_display = ', '.join(ai_tags[:5])
        print_t('ai_tags', tags_display)
    except Exception as e:
        print_t('ai_fail', e)
        ai_category = "生活-日常"
        ai_category = "生活-日常"
        ai_tags = generate_ai_tags(translated_title, translated_description)

    # 3.5 话题/活动推荐 (NEW)
    print("\n" + "-"*50)
    print_t('ai_activity_check')
    mission_id = 0
    mission_name = "" # NEW
    topic_keyword = ""
    activity_context = {
        "title": translated_title,
        "description": translated_description,
        "original_title": original_title,
        "original_description": original_desc,
        "tags": ai_tags or [],
    }
    try:
        from core.modules.translator import recommend_activity_keyword_sync
        from core.services.bili_activity import search_activity, should_reuse_cached_mission
        
        # 1. Ask AI for keyword
        topic_keyword = recommend_activity_keyword_sync(translated_title, translated_description)
        if topic_keyword:
            print(f"🤖 AI 推荐搜索关键词: {topic_keyword}")
            
            # --- Cache Check ---
            bili_cache = get_bili_cache()
            cached_mission = bili_cache.get_mission(topic_keyword, max_age_hours=12)
            if cached_mission and should_reuse_cached_mission(topic_keyword, activity_context):
                mission_id = cached_mission['id']
                mission_name = cached_mission['name']
                print(f"⚡️ 从本地缓存获取到活动: {mission_name} (ID: {mission_id})")
            else:
                if cached_mission:
                    print(f"🧭 关键词 '{topic_keyword}' 过于宽泛，跳过缓存并重新搜索活动")
                # 2. Search Bilibili
                activity = search_activity(topic_keyword, category=ai_category, metadata=activity_context)
                if activity:
                    # Safety Check: Reject "萌宠"
                    act_title = activity['title']
                    if "萌宠" in act_title and "萌宠" not in translated_title and "萌宠" not in translated_description:
                        print(f"⚠️ 拒绝不相关的活动: {act_title} (关键词: {topic_keyword})")
                        mission_id = 0
                    else:
                        mission_id = activity['id']
                        mission_name = activity['title']
                        print(f"✅ 自动关联活动: {activity['title']} (ID: {mission_id})")
                        # Update Cache
                        bili_cache.update_mission(topic_keyword, mission_id, mission_name)
                else:
                    print(f"⚠️ 未找到相关活动")
            print("🔹 AI 未推荐特定活动关键词")
            
    except Exception as e:
        print(f"⚠️ 活动推荐失败: {e}")

    # 3.5.1 定义验证函数 (内部函数或移至模块级)
    def validate_mission(mission_name, title, desc):
        """
        Validate if a mission/activity is relevant to the video content.
        Returns: (is_valid, reason)
        """
        if not mission_name: return False, "Empty name"
        
        # 1. Reject specific irrelevant keywords if they don't appear in content
        # "气象" (Meteorological) -> often mistakenly matched for "科普" (Science)
        # "萌宠" (Pet) -> often mistaken for AI/Tech
        risk_map = {
            "气象": ["气象", "天气", "台风", "暴雨"],
            "萌宠": ["萌宠", "猫", "狗", "宠物"],
            "游戏": ["游戏", "电竞", "手游", "端游", "Steam"],
            "原神": ["原神"],
            "王者荣耀": ["王者荣耀"],
        }
        
        for risk_word, required_keywords in risk_map.items():
            if risk_word in mission_name:
                # Check if ANY required keyword is in title or description
                content = (title + desc).lower()
                has_relevance = any(kw in content for kw in required_keywords)
                if not has_relevance:
                    return False, f"Risk word '{risk_word}' found but content irrelevant"

        return True, "Passed"

    # Validate the ID if we found one
    if mission_id and mission_name:
        is_valid, reason = validate_mission(mission_name, translated_title, translated_description)
        if not is_valid:
             print(f"❌ 自动关联活动未通过验证: {mission_name} (原因: {reason})")
             mission_id = 0
             mission_name = ""

    # 3.6 兜底搜索 (如果初次尝试失败，使用分区名作为关键词)
    if not mission_id and ai_category:
        try:
            from core.services.bili_activity import search_activity
            # 提取分区中的核心词 (e.g. "运动-健身" -> "健身", "知识-科学科普" -> "科普")
            sub_cat = ai_category.split('-')[-1].split('·')[0]
            
            # 准备搜索候选词
            candidates = [sub_cat]
            if len(sub_cat) > 2:
                 # 比如 "科学科普" -> 也尝试 "科普"
                 if "科普" in sub_cat: candidates.append("科普")
                 if "游戏" in sub_cat: candidates.append("游戏")
                 # 这种简单的规则能覆盖大部分 B站活动的命名习惯
                 candidates.append(sub_cat[-2:])
            
            # Add "科技" for anything related to Science/Knowledge/Tech if missing
            if "科普" in candidates or "科学" in candidates or "知识" in ai_category:
                candidates.insert(0, "科技") # High priority fallback for tech
            
            for fallback_keyword in candidates:
                if len(fallback_keyword) < 2: continue
                
                print(f"🔄 正在尝试兜底搜索关键词: {fallback_keyword}")
                activity = search_activity(fallback_keyword, category=ai_category, metadata=activity_context)
                if activity:
                    # Validate fallback result
                    t_name = activity['title']
                    is_valid, reason = validate_mission(t_name, translated_title, translated_description)
                    if is_valid:
                        mission_id = activity['id']
                        mission_name = t_name 
                        topic_keyword = fallback_keyword
                        print(f"✅ 兜底获取到活动: {t_name} (ID: {mission_id})")
                        break
                    else:
                        print(f"⚠️ 兜底活动 '{t_name}' 未通过验证: {reason}")
                        
        except Exception as e:
            print(f"⚠️ 兜底活动搜索失败: {e}")

    # 4. 准备并保存元数据
    metadata = prepare_metadata(video_info, translated_title, translated_description, ai_category)
    if ai_tags:
        metadata['tags'] = ai_tags  # Override with AI tags if available
    metadata['direction'] = 'y2b'
    metadata['original_title'] = original_title
    metadata['original_description'] = original_desc
    try:
        from core.kb.resource_links import attach_resource_links
        metadata = attach_resource_links(metadata, config=CONFIG)
    except Exception as e:
        log.warning(f"⚠️ Resource link extraction skipped: {e}")
    
    # NEW: Save paths from info (populated by downloader/get_latest_video)
    metadata['video_path'] = video_info.get("video_path", "")
    
    # --- Original Cover Preservation ---
    orig_cover = preserve_original_cover(metadata['video_path'], video_info.get("info_dict", {}))
    if not orig_cover:
        orig_cover = download_original_cover_from_url(
            metadata['video_path'],
            video_info.get("thumbnail"),
        )
    metadata['cover_path'] = video_info.get("cover_path", "") # Critical for next step
    if orig_cover:
        metadata['orig_cover_path'] = orig_cover
    
    if mission_id:
        metadata['mission_id'] = mission_id
        metadata['mission_name'] = mission_name # NEW
        metadata['topic_keyword'] = topic_keyword
        metadata['cached_at'] = int(time.time())
    
    # Save to videos/VideoName.json
    video_path_abs = video_info.get("video_path", "")
    base_name = os.path.splitext(os.path.basename(video_path_abs))[0]
    json_path = os.path.join(os.path.dirname(video_path_abs), f"{base_name}.json")

    if os.path.exists(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                existing_meta = json.load(f)
            metadata = preserve_existing_processing_artifacts(existing_meta, metadata)
        except Exception as e:
            log.warning(f"⚠️ Failed to preserve existing processing artifacts: {e}")
    
    save_metadata(metadata, path=json_path)
    
    print("\n" + "="*50)
    print_t('fetch_ready')
    print("="*50)
    
    return json_path


def main(auto_confirm=False, target_path=None):
    print("\n" + "="*50)
    print_t('fetch_title')
    print("="*50 + "\n")
    
    # 1. 获取最新视频
    video_info = get_latest_video(target_path)
    if not video_info:
        return
    
    original_title = video_info.get('title', 'N/A')
    original_desc = video_info.get('description', 'N/A')
    
    print_t('fetch_original_title', original_title)
    print_t('fetch_original_desc', original_desc[:100])
    
    # Resume Check (Interactive for CLI)
    video_path = video_info.get('video_path', '')
    video_name = os.path.splitext(os.path.basename(video_path))[0]
    video_meta_file = os.path.join(os.path.dirname(video_path), f"{video_name}.json")
    
    if os.path.exists(video_meta_file):
        try:
            with open(video_meta_file, 'r', encoding='utf-8') as f:
                existing_meta = json.load(f)
            from core.services.bili_activity import should_refresh_existing_mission
            
            print("\n" + "*"*50)
            print_t('fetch_resume_found')
            print(f"   已翻译标题: {existing_meta.get('title')}")
            should_refresh_mission = should_refresh_existing_mission(existing_meta)
            should_refresh_collection = should_refresh_existing_collection(existing_meta)
            
            # Interactive Prompt
            if auto_confirm:
                if should_refresh_mission or should_refresh_collection:
                    print("⚡️ [Auto] Existing metadata looks stale, re-processing...")
                    resume = 'n'
                else:
                    print(f"⚡️ [Auto] Resuming...")
                    resume = 'y'
            else:
                 from core.utils.i18n import i18n
                 with i18n.suspend_background_logging():
                    print("\n" + "="*50)
                    if should_refresh_mission:
                        print("💡 建议：该文件当前绑定的话题与内容不匹配，输入 'n' 将重新分析并改写话题。")
                    if should_refresh_collection:
                        print("💡 建议：该文件当前推荐的合集可能已经过时，输入 'n' 将重新分析并刷新合集推荐。")
                    print_t('fetch_resume_prompt')
                    print_t('fetch_resume_hint')
                    print("="*50)
                    prompt = i18n.get('prompt_input')
                    print(prompt, end='', flush=True)
                    resume = input().strip().lower()

            if resume != 'n':
                from core.utils.i18n import i18n
                print(i18n.get('fetch_resume_skip'))
                return video_meta_file
        except Exception as e:
            from core.utils.i18n import i18n
            print(f"⚠️ {i18n.get('fetch_check_err')}: {e}")
            
    # Process
    return process_metadata(video_info)
    

if __name__ == "__main__":
    main()

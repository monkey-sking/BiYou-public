#!/usr/bin/env python3
"""
使用 biliup 上传视频到 Bilibili
从 video_metadata.json 读取信息，自动上传视频和封面 (支持 16:9 和 4:3)
支持定时发布功能
"""
import json
import os
import argparse
import sys
import io
from dataclasses import dataclass
from datetime import datetime, timedelta
import time
import asyncio
import re
from contextlib import redirect_stdout, redirect_stderr
from core.services.subtitle_translator import SubtitleTranslator
from core.utils.config import load_config
from core.utils.time_utils import parse_schedule_time
from core.utils.logger import log
from core.utils.i18n import print_t
from core.services.bili_cache import get_bili_cache
from core.utils.text_utils import clean_bilibili_description

# 加载配置
CONFIG = load_config()


def _get_min_video_size() -> int:
    """Minimum accepted video file size in bytes (config overridable)."""
    try:
        upload_cfg = (CONFIG or {}).get("upload") or {}
        val = upload_cfg.get("min_video_size_bytes")
        if val:
            return int(val)
    except Exception:
        pass
    return 1024 * 1024  # 1MB default
VERBOSE_CONSOLE_LOGS = bool(CONFIG.get("verbose_console_logs", False))

# 分区关键词推断规则: (关键词列表, 分区名, TID)
# 顺序即优先级, 命中即停。TID 见 config/category_map.yaml
# 注意: 不要用泛化的"引擎"(游戏分析/3D也提引擎), 用具体引擎名
CATEGORY_KEYWORD_RULES = [
    (["UE5", "UE6", "Unreal", "虚幻", "蓝图", "Niagara", "Nanite", "MCP", "Sequencer", "Epic"], "知识-计算机技术", 231),
    (["空气炸锅", "食谱", "美食", "料理", "烘焙", "菜谱", "好吃", "早餐", "甜点", "烤箱", "油炸", "cook", "煎蛋", "香迷糊", "香晕"], "美食-美食制作", 76),
    (["HIIT", "燃脂", "健身", "减脂", "哑铃", "搏击", "有氧", "训练", "腹肌", "普拉提", "Pilates", "workout", "塑形", "提臀"], "运动-健身", 164),
    (["游戏", "GTA", "战地", "荒野大镖客", "使命召唤", "R星", "育碧", "Steam", "开放世界", "游戏史", "The Finals", "游戏开发", "独立游戏"], "游戏-单机游戏", 17),
    (["3D", "Blender", "建模", "渲染", "材质", "拓扑", "三维", "Mesh"], "知识-设计·创意", 229),
    (["AI日报", "AI", "GPT", "Claude", "Gemini", "DeepMind", "大模型", "机器学习", "深度学习", "论文", "科研", "OpenAI"], "知识-科学科普", 201),
    (["英语", "听力", "口语", "美语", "单词", "口音", "空耳"], "知识-科学科普", 201),
]

def infer_category_by_keywords(title: str, description: str):
    """LLM 分区失败时的关键词兜底: 按标题+描述规则推断 (category_name, tid), 推断不出返回 (None, None)."""
    text = f"{title or ''} {description or ''}"
    for keywords, cat_name, tid in CATEGORY_KEYWORD_RULES:
        for kw in keywords:
            if kw.lower() in text.lower():
                return cat_name, tid
    return None, None

# Import necessary classes
from biliup.plugins.bili_webup import BiliBili, Data
from core.modules.fetcher import load_metadata # Reuse fetcher's load logic

# Extend Data class to support cover43 and dtime
@dataclass
class ExtendedData(Data):
    cover43: str = ''
    dtime: int = 0  # 定时发布时间戳 (10位 Unix timestamp)
    mission_id: int = 0 # 话题/活动 ID (Mission ID)



from core.utils.cookie import get_bilibili_cookies, save_cookies_to_file
from core.services.bili_api import get_bilibili_credentials, upload_subtitle, get_recommended_activities
from PIL import Image

# Define METADATA_FILE and COOKIE_FILE after CONFIG is loaded
METADATA_FILE = CONFIG['metadata_file']
COOKIE_FILE = CONFIG['cookie_file']


def _run_with_captured_output(fn, *args, **kwargs):
    """
    Run noisy third-party calls and capture stdout/stderr.
    Returns (result, output, error).
    """
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer), redirect_stderr(buffer):
            result = fn(*args, **kwargs)
        return result, buffer.getvalue(), None
    except Exception as e:
        return None, buffer.getvalue(), e


def _print_captured_tail(captured: str, max_lines: int = 8):
    """Show a short tail of captured output when an error happens."""
    if not captured:
        return
    lines = [ln for ln in captured.strip().splitlines() if ln.strip()]
    if not lines:
        return
    if not VERBOSE_CONSOLE_LOGS:
        log.warning("🔎 第三方组件详细调试输出已捕获（开启 verbose_console_logs 可在控制台查看）")
        for ln in lines[-max_lines:]:
            log.debug(f"[uploader-captured] {ln}")
        return

    print("🔎 调试输出(最后几行):")
    for ln in lines[-max_lines:]:
        print(f"   {ln}")


def validate_and_fix_tags(tags: list, metadata: dict) -> list:
    """
    验证并修复标签
    - 每个标签不超过 20 字符
    - 总数不超过 12 个
    - 批量翻译英文标签为中文（单次 LLM 调用）
    """
    from core.publishing.platform_payloads import _is_predominantly_english

    raw_tags = [tag.strip() for tag in tags[:12] if tag.strip()]
    if not raw_tags:
        return raw_tags

    # Batch-translate all English tags in one LLM call
    english_tags = [t for t in raw_tags if _is_predominantly_english(t)]
    if english_tags:
        try:
            joined = ", ".join(english_tags)
            custom_prompt = (
                "将以下英文标签翻译为简洁的中文标签（适合中文视频平台搜索），"
                "每个标签不超过 8 个字，用逗号分隔，不要编号，不要解释，只输出翻译结果：\n"
                f"{joined}"
            )
            # Use LLMFactory directly to avoid translate_text's own prompt wrapper
            from core.services.llm.factory import LLMFactory
            from core.modules.translator import run_async_task
            llm = LLMFactory.get_service('translation')
            result = run_async_task(llm.generate_text(custom_prompt))
            # Parse comma-separated translations
            translated_list = [t.strip().strip("'\"") for t in result.replace("，", ",").split(",") if t.strip()]

            # Build a mapping from original English tag to translated tag
            tag_map = {}
            for i, eng in enumerate(english_tags):
                if i < len(translated_list) and translated_list[i]:
                    tag_map[eng] = translated_list[i]
                else:
                    tag_map[eng] = eng  # fallback: keep original

            # Apply translations
            new_tags = []
            for t in raw_tags:
                if t in tag_map:
                    translated = tag_map[t]
                    if len(translated) <= 20:
                        log.info(f"✅ 标签已翻译: {t} -> {translated}")
                        new_tags.append(translated)
                    else:
                        truncated = translated[:20]
                        log.warning(f"⚠️ 标签翻译后过长，已截断: {translated} -> {truncated}")
                        new_tags.append(truncated)
                else:
                    new_tags.append(t)
            raw_tags = new_tags
        except Exception as e:
            log.warning(f"⚠️ 批量标签翻译失败: {e}")

    # Truncate any remaining long tags
    fixed_tags = []
    for tag in raw_tags:
        if len(tag) > 20:
            log.warning(f"⚠️ 标签过长，已截断: {tag} -> {tag[:20]}")
            tag = tag[:20]
        fixed_tags.append(tag)

    if len(fixed_tags) < len(tags):
        log.warning(f"⚠️ 标签数量超过限制，已截断: {len(tags)} -> {len(fixed_tags)}")

    return fixed_tags


def validate_and_fix_partition(category: str, metadata: dict) -> str:
    """
    验证并修正分区
    """
    title = metadata.get('title', '').lower()
    description = metadata.get('description', '').lower()

    # 游戏相关关键词
    game_keywords = ['unreal', 'ue5', 'ue4', 'unity', 'game', '游戏', '开发', 'godot', 'gamedev']
    # 教程相关关键词
    tutorial_keywords = ['tutorial', 'guide', 'how to', '教程', '指南', 'learn', '学习']

    # 检测是否为游戏教程
    is_game = any(kw in title or kw in description for kw in game_keywords)
    is_tutorial = any(kw in title or kw in description for kw in tutorial_keywords)

    if is_game and is_tutorial:
        # 游戏教程应该在"知识-科学科普"分区
        if category == "生活-日常":
            log.warning(f"⚠️ 检测到游戏教程内容，修正分区: {category} -> 知识-科学科普")
            return "知识-科学科普"

    return category


def is_topic_relevant(topic_name: str, metadata: dict) -> bool:
    """
    检查话题是否与内容相关
    """
    if not topic_name:
        return False

    title = metadata.get('title', '').lower()
    description = metadata.get('description', '').lower()
    topic_lower = topic_name.lower()

    # 提取话题关键词
    topic_keywords = topic_lower.split()

    # 检查是否有任何关键词匹配
    for keyword in topic_keywords:
        if len(keyword) < 2:  # 跳过太短的词
            continue
        if keyword in title or keyword in description:
            return True

    return False


def _get_activity_candidates_from_metadata(metadata, category):
    candidates = []
    topic_keyword = metadata.get('topic_keyword')
    if topic_keyword:
        candidates.append(topic_keyword)

    if category:
        sub_cat = category.split('-')[-1].split('·')[0]
        if sub_cat:
            candidates.append(sub_cat)
            if len(sub_cat) > 2:
                candidates.append(sub_cat[-2:])
            if "科普" in sub_cat:
                candidates.append("科技")

    seen = set()
    ordered = []
    for item in candidates:
        if not item or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _resolve_replacement_activity(metadata, category, tid, session, excluded_ids=None):
    excluded = {int(x) for x in (excluded_ids or []) if x}
    bili_cache = get_bili_cache()

    try:
        from core.services.bili_activity import search_activity
    except Exception:
        search_activity = None

    for keyword in _get_activity_candidates_from_metadata(metadata, category):
        if not search_activity:
            break
        try:
            activity = search_activity(
                keyword,
                excluded_ids=excluded,
                category=category,
                metadata=metadata,
            )
            if activity:
                bili_cache.update_mission(keyword, activity['id'], activity['title'])
                return activity
        except Exception as exc:
            print(f"⚠️ 关键词 '{keyword}' 重新搜索活动失败: {exc}")

    recommendations = get_recommended_activities(tid, session)
    for item in recommendations or []:
        mission_id = item.get('id') or item.get('mission_id')
        if not mission_id:
            continue
        mission_id = int(mission_id)
        if mission_id in excluded:
            continue
        return {
            "id": mission_id,
            "title": item.get('title') or item.get('name') or f"活动 {mission_id}",
            "url": item.get('url', ''),
        }

    return None


def read_interactive_line(prompt_text: str, default: str = "") -> str:
    """
    Read interactive input from the controlling terminal when possible.
    This avoids stuck/no-echo prompts after multi-step or multi-URL workflows.
    """
    from core.utils.i18n import i18n

    with i18n.suspend_background_logging():
        print(prompt_text, end='', flush=True)

        try:
            with open('/dev/tty', 'r', encoding='utf-8', errors='ignore') as tty:
                line = tty.readline()
                if line:
                    return line.strip()
        except Exception:
            pass

        try:
            return input().strip()
        except EOFError:
            return default
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log.warning(f"⚠️ 读取交互输入失败，使用默认值: {e}")
            return default


def compress_image(path, max_size=4.5 * 1024 * 1024):
    """
    Compress image if larger than max_size (default 4.5MB).
    Returns path to compressed image (temp file) or original path.
    """
    if not os.path.exists(path):
        return path

    file_size = os.path.getsize(path)
    if file_size <= max_size:
        return path

    print(f"📉 Image too large ({file_size / 1024 / 1024:.2f}MB). Compressing...")

    try:
        img = Image.open(path)

        # Convert to RGB if needed (e.g. RGBA -> JPEG)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        # Create temp file
        temp_dir = os.path.join(os.path.dirname(path), "temp_covers")
        os.makedirs(temp_dir, exist_ok=True)
        filename = os.path.basename(path)
        name, ext = os.path.splitext(filename)
        temp_path = os.path.join(temp_dir, f"{name}_compressed.jpg")

        # Compress
        quality = 85
        while quality > 10:
            img.save(temp_path, "JPEG", quality=quality)
            if os.path.getsize(temp_path) <= max_size:
                print(f"✅ Compressed to {os.path.getsize(temp_path) / 1024 / 1024:.2f}MB (Quality: {quality})")
                return temp_path
            quality -= 10

        print("⚠️ Warning: Could not compress below target size.")
        return temp_path

    except Exception as e:
        print(f"⚠️ Compression failed: {e}")
        return path


def preprocess_metadata(metadata: dict) -> dict:
    """
    预处理元数据，确保所有字段符合要求
    - 翻译英文标题和描述
    - 翻译英文标签
    - 验证和修复分区
    - 验证话题相关性
    """
    from core.modules.translator import translate_text
    from core.publishing.platform_payloads import _is_predominantly_english

    # 1. 验证和修复标题
    title = metadata.get('title', '')
    # Defensive guard: a title must never be (or contain) a filesystem path.
    # Leaked paths come from get_latest_video falling back to base_name_full when
    # .info.json is missing (anti-bot browser-download path). If a path leaks through
    # here, BLOCK upload instead of publishing a path as the title. (2026-08-19)
    #
    # 2026-08-21 收紧: 原判断 `'/' in title` 误伤合法标题中的斜杠
    # (实证: "【UE5/6】..." 被误当路径阻断上传, 造成 watchdog 无限重启死循环)。
    # 真正的路径泄漏特征 = 以 `/` 开头的绝对路径, 或含 macOS 路径片段
    # (Volumes/  Users/  .mp4/.webm/.mkv/.json 等), 而非标题里恰好含一个 `/`。
    if title:
        _t = title.strip()
        _lower = _t.lower()
        _is_leaked_path = (
            _t.startswith(('/'))
            or ('Volumes/' in _t or 'Users/' in _t)
            or ('/' in _t and _lower.endswith(('.mp4', '.webm', '.mkv', '.mov', '.json', '.flv', '.ts')))
        )
        if _is_leaked_path:
            log.error(f"❌ 标题疑似文件路径, 阻断上传: {title[:80]!r}")
            raise ValueError(f"标题疑似文件路径, 阻断上传: {title[:50]!r}")
    if title and _is_predominantly_english(title):
        log.warning(f"⚠️ 检测到英文标题，尝试翻译: {title}")
        try:
            translated = translate_text(title, target="zh")
            if translated and translated.strip():
                # 2026-08-14 修复: translate_text() 失败时返回原文(不抛异常),
                # 且 Gemini 可能返回"翻译结果"仍是英文 → 与原文相同或仍为英文主导 =
                # 翻译未成功。必须阻断上传, 否则英文标题被当作"翻译成功"静默发布
                # (实证: BV1jDgA6wEkh / BV1dagP6AEyD 两个视频 2026-08-14 因此发布英文标题)。
                is_same = translated.strip().lower() == title.strip().lower()
                if is_same or _is_predominantly_english(translated):
                    log.error(f"❌ 标题翻译无效 (Gemini 返回原文或仍为英文), 阻断上传: {title[:60]!r} -> {translated[:60]!r}")
                    raise ValueError(f"标题翻译失败: Gemini 未返回中文翻译 (原: {title[:50]!r})")
                metadata['title'] = translated
                log.info(f"✅ 标题已翻译: {title} -> {translated}")
            else:
                log.error(f"❌ 标题翻译结果为空, 阻断上传: {title[:60]!r}")
                raise ValueError(f"标题翻译失败: Gemini 返回空结果 (原: {title[:50]!r})")
        except ValueError:
            raise
        except Exception as e:
            log.error(f"❌ 标题翻译失败, 阻断上传: {e}")
            raise ValueError(f"标题翻译异常: {e}")

    # 2. 翻译英文描述/正文
    description = metadata.get('description', '') or ''
    if description and _is_predominantly_english(description):
        log.warning(f"⚠️ 检测到英文描述，尝试翻译 ({len(description)} chars)")
        # Preserve original before overwriting
        metadata.setdefault('original_description', description)
        try:
            translated_desc = translate_text(description, target="zh")
            if translated_desc and translated_desc.strip():
                metadata['description'] = translated_desc
                log.info(f"✅ 描述已翻译: {description[:60]}... -> {translated_desc[:60]}...")
            else:
                log.warning(f"⚠️ 描述翻译结果为空，保留原描述")
        except Exception as e:
            log.error(f"❌ 描述翻译失败: {e}")

    # 3. 批量翻译英文标签
    tags = metadata.get('tags', [])
    if tags:
        fixed_tags = validate_and_fix_tags(tags, metadata)
        metadata['tags'] = fixed_tags

    # 4. 验证和修复分区
    category = metadata.get('category', '生活-日常')
    fixed_category = validate_and_fix_partition(category, metadata)
    if fixed_category != category:
        metadata['category'] = fixed_category

    # 5. 验证话题相关性
    mission_id = metadata.get('mission_id')
    mission_name = metadata.get('mission_name', '')
    if mission_id and mission_name:
        if not is_topic_relevant(mission_name, metadata):
            log.warning(f"⚠️ 话题 '{mission_name}' 与内容不相关，移除")
            metadata['mission_id'] = 0
            metadata['mission_name'] = ""

    return metadata


def upload_video(metadata, cookie_data, dry_run=False, schedule_time=0, final_video_path=None, season=None, auto_season=False, interactive=False):
    """使用 biliup 上传视频"""
    effect_id = None  # Effect sandwich 记录 id（外层 except 也要用）

    # Support both new 'video_path' and legacy 'file_path' keys
    video_path = metadata.get("video_path") or metadata.get("file_path", "")

    # 优先从 metadata 补充参数 (如果 CLI 未指定)
    if schedule_time == 0:
        schedule_time = int(metadata.get('dtime', 0) or metadata.get('schedule_time', 0))
    
    if not season:
        season = metadata.get('season') or metadata.get('suggested_collection') or metadata.get('collection')
    
    if final_video_path:
        video_path = final_video_path
        
    if not video_path or not os.path.exists(video_path):
        log.error(f"视频文件不存在: {video_path}")
        return False
    
    # 预处理元数据
    metadata = preprocess_metadata(metadata)

    # 准备上传数据
    # 优先使用 metadata 中的数据，如果不存在则使用 config 中的默认值
    title = metadata.get("title") or CONFIG.get("default_title", "未命名视频")
    description = metadata.get("description") or CONFIG.get("default_description", "")
    
    # Clean description (remove Markdown, convert links)
    description = clean_bilibili_description(description)
    # Strip B站-prohibited external platform links and promotions
    from core.utils.bili_compliance import filter_bilibili_description
    description = filter_bilibili_description(description)
    
    tags = metadata.get("tags") or CONFIG.get("default_tags", ["视频"])
    cover_path = metadata.get("cover_path_16_9", "")
    cover_path_4_3 = metadata.get("cover_path_4_3", "")
    
    # Load category map from config file
    from core.utils.config import PROJECT_ROOT
    import yaml
    
    category_map_file = os.path.join(PROJECT_ROOT, "config", "category_map.yaml")
    default_map = {}
    
    if os.path.exists(category_map_file):
        try:
            with open(category_map_file, 'r', encoding='utf-8') as f:
                default_map = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"⚠️ Failed to load category map: {e}")
    
    if not default_map:
        # Fallback minimal map if file missing
        default_map = {"生活-日常": 21}
    
    # Merge config user map
    user_map = CONFIG.get('category_map', {})
    category_map = {**default_map, **user_map}
    category = metadata.get("category", "生活-日常")
    
    # 获取 TID
    tid = category_map.get(category)
    if not tid:
        # 尝试模糊匹配 (e.g. Gemini 仅返回 "健身")
        for k, v in category_map.items():
            if category in k or k.endswith(category):
                tid = v
                print(f"⚠️ 分区 '{category}' 未直接匹配，模糊匹配到 '{k}' (tid={tid})")
                category = k # Update display name
                break
    
    # --- Cache Check for TID ---
    bili_cache = get_bili_cache()
    if not tid:
        cached_tid = bili_cache.get_category_tid(category)
        if cached_tid:
            tid = cached_tid
            print(f"⚡️ 从本地缓存获取到分区 ID: {category} -> {tid}")

    if not tid:
        # 关键词兜底推断 (LLM 分区失败时, 用标题/描述规则推断, 不再默认生活-日常)
        inferred_cat, inferred_tid = infer_category_by_keywords(title, description)
        if inferred_tid and inferred_cat:
            tid = inferred_tid
            category = inferred_cat
            print(f"🔧 关键词推断分区: {category} -> tid={tid}")
            bili_cache.update_category(category, tid)
        else:
            tid = 21 # Last-resort fallback
            print_t('up_cat_wrong', category)
    else:
        # Update cache for TID
        bili_cache.update_category(category, tid)

    # -------------------------------------------------------------------------
    # Subtitle Processing (Early Stage)
    # Check for English subtitles and translate them BEFORE potentially uploading
    # This prepares the files so the upload logic below picks the translated one.
    # -------------------------------------------------------------------------
    base_name = os.path.splitext(video_path)[0]
    
    # Define expected paths
    zh_sub_vtt = f"{base_name}.zh-Hans.vtt" # Preferred
    zh_sub_srt = f"{base_name}.zh-Hans.srt"
    
    en_sub_vtt = f"{base_name}.en.vtt"
    en_sub_srt = f"{base_name}.en.srt"
    
    # If no Chinese sub but English exists, translate it
    if not (os.path.exists(zh_sub_vtt) or os.path.exists(zh_sub_srt)):
        source_sub = None
        if os.path.exists(en_sub_vtt):
            source_sub = en_sub_vtt
        elif os.path.exists(en_sub_srt):
            source_sub = en_sub_srt
            
        if source_sub:
            print_t('up_sub_trans_start', os.path.basename(source_sub))
            
            # Run translation synchronously in this flow (using asyncio.run)
            try:
                # Output strictly .vtt for consistency
                target_sub = zh_sub_vtt 
                source_lang = metadata.get('source_lang', 'en')
                target_lang = metadata.get('target_lang', 'zh')
                success = asyncio.run(SubtitleTranslator.translate_subtitle(source_sub, target_sub, source_lang=source_lang, target_lang=target_lang))
                if success:
                    print_t('up_sub_trans_done', os.path.basename(target_sub))
                else:
                    print_t('up_sub_trans_fail')
            except Exception as e:
                print_t('up_sub_trans_err', e)

    print("\n" + "="*50)
    print_t('up_title')
    print("="*50)
    
    # Display info
    print_t('up_file', os.path.basename(video_path))
    print_t('up_meta_title', title)
    
    # Show description (truncated for concise console readability)
    desc_display = description[:120] + "..." if len(description) > 120 else description
    print_t('up_desc_label', desc_display)
    
    tags_display = ', '.join(tags[:5])
    print_t('up_tags', tags_display)
    print_t('up_cat', category, tid)
    
    cover_16_9 = metadata.get('cover_path_16_9')
    cover_4_3 = metadata.get('cover_path_4_3')
    if cover_16_9:
        print_t('up_cover_16', os.path.basename(cover_16_9))
    if cover_4_3:
        print_t('up_cover_43', os.path.basename(cover_4_3))
    
    # 显示定时发布信息
    if schedule_time > 0:
        schedule_dt = datetime.fromtimestamp(schedule_time)
        print_t('up_schedule', schedule_dt.strftime('%Y-%m-%d %H:%M'))
        
        # 检查是否至少4小时后
        min_time = datetime.now() + timedelta(hours=4)
        if schedule_time < min_time.timestamp():
            print_t('up_schedule_warn')
            print_t('up_schedule_earliest', min_time.strftime('%Y-%m-%d %H:%M'))
    else:
        print_t('up_schedule_now')
        
    print(f"📦 合集设定: {season or '无'}")
    
    mission_id = metadata.get('mission_id') or metadata.get('topic_id') or metadata.get('activity_id')
    
    # --- Fallback: Try to search mission_id by category if missing ---
    if not mission_id and category:
        print(f"🔍 检查到元数据缺失活动 ID，正在尝试根据分类 '{category}' 搜索...")
        try:
            from core.services.bili_activity import search_activity
            # Extract core keyword from category (e.g. "生活-日常" -> skip, "运动-健身" -> "健身")
            keyword = category.split('-')[-1]
            if len(keyword) >= 2:
                activity = search_activity(keyword)
                if activity:
                    mission_id = activity['id']
                    mission_name = activity['title']
                    # Inject back to metadata for display and upload
                    metadata['mission_id'] = mission_id
                    metadata['mission_name'] = mission_name
                    print(f"✅ 自动关联活动 (兜底): {mission_name} (ID: {mission_id})")
        except Exception as e:
            print(f"⚠️ 兜底活动搜索失败: {e}")

    # Prioritize name from metadata (dynamic), then config map (fallback)
    mission_name = metadata.get('mission_name') 
    
    if not mission_name and mission_id:
        # Check config map for name as fallback
        topic_map = CONFIG.get('topic_map', {})
        mission_name = topic_map.get(int(mission_id))
        
        # If still no name, try to fetch it dynamically
        if not mission_name:
            print(f"🔍 正在尝试获取话题名称 (ID: {mission_id})...")
            try:
                # Create session for API call
                from core.services.bili_api import create_session, search_mission_activities
                session = create_session(cookie_data)
                
                # Search by ID string
                results = search_mission_activities(str(mission_id), session)
                if results:
                    for topic in results:
                        if int(topic.get('mission_id', 0)) == int(mission_id):
                            mission_name = topic.get('name')
                            # Update metadata for future use (in memory only unless saved)
                            metadata['mission_name'] = mission_name 
                            # Update Cache
                            bili_cache = get_bili_cache()
                            # Use topic_keyword if available in metadata
                            topic_keyword = metadata.get('topic_keyword', mission_name)
                            bili_cache.update_mission(topic_keyword, mission_id, mission_name)
                            break
            except Exception as e:
                print(f"⚠️ 获取话题名称失败: {e}")

    if mission_name:
        print(f"🏷️ 话题: {mission_name} (ID: {mission_id})")
    else:
        print(f"🏷️ 话题 ID: {mission_id if mission_id else '无'}")
    
    # 确认上传
    print("\n" + "-"*50)

    if interactive:
        prompt_text = "确认上传？(y/N): "
        confirm = read_interactive_line(prompt_text, default='n').lower()
        if confirm != 'y':
            print_t('up_cancel')
            return {"success": False}

    if dry_run:
        print_t('up_dry_run')
        print("Assuming success for testing flow.")
        
        # Dry run season logic
        if auto_season:
             s_coll = metadata.get('suggested_collection')
             print(f"⚡️ [Dry Run] Would add to AI collection: {s_coll}")
        elif season:
             print(f"⚡️ [Dry Run] Would add to collection: {season}")
             
        return {"success": True, "bvid": "BV_DRY_RUN_TEST", "aid": 123456}

    # Input prompt is hard to i18n cleanly without changing logic or adding a getter
    # For now, let's keep input simple or use raw input string from i18n?
    # Actually i18n.get is available if we import instance
    from core.utils.i18n import i18n
    
    print_t('up_start')
    
    try:
        # 准备数据对象 (ExtendedData logic unchanged)
        video_data = ExtendedData()
        video_data.title = title[:80]
        video_data.desc = description[:2000]
        video_data.set_tag(tags[:10])
        video_data.tid = tid
        
        # Read copyright from metadata (priority) or global config (default: 1)
        video_data.copyright = int(metadata.get("copyright") or CONFIG.get("bilibili_copyright", 1))

        # 话题/活动 ID
        mission_id = metadata.get('mission_id') or metadata.get('topic_id') or metadata.get('activity_id')
        if mission_id:
             video_data.mission_id = int(mission_id)
        
        if schedule_time > 0:
            video_data.dtime = schedule_time
        
        # 初始化上传器
        with BiliBili(video_data) as bili:
            # Hack: Inject headers to fix -101 error
            from core.utils.cookie import get_user_agent
            ua = get_user_agent()
            bili._BiliBili__session.headers.update({
                "User-Agent": ua,
                "Referer": "https://www.bilibili.com/",
                "Origin": "https://www.bilibili.com"
            })
            
            # Login with provided cookie data
            bili.login_by_cookies(cookie_data)
            
            # Helper to upload cover without cropping (biliup forces 16:10 crop)
            def upload_cover_raw(bili_instance, path):
                import base64
                with open(path, 'rb') as f:
                    raw_data = f.read()
                
                # Use private session from bili instance
                csrf_token = cookie_data.get('bili_jct') or bili_instance._BiliBili__bili_jct
                r = bili_instance._BiliBili__session.post(
                    url='https://member.bilibili.com/x/vu/web/cover/up',
                    data={
                        'cover': b'data:image/jpeg;base64,' + base64.b64encode(raw_data),
                        'csrf': csrf_token
                    }, timeout=30
                )
                try:
                    res = r.json()
                except Exception as e:
                    print(f"❌ Cover upload failed (JSON Decode Error).")
                    print(f"Status Code: {r.status_code}")
                    print(f"Response: {r.text[:1000]}") # Print first 1000 chars
                    return None
                if res.get('data') is None:
                    print(f"⚠️ Cover upload failed: {res}")
                    return None
                return res['data']['url']

            # 1. 上传 16:9 封面
            if cover_path and os.path.exists(cover_path):
                print_t('up_cover_16', os.path.basename(cover_path))
                # Compress if needed
                ready_cover_path = compress_image(cover_path)
                # Use raw upload to avoid auto-cropping by biliup
                cover_url = upload_cover_raw(bili, ready_cover_path)
                if cover_url:
                    video_data.cover = cover_url.replace("http:", "")
                    if VERBOSE_CONSOLE_LOGS:
                        print(f"   URL: {cover_url}")
            
            # 2. 上传 4:3 封面 (如果存在)
            if cover_path_4_3 and os.path.exists(cover_path_4_3):
                print_t('up_cover_43', os.path.basename(cover_path_4_3))
                # Compress if needed
                ready_cover_path_4_3 = compress_image(cover_path_4_3)
                # 4:3 must not be cropped to 16:10
                cover43_url = upload_cover_raw(bili, ready_cover_path_4_3)
                if cover43_url:
                    video_data.cover43 = cover43_url.replace("http:", "")
                    # We also add it dynamically to the object in case 'cover43' isn't the right key
                    setattr(video_data, 'recreate_cover', cover43_url.replace("http:", ""))
                    if VERBOSE_CONSOLE_LOGS:
                        print(f"   URL: {cover43_url}")

            # 3. 上传视频
            print_t('up_video', os.path.basename(video_path))
            if VERBOSE_CONSOLE_LOGS:
                video_part = bili.upload_file(video_path)
            else:
                video_part, captured_upload, upload_err = _run_with_captured_output(bili.upload_file, video_path)
                if upload_err:
                    _print_captured_tail(captured_upload)
                    raise upload_err
            video_part["title"] = title[:80]
            video_data.append(video_part)
            
            # 提交
            print_t('up_submit')

            # Effect sandwich: 发布副作用前写入 intent。
            # 若进程在 submit() 与结算之间崩溃，history 里会留下 pending 记录，
            # dedup 检查据此阻止盲目重传（历史上重复视频的根因）。
            try:
                from core.utils.effects import record_effect
                effect_id = record_effect(metadata, "bili_upload", "pending",
                                          payload={"title": title, "video_path": video_path})
            except Exception as _effect_err:
                log.warning(f"⚠️ 记录上传 intent 失败（不影响上传）: {_effect_err}")

            try:
                if VERBOSE_CONSOLE_LOGS:
                    result = bili.submit()
                else:
                    result, captured_submit, submit_err = _run_with_captured_output(bili.submit)
                    if submit_err:
                        _print_captured_tail(captured_submit)
                        raise submit_err
            except Exception as e:
                # 处理活动/话题过期错误: {'code': 21004, 'message': '该活动不在有效的开始和结束日期', 'ttl': 1}
                err_msg = str(e)
                if '21004' in err_msg and video_data.mission_id:
                    print(f"\n⚠️ 话题/活动 ID {video_data.mission_id} 无效或已过期 (Error 21004)")
                    expired_mission_id = int(video_data.mission_id)
                    topic_keyword = metadata.get('topic_keyword')
                    bili_cache = get_bili_cache()
                    bili_cache.remove_mission(keyword=topic_keyword, mission_id=expired_mission_id)

                    print("🔍 正在重新选择可用活动...")
                    replacement = _resolve_replacement_activity(
                        metadata,
                        category,
                        video_data.tid,
                        bili._BiliBili__session,
                        excluded_ids={expired_mission_id},
                    )
                    if replacement:
                        new_id = replacement.get('id')
                        new_title = replacement.get('title')
                        print(f"✅ 已切换到可用活动: {new_title} (ID: {new_id})")
                        video_data.mission_id = int(new_id)
                        metadata['mission_id'] = int(new_id)
                        metadata['mission_name'] = new_title
                    else:
                        print("❌ 未找到可用活动，停止提交，避免无活动发布。")
                        raise RuntimeError("No valid Bilibili activity available after mission expiry (21004).")
                    
                    if VERBOSE_CONSOLE_LOGS:
                        result = bili.submit()
                    else:
                        result, captured_submit, submit_err = _run_with_captured_output(bili.submit)
                        if submit_err:
                            _print_captured_tail(captured_submit)
                            raise submit_err
                else:
                    raise e
            
            print("\n" + "="*50)
            print_t('up_success')
            print("="*50)
            
            # Extract ID from result
            try:
                data = result.get('data', {})
                bvid = data.get('bvid')
                aid = data.get('aid')
            except:
                bvid = None
                aid = None
            
            if not bvid and isinstance(result, str):
                 bvid = result
            
            # Display clean BVID
            if bvid:
                print(f"\n🔗 BVID: {bvid}")

            # Effect sandwich: 发布完成 → 结算。bvid 立即落 history，
            # 崩溃后 dedup 看到 settled 直接跳过，不会再传一遍。
            if effect_id:
                try:
                    from core.utils.effects import record_effect
                    record_effect(metadata, "bili_upload", "settled",
                                  result={"bvid": bvid, "aid": aid}, effect_id=effect_id)
                except Exception as _effect_err:
                    log.warning(f"⚠️ 记录上传 settlement 失败: {_effect_err}")

            metadata["mission_id"] = int(getattr(video_data, "mission_id", 0) or 0)
                 
            # 4. 加入合集
            try:
                target_season = season
                if auto_season and not target_season:
                    target_season = metadata.get('suggested_collection')
                
                if target_season:
                    print(f"📂 正在将视频加入合集: {target_season}")
                    from core.services.bili_collection_api import BiliCollectionAPI, create_session
                    # Reuse session if possible or create new from cookie_data
                    # cookie_data here is dict, create_session handles it
                    session = create_session(cookie_data)
                    api = BiliCollectionAPI(session)
                    
                    # Get or create
                    collection = api.get_or_create_collection(target_season)
                    if collection:
                        # Add video (using aid from result)
                        # 用稿件标题作为本集标题
                        episode_title = title
                        if aid:
                            api.add_video_to_collection(collection['id'], aid, title=episode_title)
                        elif bvid:
                            api.add_video_by_bvid(collection['id'], bvid)
                    else:
                        print(f"⚠️ 无法获取合集信息: {target_season}")

            except Exception as e:
                print(f"⚠️ 加入合集失败: {e}")

            # 4b. 加入系列（按内容主题，与合集互为补充；一条稿件可同时属于两者）
            try:
                target_series = metadata.get('suggested_series')
                if target_series and aid:
                    from core.services.bili_series_api import add_video_to_series
                    print(f"🗂 正在将视频加入系列: {target_series}")
                    if add_video_to_series(target_series, aid):
                        metadata["joined_series"] = target_series
                elif not target_series:
                    print("ℹ️ 无内容系列推荐，跳过加入系列")
            except Exception as e:
                print(f"⚠️ 加入系列失败: {e}")

            # 5. 尝试上传字幕 (Disabled by user request, use `by -t` or `--sync-subtitles` later)
            # if bvid:
            #     base_name = os.path.splitext(video_path)[0]
            #     ...
                 
            return {"success": True, "bvid": bvid, "aid": aid}
            
    except Exception as e:
        import traceback
        print_t('up_fail', e)
        traceback.print_exc()
        # Effect sandwich: 失败也结算为 failed，避免 pending 永远卡住下游 dedup
        try:
            if effect_id:
                from core.utils.effects import record_effect
                record_effect(metadata, "bili_upload", "failed",
                              result={"error": str(e)}, effect_id=effect_id)
        except Exception:
            pass
        return {"success": False}


def validate_upload_assets(metadata, targets=None):
    """
    Validate that all required assets for upload are present.
    Returns: (is_valid, missing_items)
    """
    missing_items = []
    target_set = {str(target).strip().lower() for target in (targets or ["bilibili"]) if target}

    # Hydrate cover paths from disk so stale/collapsed-space paths get corrected
    try:
        from core.publishing.platform_payloads import hydrate_cover_metadata_from_disk
        metadata = hydrate_cover_metadata_from_disk(metadata, targets=sorted(target_set) or None)
    except Exception as e:
        log.warning(f"⚠️ Cover metadata hydration failed: {e}")

    # 1. Video file
    video_path = metadata.get("video_path") or metadata.get("file_path", "")
    if not video_path:
        missing_items.append("Video Path (Missing in metadata)")
    elif not os.path.exists(video_path):
        missing_items.append(f"Video File (Not found: {video_path})")
    elif os.path.getsize(video_path) < _get_min_video_size(): # configurable minimum (default 1MB)
        missing_items.append(f"Video File (Too small: {os.path.getsize(video_path)} bytes)")

    # 2. Basic Metadata
    if not metadata.get("title"):
        missing_items.append("Title")
    if not metadata.get("description"):
        missing_items.append("Description")
    if not metadata.get("tags") or not isinstance(metadata.get("tags"), list) or len(metadata.get("tags")) == 0:
        # 自动用默认标签兜底，而不是报"Tags缺失"导致失败
        default_tags = CONFIG.get("default_tags", ["视频"])
        if default_tags:
            metadata["tags"] = list(default_tags)
            log.warning(f"⚠️ 标签为空，已自动填充默认标签: {default_tags}")
        else:
            missing_items.append("Tags")

    # 3. Covers
    requires_cover_16x9 = bool(target_set)
    requires_cover_4x3 = "bilibili" in target_set

    if requires_cover_16x9:
        cover_16x9 = metadata.get("cover_path_16_9")
        if not cover_16x9:
            missing_items.append("16:9 Cover (Missing path)")
        elif not os.path.exists(cover_16x9):
            missing_items.append(f"16:9 Cover (Not found: {cover_16x9})")

    if requires_cover_4x3:
        cover_4x3 = metadata.get("cover_path_4_3")
        if not cover_4x3:
            missing_items.append("4:3 Cover (Missing path)")
        elif not os.path.exists(cover_4x3):
            missing_items.append(f"4:3 Cover (Not found: {cover_4x3})")

    try:
        from core.publishing.platform_payloads import build_platform_payloads

        platform_payloads = build_platform_payloads(metadata, sorted(target_set))
    except Exception:
        platform_payloads = {}

    for target in sorted(target_set):
        payload = platform_payloads.get(target) or {}
        cover_path = str(payload.get("cover_path", "")).strip()
        base_cover_16x9 = str(metadata.get("cover_path_16_9", "")).strip()
        if cover_path and cover_path != base_cover_16x9 and not os.path.exists(cover_path):
            missing_items.append(f"{target} Cover (Not found: {cover_path})")

        cover_path_4x3 = str(payload.get("cover_path_4_3", "")).strip()
        base_cover_4x3 = str(metadata.get("cover_path_4_3", "")).strip()
        if (
            target == "bilibili"
            and cover_path_4x3
            and cover_path_4x3 != base_cover_4x3
            and not os.path.exists(cover_path_4x3)
        ):
            missing_items.append(f"{target} 4:3 Cover (Not found: {cover_path_4x3})")

    return len(missing_items) == 0, missing_items


def run_upload(dry_run=False, schedule_time=0, auto_comment=False, metadata_path=None, auto_confirm=False, season=None, auto_season=False, interactive=False):
    """
    执行上传流程（供外部调用）
    """
    # Get credentials (in-memory)
    cookie_data = get_bilibili_credentials()
    if not cookie_data:
        return False
    
    metadata = load_metadata(path=metadata_path)
    if not metadata:
        return False
    
    result = upload_video(metadata, cookie_data, dry_run=dry_run, schedule_time=schedule_time, season=season, auto_season=auto_season, interactive=interactive)
    
    if result and result.get("success"):
        # Save the uploaded IDs back to metadata for other scripts to use
        metadata["uploaded_bvid"] = result.get("bvid")
        metadata["uploaded_aid"] = result.get("aid")
        try:
            from core.kb.store import store_resource_links_from_metadata
            store_resource_links_from_metadata(metadata)
        except Exception as e:
            log.warning(f"⚠️ 资源链接 KB 写入失败: {e}")
        
        # Save back to file
        try:
            target = metadata_path if metadata_path else METADATA_FILE
            with open(target, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, ensure_ascii=False, indent=4)
            print(f"✅ 已保存上传信息到: {target}")
        except Exception as e:
            print(f"⚠️ 保存上传信息失败: {e}")

        if dry_run:
            print("⚡️ DRY RUN: Upload simulation complete.")
            return True

        # 自动评论
        if auto_comment:
            try:
                from core.modules.commenter import post_comment_flow
                post_comment_flow(dry_run=False, metadata_path=metadata_path, cookies=cookie_data)
            except Exception as e:
                print(f"⚠️ 自动评论失败: {e}")

        # 多平台发布（快手等）— 同步调用，adapter 内部自己管 asyncio
        try:
            from core.publishing.manager import PublishManager
            from core.publishing.models import PublishJob, ScheduleIntent
            from core.utils.config import load_config
            config = load_config()
            targets = config.get("publishing", {}).get("default_targets", ["bilibili"])
            non_bili = [t for t in targets if t != "bilibili"]
            if non_bili:
                from core.publishing.adapters.kuaishou_sau import KuaishouSAUAdapter
                publishing_cfg = config.get("publishing", {})
                automation_cfg = publishing_cfg.get("automation", {})
                adapter_cfg = publishing_cfg.get("kuaishou", {})
                selectors = adapter_cfg.get("selectors", {})
                adapter = KuaishouSAUAdapter(config=config, selectors=selectors)
                manager = PublishManager({"kuaishou": adapter})
                job = PublishJob(
                    platform_targets=["kuaishou"],
                    video_path=metadata.get("video_path", ""),
                    title=metadata.get("title", ""),
                    description=metadata.get("description", ""),
                    cover_path=metadata.get("cover_path_16_9", ""),
                    tags=metadata.get("tags", []),
                    schedule_intent=ScheduleIntent(),
                    metadata_path=metadata_path or "",
                    metadata=metadata,
                    platform_payloads=metadata.get("platform_payloads", {}),
                )
                manager.execute(job)
                print(f"✅ 多平台发布完成: {', '.join(non_bili)}")
        except Exception as e:
            # 多平台失败不影响主流程（B站已成功）
            print(f"⚠️ 多平台发布失败 (不影响B站): {e}")

        return True
    
    return False


def main():
    parser = argparse.ArgumentParser(description='Bilibili 视频上传工具')
    parser.add_argument('-n', '--dry-run', action='store_true', 
                        help='模拟上传，不实际发送网络请求')
    parser.add_argument('-s', '--schedule', type=str, default='',
                        help='定时发布时间 (格式: "2024-01-08 18:00", "+4h", "+1d", 或 Unix 时间戳)')
    parser.add_argument('-m', '--metadata', type=str, help='元数据 JSON 文件路径')
    parser.add_argument('--season', type=str, help='指定合集名称或ID')
    parser.add_argument('--auto-season', action='store_true', help='使用AI推荐的合集')
    parser.add_argument('-y', '--yes', action='store_true', help='自动确认上传')
    args = parser.parse_args()
    
    # 解析定时发布时间
    schedule_time = 0
    if args.schedule:
        schedule_time = parse_schedule_time(args.schedule)
        if schedule_time == 0:
            print("❌ 无法解析定时发布时间，已取消")
            return

    from core.utils.i18n import i18n
    
    # CLI mode confirmation
    if not args.dry_run and not args.yes:
        prompt = i18n.get('up_confirm')
        confirm = read_interactive_line(f"\n{prompt}", default='n').lower()
            
        if confirm != 'y':
            print("Operation cancelled.")
            return

    run_upload(dry_run=args.dry_run, schedule_time=schedule_time, auto_comment=True, metadata_path=args.metadata, auto_confirm=args.yes, season=args.season, auto_season=args.auto_season)


if __name__ == "__main__":
    main()

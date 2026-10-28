#!/usr/bin/env python3
"""
使用 LLM Service 进行翻译
"""

import asyncio
import atexit
import os
import sys
import threading
import re

from core.utils.config import load_config
from core.services.llm.factory import LLMFactory
from core.utils.logger import log


class _AsyncLoopRunner:
    """Run async coroutines on a shared background loop for sync call sites."""
    _loop = None
    _thread = None
    _lock = threading.Lock()

    @classmethod
    def _ensure_loop(cls):
        with cls._lock:
            if cls._loop and cls._thread and cls._thread.is_alive():
                return cls._loop

            loop = asyncio.new_event_loop()
            thread = threading.Thread(target=cls._run_loop, args=(loop,), daemon=True, name="translator-async-loop")
            thread.start()
            cls._loop = loop
            cls._thread = thread
            return cls._loop

    @staticmethod
    def _run_loop(loop):
        asyncio.set_event_loop(loop)
        loop.run_forever()

    @classmethod
    def run(cls, coro):
        loop = cls._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result()

    @classmethod
    def shutdown(cls):
        from core.utils.cookie import close_gemini_client_sync

        close_gemini_client_sync()
        with cls._lock:
            if cls._loop and cls._loop.is_running():
                cls._loop.call_soon_threadsafe(cls._loop.stop)
            cls._loop = None
            cls._thread = None


atexit.register(_AsyncLoopRunner.shutdown)

# Common Gemini web UI prefixes that should be stripped from responses
_GEMINI_PREFIXES = [
    "Gemini 说",    # Chinese UI
    "Gemini says",  # English UI
    "Gemini wrote", # English UI variant
    "Gemini：",     # Chinese UI colon variant
    "Gemini: ",     # English UI colon variant
]

def _clean_gemini_prefix(text: str) -> str:
    """Strip Gemini web UI conversational prefixes from response text."""
    if not text:
        return text
    for prefix in _GEMINI_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    return text

# 模板残留检测：Gemini 偶尔把 prompt 里的格式指令当输出返回
_TEMPLATE_LEAK_MARKERS = [
    "[END]", "TITLE:", "DESCRIPTION:", "TRANSLATED:",
    "ending with", "以`[END]", "以 [END]",
    "Please provide", "请提供", "you would like me to", "once you",
    "```", "CATEGORY:", "TAGS:",
]

def _looks_like_template_leak(text: str) -> bool:
    """True if Gemini response looks like prompt template leakage, not real translation."""
    if not text:
        return False
    if any(marker in text for marker in _TEMPLATE_LEAK_MARKERS):
        return True
    # 残留描述: 很短 + 包含代码反引号/省略号 + 无明显语义 (如 "``` 以 `" 或 "…` 以 `")
    stripped = text.strip()
    if len(stripped) < 30 and ('`' in stripped or stripped.startswith('...') or stripped.startswith('…')):
        # 排除正常短标题 (如 "健身") — 必须有模板符号特征
        if ('`' in stripped or stripped.count('...') >= 1 or stripped.count('…') >= 1) and len(stripped) < 25:
            return True
    return False

def _is_mostly_target_lang(text: str, target_lang: str = "Chinese") -> bool:
    """Check that translated text is actually in the target language."""
    if not text or not text.strip():
        return False
    if target_lang.lower().startswith("chinese") or target_lang == "zh":
        import re
        # 2026-09-01 (Shorts/带标签标题修复): 剔除 #标签 / emoji / 变体选择符后计算，
        # 否则 "来自 Rick 的一条消息 ♥️ #rickastley" 被标签稀释到 <0.3 误杀真翻译。
        cleaned = re.sub(r"#[^\s#]+", "", text)
        cleaned = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", "", cleaned)
        han = sum(1 for ch in cleaned if '\u4e00' <= ch <= '\u9fff')
        total = sum(1 for ch in cleaned if ch.strip())
        if total == 0:
            return False
        return han / total >= 0.3
    return True

def _validate_translation(text: str, original: str, target_lang: str = "Chinese") -> bool:
    """Combined validation: no template leak, target language, non-empty, changed from original."""
    if not text or not text.strip():
        return False
    if _looks_like_template_leak(text):
        return False
    if not _is_mostly_target_lang(text, target_lang):
        return False
    # Translation should differ from the original
    if text.strip().lower() == original.strip().lower():
        return False
    return True

def load_context():
    """Load local context/style guidelines."""
    import json
    context_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'config', 'context.json')
    if os.path.exists(context_path):
        try:
            with open(context_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            log.warning(f"⚠️ Failed to load context: {e}")
    return {}

async def translate_with_gemini(text: str, target_lang: str = "Chinese", context: str = "") -> str:
    """
    使用 LLM 翻译文本
    """
    llm = LLMFactory.get_service('translation')
    
    # 构建翻译提示词
    config = load_config()
    prompts = config.get('prompts', {})
    
    prompt_template = prompts.get('translation_simple', "")
    if not prompt_template:
        # Fallback to default if not in config
        context_hint = f" This is a {context}." if context else ""
        prompt = f"Translate the following text to {target_lang}.{context_hint}\n" \
                 f"Keep the translation natural and fluent.\n" \
                 f"Start your reply with 'TRANSLATED:' followed by the translated text, nothing else.\n\n" \
                 f"Text to translate:\n{text}"
    else:
        context_hint = f" This is a {context}." if context else ""
        prompt = prompt_template.format(target_lang=target_lang, context_hint=context_hint, text=text)
    
    try:
        result = await llm.generate_text(prompt)
        # Extract content after TRANSLATED: marker
        if 'TRANSLATED:' in result:
            idx = result.index('TRANSLATED:') + len('TRANSLATED:')
            result = result[idx:].strip()
        # Safety: strip common Gemini web UI prefixes
        result = _clean_gemini_prefix(result)
        # 2026-08-09: 校验模板残留, 失败时抛异常让上层回退原文
        if _looks_like_template_leak(result):
            log.warning(f"⚠️ Gemini 翻译返回模板残留, 丢弃: {result[:60]!r}")
            raise ValueError("translation template leak detected")
        if not _validate_translation(result, text, target_lang):
            log.warning(f"⚠️ Gemini 翻译结果无效, 丢弃: {result[:60]!r}")
            raise ValueError("translation validation failed")
        return result
    except Exception as e:
        log.error(f"Translation failed: {e}")
        raise


def translate_text(text: str, target: str = "zh", context: str = "") -> str:
    """
    同步版本的翻译函数
    """
    if not text or not text.strip():
        return text
    
    target_lang_map = {
        "zh": "Chinese",
        "en": "English",
        "ja": "Japanese",
        "ko": "Korean",
    }
    target_lang = target_lang_map.get(target, target)
    
    try:
        return _AsyncLoopRunner.run(translate_with_gemini(text, target_lang, context))
    except Exception as e:
        log.warning(f"Translation failed: {e}")
        return text  # 失败时返回原文


def run_async_task(coro):
    """Run an async task on the shared background loop used by translator/analysis."""
    return _AsyncLoopRunner.run(coro)


async def translate_video_metadata(title: str, description: str, source_lang: str = "en", target_lang: str = "zh") -> tuple[str, str]:
    """
    一次性翻译视频标题和描述
    
    Args:
        title: 原标题
        description: 原描述
        source_lang: 源语言 (en/zh)
        target_lang: 目标语言 (en/zh)
    """
    llm = LLMFactory.get_service('translation')
    
    config = load_config()
    prompts = config.get('prompts', {})
    
    # Determine direction
    if source_lang == "en" and target_lang == "zh":
        # English -> Chinese (Original behavior)
        prompt_template = prompts.get('metadata_translation', "")
        if not prompt_template:
            prompt = f"""Translate the following video metadata from English to Chinese for Bilibili.

Title rules (data-verified from high-performing videos on this channel):
1. Make it catchy with a strong hook at the start — use emotion hooks like 别再/答应我/没想到/震惊/全网爆火/封神/秒杀/彻底 when fitting, never a flat literal translation.
2. Include a concrete number or specific item when the original has one (duration, calories, ingredient, tech keyword, version number).
3. Use an emotion word (神仙/爆爽/炸裂/香迷糊/停不下来/太绝了) where natural.
4. Keep it 15-30 Chinese characters. Formula: 具体食材/动作/数字 + 情绪词 → e.g. "别再只炸薯条了！全网爆火的空气炸锅神仙吃法", "45分钟爆汗700大卡！脂肪粉碎机".
5. Do NOT prefix with 【】 unless the original title already uses brackets; keep the topic keyword (like Day17 / UE5 / 28天) where it adds search value.

Description rules:
1. Translate ALL key information — do NOT summarize or drop details (训练要点/食材清单/技术栈 must survive).
2. Structure the description with clear sections or bullet lines when the original has them.
3. Keep timestamps/table of contents if present.
4. Add one light interaction line at the end in Bilibili style (e.g. "觉得有用记得点赞收藏～", "喜欢这个系列就关注吧") — keep it short and natural.

Return ONLY the translations in this exact format:

TITLE: [Chinese title here]
DESCRIPTION: [Chinese description here]

Original Title:
{title}
 
Original Description:
{description}
 
---
Local Context for Style/Guidelines:
{context_text}
---"""
        else:
            context_data = load_context()
            context_text = context_data.get('style_guidelines', '')
            prompt = prompt_template.format(
                title=title, 
                description=description,
                source_lang=source_lang,
                target_lang=target_lang,
                context_text=context_text
            )
    else:
        # Chinese -> English (Reverse sync / Global direction)
        prompt = f"""Translate the following video metadata from Chinese to English.
Make the translation natural and suitable for YouTube's international audience.

Requirements:
1. Keep titles catchy but also SEO-friendly for YouTube (include relevant keywords)
2. Translate the description FULLY. Do not summarize or shorten.
3. Adapt Chinese-specific references to be understandable globally (e.g., explain platform names if necessary)
4. Return ONLY the translations in this exact format:

TITLE: [English title here]
DESCRIPTION: [English description here]

---
Original Title:
{title}

Original Description:
{description}
---"""

    try:
        result = await llm.generate_text(prompt)
        
        # 解析结果
        translated_title = title  # 默认使用原文
        translated_description = description
        
        # Find TITLE: line
        if 'TITLE:' in result:
            title_start = result.index('TITLE:') + len('TITLE:')
            # Title ends at newline
            title_end = result.find('\n', title_start)
            if title_end == -1:
                title_end = len(result)
            translated_title = result[title_start:title_end].strip()

        # Find DESCRIPTION: and capture everything after it
        if 'DESCRIPTION:' in result:
            desc_start = result.index('DESCRIPTION:') + len('DESCRIPTION:')
            translated_description = result[desc_start:].strip()

            # Remove [END] if present to avoid leaking it to Bilibili
            if '[END]' in translated_description:
                translated_description = translated_description[:translated_description.index('[END]')].strip()

        # 2026-08-09 修复: Gemini 偶发返回 prompt 模板残留 (如 "请提供标题和描述…以[END]结尾")
        # 必须校验翻译质量, 失败则回退原文, 避免把模板话术发布到 B站
        if translated_title != title and not _validate_translation(translated_title, title, target_lang):
            log.warning(f"⚠️ 翻译标题疑似模板残留, 回退原文: {translated_title[:60]!r}")
            translated_title = title
        if translated_description != description and _looks_like_template_leak(translated_description):
            log.warning(f"⚠️ 翻译描述疑似模板残留, 回退原文: {translated_description[:60]!r}")
            translated_description = description

        return translated_title, translated_description
        
    except Exception as e:
        log.error(f"Metadata translation failed: {e}")
        return title, description


def translate_metadata_sync(title: str, description: str, source_lang: str = "en", target_lang: str = "zh") -> tuple[str, str]:
    """同步版本"""
    return _AsyncLoopRunner.run(translate_video_metadata(title, description, source_lang, target_lang))



async def analyze_video_content(title: str, description: str) -> tuple[str, list]:
    """
    使用 LLM 分析视频内容，推荐分区和标签
    """
    llm = LLMFactory.get_service('analysis')
    
    # Bilibili Categories
    categories = """
    运动-健身, 运动-篮球·足球, 运动-竞技体育, 运动-运动文化, 运动-运动综合
    生活-日常, 生活-搞笑, 生活-家居房产, 生活-手工, 生活-绘画
    知识-科学科普, 知识-计算机技术, 知识-设计·创意, 知识-财经商业, 知识-人文历史
    科技-数码, 科技-软件应用, 科技-计算机技术, 科技-科工机械, 科技-极客DIY
    美食-美食制作, 美食-美食测评, 美食-美食记录, 美食-美食侦探
    游戏-单机游戏, 游戏-手机游戏, 游戏-网络游戏, 游戏-GMV
    时尚-美妆护肤, 时尚-穿搭, 时尚-时尚潮流
    影视-影视杂谈, 影视-短片, 影视-AI影像
    """
    # Truncated list for brevity in code, prompt usually handles it fine. 
    # Use config map maybe? But prompt needs context. 
    # Let's keep the short hardcoded list or expand it if needed.
    
    # Bilibili Categories
    # (Leaving default list logic or moving to config is separate)
    config = load_config()
    prompts = config.get('prompts', {})
    prompt_template = prompts.get('content_analysis', "")
    
    context_data = load_context()
    pref_tags = ", ".join(context_data.get('preferred_tags', []))
    style = context_data.get('style_guidelines', '')

    if not prompt_template:
       prompt = f"""Analyze the following video metadata and recommend the best Bilibili partition (category) and tags.

Video Title: {title}
Video Description: {description}

Available Categories:
{categories}

Instructions:
1. Select the MOST relevant category from the list above. Follow these content-type rules (they override personal judgment):
   - UE5 / Unreal / 虚幻引擎 / 蓝图 / Niagera / 引擎 / 游戏开发引擎 / MCP → 知识-计算机技术 (or 科技-计算机技术)
   - 空气炸锅 / 食谱 / 美食 / 料理 / 烘焙 / 菜谱 / cooking → 美食-美食制作
   - HIIT / 健身 / 燃脂 / 减脂 / 哑铃 / 训练 / 搏击 / 普拉提 / pilates → 运动-健身
   - AI / 大模型 / GPT / 机器学习 / 深度学习 / 论文 / 科研 → 知识-科学科普
   - 3D建模 / Blender / 渲染 / 材质 / 拓扑 → 知识-设计·创意 (or 科技-软件应用)
   - 游戏 / GTA / 战地 / 游戏分析 / 游戏史 → 游戏-单机游戏 (or 游戏-GMV)
   - 英语 / 听力 / 口语 / 学习 → 知识-校园学习 (if present) or 知识-科学科普
   - 3D生成 / AI视频 / 动画 → 科技-软件应用 or 影视-AI影像
   Never use 生活-日常 for tech/food/fitness content — it is only for genuine daily-life vlogs.
2. Generate 10-15 relevant tags (in Chinese) that are popular and searchable. Mix three kinds:
   - Category/vertical tags (类别词: 美食教程/健身跟练/UE5教程/AI资讯)
   - Specific tags from the title (具体词: 空气炸锅/普拉提/臀部训练/避障系统)
   - Search-friendly angle tags (搜索热词/情绪词: 减脂餐/无器械/居家健身/暴汗燃脂/神仙吃法)
   Avoid generic one-char or filler tags (生活/日常/视频).
3. Output strictly in the following format:

CATEGORY: [Selected Category]
TAGS: [tag1, tag2, tag3, ...]
 
---
User Preferred Tags (Prioritize if relevant):
{pref_tags}
 
Style Guidelines:
{style}
---
"""
    else:
        prompt = prompt_template.format(
            title=title, 
            description=description, 
            categories=categories,
            pref_tags=pref_tags,
            style=style
        )

    try:
        result = await llm.generate_text(prompt)
        
        category = "生活-日常"
        tags = []
        
        lines = result.split('\n')
        for line in lines:
            line = line.strip()
            if line.startswith('CATEGORY:'):
                category = line.replace('CATEGORY:', '').strip()
            elif line.startswith('TAGS:'):
                tag_str = line.replace('TAGS:', '').strip()
                # Remove [END] if present
                if '[END]' in tag_str:
                    tag_str = tag_str[:tag_str.index('[END]')].strip()
                tag_str = tag_str.replace('，', ',')
                tag_str = tag_str.strip('[]')
                tags = [t.strip().strip('[]') for t in tag_str.split(',') if t.strip()]

        return category, tags
        
    except Exception as e:
        log.error(f"Content analysis failed: {e}")
        return "生活-日常", ["生活", "日常", "视频"]

def analyze_video_content_sync(title, description):
    return _AsyncLoopRunner.run(analyze_video_content(title, description))


def _sanitize_activity_keyword(raw: str) -> str:
    if not raw:
        return ""
    text = str(raw).replace("\\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    drop_fragments = ["Gemini 说", "显示思路", "关键词", "Keyword"]
    filtered = [l for l in lines if not any(x in l for x in drop_fragments)]
    if not filtered:
        return ""
    token = filtered[0]
    if re.search(r"\s", token):
        return ""
    token = re.split(r"[\s,，:：]", token)[0]
    token = token.strip().strip("\"'`")
    if not re.match(r"^[\u4e00-\u9fffA-Za-z0-9]{2,8}$", token):
        return ""
    return token

async def recommend_activity_keyword(title: str, description: str) -> str:
    """
    Recommend a Bilibili activity search keyword based on video content.
    Returns a keyword string, or empty string if no specific activity seems relevant.
    """
    llm = LLMFactory.get_service('analysis')
    
    prompt = f"""Based on the video title and description, suggest a short, high-level Chinese keyword to search for official Bilibili "Activities" (Missions/Hashtags).
    
    Examples of GOOD (broad) keywords:
    - "科技" (Technology - HIGH PRIORITY for AI/Tech content)
    - "人工智能" (Artificial Intelligence - ONLY if specific)
    - "健身" (for workouts, health)
    - "数码" (for tech gadgets, hardware)
    - "单机游戏" or the Game Name (e.g., "黑神话")
    - "手工" (for DIY)
    
    Examples of BAD (too specific or long) keywords:
    - "AI" (Too ambiguous, often matches unrelated specific events like "Pet AI")
    - "科普" (Too broad, often matches "Meteorological Science")
    - "人工智能科普" (too long, use "科技")
    - "UE5材质教学" (too specific, use "游戏开发" or "单机游戏")
    
    Video Title: {title}
    Video Description: {description}
    
    Instructions:
    1. Return ONLY the keyword.
    2. Prefer high-level category keywords that are likely to have active Bilibili missions.
    3. For AI, Coding, or Tech news, PREFER "科技" over "科普".
    4. Do NOT output "Keyword:" or any other labels.
    """
    
    try:
        keyword = await llm.generate_text(prompt)
        return _sanitize_activity_keyword(keyword)
    except Exception as e:
        log.error(f"Activity keyword recommendation failed: {e}")
        return ""

def recommend_activity_keyword_sync(title, description):
    return _AsyncLoopRunner.run(recommend_activity_keyword(title, description))

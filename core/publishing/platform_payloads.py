import copy
import os
import re

PLATFORM_COVER_STYLES = {
    "bilibili": "信息密度略高，标题感明确，适合教程和系列感封面。",
    "douyin": "短促有冲击，结果导向，突出动作收益和即时感。",
    "kuaishou": "直接接地气，动作收益明确，适合朴素直接的封面文案。",
    "wechat_channels": "克制简洁，摘要感更强，避免太炸裂的短视频语气。",
    "xiaohongshu": "更像笔记标题，生活化、经验感、自述感更强。",
}

PLATFORM_TITLE_LIMITS = {
    "bilibili": 80,
    "douyin": 30,
    "kuaishou": 30,
    "wechat_channels": 16,
    "xiaohongshu": 20,
}

PLATFORM_COVER_TEXT_LIMITS = {
    "bilibili": 18,
    "douyin": 12,
    "kuaishou": 14,
    "wechat_channels": 14,
    "xiaohongshu": 16,
}

PLATFORM_COVER_VARIANT_VERSION = 2
PLATFORM_COVER_SPECS = {
    "douyin": {
        "aspect_ratio": "16:9",
        "display_crop_ratio": "3:4",
        "source_base": "16_9",
        "layout": (
            "横版视频封面本身保持 16:9，但抖音预览常按中间 3:4 区域取景；"
            "所有关键文案、主体动作和重点标签都必须收在中央安全区，"
            "不要把补充说明或小字堆在右上角和边缘。"
        ),
    },
    "kuaishou": {
        "aspect_ratio": "16:9",
        "display_crop_ratio": "",
        "source_base": "16_9",
        "layout": (
            "快手横版封面优先与视频保持同尺寸；"
            "整体信息集中在中部，避免贴边、多层角标和复杂的小字说明，"
            "只保留一个最直接的主钩子。"
        ),
    },
    "wechat_channels": {
        "aspect_ratio": "16:9",
        "display_crop_ratio": "6:7",
        "source_base": "16_9",
        "layout": (
            "视频号横版封面本身按 16:9 出图，但主页缩略区会近似裁到 6:7；"
            "标题、主体和任何核心信息都必须放进中央 6:7 安全区，"
            "左右两边尽量只留背景和辅助元素。"
        ),
    },
    "xiaohongshu": {
        "aspect_ratio": "3:4",
        "display_crop_ratio": "3:4",
        "source_base": "16_9",
        "layout": (
            "小红书优先做 3:4 竖版笔记封面；"
            "如果源图是横版，允许用纯色、渐变或模糊延展补成竖版画布，"
            "不要粗暴裁掉脸、手和主体动作。"
        ),
    },
}

PLATFORM_TAG_LIMITS = {
    "bilibili": 12,
    "douyin": 5,
    "kuaishou": 4,
    "wechat_channels": 5,
    "xiaohongshu": 5,
}

PLATFORM_DESCRIPTION_LIMITS = {
    "bilibili": 5000,
    "douyin": 80,
    "kuaishou": 80,
    "wechat_channels": 80,
    "xiaohongshu": 90,
}

RESULT_KEYWORDS = (
    "燃脂",
    "暴汗",
    "塑形",
    "紧致",
    "减脂",
    "减肥",
    "瘦身",
    "跟练",
    "高效",
    "全身",
    "训练",
)

FITNESS_KEYWORDS = (
    "健身",
    "训练",
    "燃脂",
    "减脂",
    "减肥",
    "瘦身",
    "塑形",
    "跟练",
    "hiit",
    "tabata",
    "workout",
    "cardio",
    "strength",
    "glutes",
    "dumbbell",
    "home workout",
    "居家",
    "哑铃",
    "臀",
    "腿",
)

TECH_KEYWORDS = (
    "ue",
    "ue5",
    "unreal",
    "ai",
    "cg",
    "vfx",
    "metahuman",
    "kling",
    "nanite",
    "lumen",
    "plugin",
    "渲染",
    "短片",
    "影视",
    "后期",
    "生成式",
)

FOOD_KEYWORDS = (
    "空气炸锅",
    "食谱",
    "美食",
    "料理",
    "烘焙",
    "菜谱",
    "好吃",
    "早餐",
    "甜点",
    "烤箱",
    "鸡蛋",
    "茄子",
    "桃子",
    "香蕉",
    "金枪鱼",
    "燕麦",
    "香迷糊",
    "香晕",
    "神仙吃法",
    "封神",
)

GAME_KEYWORDS = (
    "游戏",
    "gta",
    "战地",
    "荒野大镖客",
    "使命召唤",
    "r星",
    "育碧",
    "steam",
    "开放世界",
    "游戏史",
    "the finals",
    "游戏开发",
    "独立游戏",
    "unity",
    "关卡",
    "游戏美术",
)

SALUTATION_PREFIX_RE = re.compile(
    r"^(?:(?:各位好|大家好|家人们|朋友们|欢迎来到今天的训练营|欢迎来到今天的分享)[！!，,、\s]*)+"
)
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
RAW_URL_RE = re.compile(r"https?://\S+")
NOISE_LINE_RE = re.compile(
    r"^(?:[#\-•*]+|\d+\.)"
)
SECTION_LINE_RE = re.compile(
    r"^(?:[\W_]*)(?:训练亮点|训练要点|计划详情|进阶计划|训练场景|训练收益|训练建议|技术栈清单|音乐素材|获取更多计划|持续关注)\s*$"
)
CTA_TERMS = (
    "点个三连",
    "欢迎订阅",
    "评论区",
    "加入完整训练营",
    "点击下方链接",
    "下载",
    "Music ",
)

PLATFORM_VARIANT_TARGETS = (
    "douyin",
    "kuaishou",
    "wechat_channels",
    "xiaohongshu",
)
SUCCESSFUL_COVER_ASSET_STATUSES = {"generated", "reused_existing"}

BILIBILI_TOP_LEVEL_OPTION_FIELDS = (
    "tid",
    "category",
    "copyright",
    "mission_id",
    "topic_id",
    "activity_id",
    "topic_keyword",
    "season",
    "collection",
    "suggested_collection",
    "series",
    "suggested_series",
)


def _clean_text(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _clean_path_text(value: str) -> str:
    """Normalize a file path for storage/comparison.

    Unlike _clean_text, this preserves internal whitespace because
    file paths may legitimately contain double spaces (e.g. YouTube
    titles with full-width pipe + extra spacing).
    """
    text = str(value or "").strip()
    if text.casefold() in {"none", "null"}:
        return ""
    return text


def _truncate(value: str, limit: int) -> str:
    text = _clean_text(value)
    if limit <= 0:
        return text
    return text[:limit].strip()


def _unique_preserve(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _split_title_clauses(title: str) -> list[str]:
    text = _clean_text(title)
    if not text:
        return []
    parts = re.split(r"[【】\[\]\(\)（）：:|｜/／\-—–,，、。！？!?\s]+", text)
    clauses = []
    for part in parts:
        cleaned = _clean_text(part)
        if cleaned and cleaned not in clauses:
            clauses.append(cleaned)
    if not clauses:
        clauses.append(text)
    return clauses


def _pick_clause(title: str, preferred_keywords: tuple[str, ...] = RESULT_KEYWORDS) -> str:
    clauses = _split_title_clauses(title)
    if not clauses:
        return ""
    for keyword in preferred_keywords:
        for clause in clauses:
            if keyword in clause:
                return clause
    return max(clauses, key=len)


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    haystack = _clean_text(text).lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def infer_content_domain(
    title: str,
    description: str = "",
    tags: list[str] = None,
) -> str:
    haystack = " ".join(
        [
            _clean_text(title),
            _clean_text(description),
            " ".join(_clean_text(tag) for tag in (tags or [])),
        ]
    )
    if _contains_any(haystack, FOOD_KEYWORDS):
        return "food"
    if _contains_any(haystack, FITNESS_KEYWORDS):
        return "fitness"
    if _contains_any(haystack, GAME_KEYWORDS):
        return "game"
    if _contains_any(haystack, TECH_KEYWORDS):
        return "tech"
    return "generic"


def _strip_description_noise(description: str) -> str:
    text = MARKDOWN_LINK_RE.sub(r"\1", str(description or ""))
    text = RAW_URL_RE.sub("", text)
    return text.replace("\r", "").strip()


def _clean_description_line(line: str) -> str:
    text = _clean_text(line)
    if not text:
        return ""
    text = SALUTATION_PREFIX_RE.sub("", text).strip()
    return text


def _extract_summary_line(title: str, description: str) -> str:
    cleaned = _strip_description_noise(description)
    for raw_line in cleaned.splitlines():
        line = _clean_description_line(raw_line)
        if not line:
            continue
        if NOISE_LINE_RE.match(line):
            continue
        if SECTION_LINE_RE.match(line):
            continue
        if any(term in line for term in CTA_TERMS):
            continue
        if len(line) < 8:
            continue
        return _truncate(line, 48)
    return _truncate(_clean_text(title), 48)


def _should_refresh_platform_description(
    platform: str,
    existing_description: str,
    base_description: str,
) -> bool:
    text = str(existing_description or "").strip()
    if not text:
        return True
    limit = PLATFORM_DESCRIPTION_LIMITS.get(platform, 0)
    if limit and len(text) > limit:
        return True
    clean_base = _strip_description_noise(base_description)
    if text == clean_base:
        return True
    if any(term in text for term in CTA_TERMS):
        return True
    if RAW_URL_RE.search(text) or MARKDOWN_LINK_RE.search(text):
        return True
    if SALUTATION_PREFIX_RE.match(text):
        return True
    return False


def _is_predominantly_english(text: str) -> bool:
    """Return True if text contains English letters but no CJK characters.

    Unlike is_english_title() in fetcher.py which checks character ratio,
    this checks for CJK presence — better suited for detecting whether a
    title/tags payload has been translated to Chinese or is still English.
    """
    clean = _clean_text(text)
    if not clean:
        return False
    has_english = bool(re.search(r'[a-zA-Z]', clean))
    has_chinese = bool(re.search(r'[\u4e00-\u9fff]', clean))
    return has_english and not has_chinese


def _should_refresh_platform_tags(platform: str, existing_tags, base_tags=None) -> bool:
    normalized_tags = [
        _clean_text(tag) for tag in (existing_tags or []) if _clean_text(tag)
    ]
    if not normalized_tags:
        return True
    limit = PLATFORM_TAG_LIMITS.get(platform, 0)
    if limit and len(normalized_tags) > limit:
        return True
    # Language mismatch: force refresh when existing tags are English
    # but the base tags have been translated to Chinese (or vice versa)
    if base_tags is not None:
        existing_text = " ".join(normalized_tags)
        base_normalized = [_clean_text(tag) for tag in base_tags if _clean_text(tag)]
        base_text = " ".join(base_normalized)
        if _is_predominantly_english(existing_text) != _is_predominantly_english(base_text):
            return True
    return False


def _has_unbalanced_leading_bracket(text: str) -> bool:
    pairs = (
        ("【", "】"),
        ("[", "]"),
        ("（", "）"),
        ("(", ")"),
    )
    for left, right in pairs:
        if text.startswith(left) and right not in text:
            return True
    return False


def _should_refresh_platform_title(platform: str, existing_title: str, base_title: str) -> bool:
    text = _clean_text(existing_title)
    if not text:
        return True

    limit = PLATFORM_TITLE_LIMITS.get(platform, 0)
    if limit and len(text) > limit:
        return True

    clean_base = _clean_text(base_title)
    if platform != "bilibili" and clean_base and text == clean_base:
        candidate = build_platform_title(platform, clean_base, None)
        if candidate and candidate != text:
            return True

    if _has_unbalanced_leading_bracket(text):
        return True

    # Language mismatch: force refresh when existing title is English
    # but the base title has been translated to Chinese (or vice versa)
    if _is_predominantly_english(text) != _is_predominantly_english(clean_base):
        return True

    return False


def build_platform_tags(platform: str, title: str, tags: list[str] = None) -> list[str]:
    del title
    cleaned_tags = _unique_preserve([_clean_text(tag) for tag in (tags or []) if _clean_text(tag)])
    limit = PLATFORM_TAG_LIMITS.get(platform, len(cleaned_tags))
    if platform == "bilibili":
        return cleaned_tags[:limit]
    return cleaned_tags[:limit]


def build_platform_cover_style(platform: str, title: str, tags: list[str] = None) -> str:
    base_style = PLATFORM_COVER_STYLES.get(platform, "")
    cover_spec = PLATFORM_COVER_SPECS.get(platform, {})
    layout_hint = _clean_text(str(cover_spec.get("layout", "")))
    domain = infer_content_domain(title, "", tags)
    parts = [base_style]
    if platform == "bilibili":
        # 数据驱动(2026-08-11 诊断): 高播放封面 = 短钩子短语 + 具体数字/食材 + 大字高对比 + 情绪词
        # 设计原则(2026-08-15): 克制(少即是好) + 对比(焦点) + 均衡(留白) + 诚实(与内容一致)
        parts.append(
            "封面文字用短钩子短语（如「45分钟燃脂」「鸡蛋神仙吃法」「700大卡挑战」），"
            "3-8 字为佳、绝对不要长句；大字高对比色，边缘留白，缩略图尺寸下清晰可读。"
            "克制优先：一个强钩子好过堆叠多句，宁缺毋滥；"
            "每个文字元素都要承载信息（时长/卡路里/结果/情绪），不要装饰性贴纸和废话；"
            "文字与背景必须形成强对比（亮字配暗底或反之），主体与文字是两大焦点、不要挤在同一位置；"
            "保持主体、文字、留白三者均衡，文字周围留呼吸空间；"
            "封面文案必须与视频真实内容一致，不夸大不虚假承诺。"
        )
    if domain == "fitness":
        parts.append("画面文案可强调训练收益、动作状态和跟练氛围，尽量带具体时长或卡路里数字。")
    elif domain == "tech":
        parts.append("画面文案可强调成片效果、技术关键词和质感冲击。")
    elif domain == "food":
        parts.append("画面文案可强调成品食物的诱人状态，用具体食材名 + 情绪词（香迷糊/神仙吃法/停不下来）。")
    elif domain == "game":
        parts.append("画面文案可强调游戏画面冲击或技术细节，用具体游戏名/引擎名。")
    if layout_hint:
        parts.append(layout_hint)
    return _clean_text(" ".join(part for part in parts if part))


def _build_platform_suffix(platform: str, domain: str) -> str:
    if platform == "douyin":
        if domain == "fitness":
            return "适合居家跟练，建议先收藏再开练。"
        if domain == "tech":
            return "重点看技术组合和最终成片效果。"
        return "核心看点都在视频里，建议先收藏。"

    if platform == "kuaishou":
        if domain == "fitness":
            return "动作清楚，打开就能跟。"
        if domain == "tech":
            return "直接看效果，再看关键技术点。"
        return "内容直接，点开就能看明白。"

    if platform == "wechat_channels":
        if domain == "fitness":
            return "适合收藏后按节奏完成。"
        if domain == "tech":
            return "适合收藏后慢慢看制作细节。"
        return "适合收藏后慢慢看。"

    if platform == "xiaohongshu":
        if domain == "fitness":
            return "更适合居家跟练，先收藏再练。"
        if domain == "tech":
            return "重点看技术组合和成片效果。"
        return "适合先收藏再回看。"

    return ""


def build_platform_description(
    platform: str,
    title: str,
    description: str,
    tags: list[str] = None,
) -> str:
    clean_description = _strip_description_noise(description)
    if platform == "bilibili":
        return clean_description

    domain = infer_content_domain(title, clean_description, tags)
    hook = build_platform_title(platform, title, tags)
    summary = _extract_summary_line(title, clean_description)
    suffix = _build_platform_suffix(platform, domain)

    if platform == "xiaohongshu":
        lead = f"今天分享一套{hook}" if domain == "fitness" else f"今天分享的是{hook}"
        if domain == "tech":
            lead = f"今天整理一条{hook}"
        lines = [lead, suffix]
    elif platform == "wechat_channels":
        lines = [hook]
        if summary and summary not in {hook, title} and summary[:6] not in hook:
            lines.append(summary)
        else:
            lines.append(suffix)
    else:
        lines = [hook, suffix]

    limit = PLATFORM_DESCRIPTION_LIMITS.get(platform, 120)
    text = "\n".join(line for line in lines if line).strip()
    return _truncate(text, limit)


def build_platform_title(platform: str, title: str, tags: list[str] = None) -> str:
    clean_title = _clean_text(title)
    if not clean_title:
        return ""

    if platform == "bilibili":
        return clean_title

    if platform == "douyin":
        candidate = _pick_clause(clean_title)
        return _truncate(candidate or clean_title, PLATFORM_TITLE_LIMITS[platform])

    if platform == "kuaishou":
        first = _pick_clause(clean_title)
        return _truncate(first or clean_title, PLATFORM_TITLE_LIMITS[platform])

    if platform == "wechat_channels":
        first = _pick_clause(clean_title)
        return _truncate(first or clean_title, PLATFORM_TITLE_LIMITS[platform])

    if platform == "xiaohongshu":
        first = _pick_clause(clean_title)
        if "跟练" not in first and tags and any(tag in {"健身", "居家健身", "全身训练"} for tag in tags):
            first = f"{first}跟练"
        return _truncate(first or clean_title, PLATFORM_TITLE_LIMITS[platform])

    return _truncate(clean_title, PLATFORM_TITLE_LIMITS.get(platform, 80))


def build_platform_cover_text(platform: str, title: str, tags: list[str] = None) -> str:
    tags = [str(tag).strip() for tag in (tags or []) if str(tag).strip()]
    base = build_platform_title(platform, title, tags)

    if platform == "bilibili":
        # 数据驱动(2026-08-11 诊断): 爆款封面 = 短钩子短语(时长/卡路里/食材+情绪), 不是标题截断
        domain = infer_content_domain(title, "", tags)
        if domain == "fitness":
            # 数据驱动(2026-08-11 诊断): 爆款封面 = 短钩子短语(时长/卡路里/食材+情绪), 不是标题截断
            # 2026-08-14 补全: 支持英文标题(翻译前) + 普拉提/拉伸类目, 避免 fallback 到标题截断
            title_l = title.lower()
            # 时长提取(中英都认): "30分钟" / "30 Min" / "45MIN"
            duration_match = re.search(r"(\d{1,3})\s*(?:分钟|min(?:ute)?s?|m)\b", title_l)
            duration = f"{duration_match.group(1)}分钟" if duration_match else ""
            if "pilates" in title_l or "普拉提" in title:
                # 普拉提: 有具体分钟就用「N分钟普拉提」, 否则「普拉提跟练」
                if duration:
                    return _truncate(f"{duration}普拉提跟练", PLATFORM_COVER_TEXT_LIMITS[platform])
                return _truncate("普拉提跟练", PLATFORM_COVER_TEXT_LIMITS[platform])
            if "stretch" in title_l or "cool down" in title_l or "cooldown" in title_l or "拉伸" in title or "放松" in title:
                # 拉伸/放松: 有具体分钟就用「N分钟拉伸」, 否则「练后拉伸」
                if duration:
                    return _truncate(f"{duration}拉伸", PLATFORM_COVER_TEXT_LIMITS[platform])
                return _truncate("练后拉伸", PLATFORM_COVER_TEXT_LIMITS[platform])
            for kw in ["60分钟", "45分钟", "40分钟", "30分钟", "25分钟", "20分钟", "15分钟", "12分钟"]:
                if kw in title and "燃脂" in title:
                    return _truncate(f"{kw}燃脂", PLATFORM_COVER_TEXT_LIMITS[platform])
            if "全程站立" in title:
                return _truncate("全程站立燃脂", PLATFORM_COVER_TEXT_LIMITS[platform])
            if "无器械" in title or "无跳跃" in title or "no equipment" in title_l or "no jumping" in title_l:
                return _truncate("无器械燃脂跟练", PLATFORM_COVER_TEXT_LIMITS[platform])
            if "腹肌" in title or "核心" in title or "core" in title_l or "abs" in title_l:
                return _truncate("核心燃脂跟练", PLATFORM_COVER_TEXT_LIMITS[platform])
            if "hiit" in title_l or "tabata" in title_l:
                return _truncate("HIIT燃脂跟练", PLATFORM_COVER_TEXT_LIMITS[platform])
        elif domain == "food":
            for kw in ["空气炸锅", "鸡蛋", "茄子", "桃子", "香蕉", "金枪鱼", "燕麦", "土豆"]:
                if kw in title:
                    if "神仙" in title or "封神" in title:
                        return _truncate(f"{kw}神仙吃法", PLATFORM_COVER_TEXT_LIMITS[platform])
                    if "好吃" in title or "香" in title:
                        return _truncate(f"{kw}香迷糊了", PLATFORM_COVER_TEXT_LIMITS[platform])
                    return _truncate(f"{kw}吃法", PLATFORM_COVER_TEXT_LIMITS[platform])
            if "香迷糊" in title or "香晕" in title or "好吃" in title:
                return _truncate("香迷糊了", PLATFORM_COVER_TEXT_LIMITS[platform])
        elif domain == "tech":
            if "AI日报" in title:
                return _truncate("AI日报", PLATFORM_COVER_TEXT_LIMITS[platform])
            for kw in ["UE5", "UE6", "虚幻", "Blender", "AI", "3D"]:
                if kw in title:
                    return _truncate(f"{kw}教程", PLATFORM_COVER_TEXT_LIMITS[platform])
        elif domain == "game":
            for kw in ["The Finals", "GTA", "战地", "荒野大镖客", "使命召唤", "R星", "育碧", "Steam", "红荒", "赤色沙漠"]:
                if kw in title:
                    return _truncate(f"{kw}解析", PLATFORM_COVER_TEXT_LIMITS[platform])
        return _truncate(base, PLATFORM_COVER_TEXT_LIMITS[platform])

    if platform == "douyin":
        if "燃脂" in title and "全身" in title:
            return _truncate("全身燃脂", PLATFORM_COVER_TEXT_LIMITS[platform])
        return _truncate(base, PLATFORM_COVER_TEXT_LIMITS[platform])

    if platform == "kuaishou":
        if "无需器械" in title:
            return _truncate("无需器械燃脂", PLATFORM_COVER_TEXT_LIMITS[platform])
        return _truncate(base, PLATFORM_COVER_TEXT_LIMITS[platform])

    if platform == "wechat_channels":
        if "40分钟" in title and "燃脂" in title:
            return _truncate("40分钟燃脂", PLATFORM_COVER_TEXT_LIMITS[platform])
        return _truncate(base, PLATFORM_COVER_TEXT_LIMITS[platform])

    if platform == "xiaohongshu":
        if "居家" in title and "燃脂" in title:
            return _truncate("居家燃脂跟练", PLATFORM_COVER_TEXT_LIMITS[platform])
        return _truncate(base, PLATFORM_COVER_TEXT_LIMITS[platform])

    return _truncate(base, PLATFORM_COVER_TEXT_LIMITS.get(platform, 18))


def build_platform_cover_output_path(base_cover_path: str, platform: str) -> str:
    if not base_cover_path:
        return ""
    directory = os.path.dirname(base_cover_path)
    filename = os.path.basename(base_cover_path)
    stem, ext = os.path.splitext(filename)
    if stem.endswith("_cover_16x9"):
        stem = stem[: -len("_cover_16x9")]
    elif stem.endswith("_cover_4x3"):
        stem = stem[: -len("_cover_4x3")]
    return os.path.join(directory, f"{stem}_cover_{platform}{ext or '.jpg'}")


def hydrate_cover_metadata_from_disk(metadata: dict, targets: list[str] = None) -> dict:
    hydrated = copy.deepcopy(metadata or {})
    video_path = _clean_path_text(hydrated.get("video_path", ""))
    if not video_path:
        return hydrated

    video_dir = os.path.dirname(video_path)
    video_stem = os.path.splitext(os.path.basename(video_path))[0]

    def _pick_existing(*candidates: str) -> str:
        for candidate in candidates:
            candidate = _clean_path_text(candidate)
            if candidate and os.path.isfile(candidate):
                return candidate
        return ""

    base_cover_16_9 = _pick_existing(
        hydrated.get("cover_path_16_9", ""),
        os.path.join(video_dir, f"{video_stem}_cover_16x9.jpg"),
    )
    base_cover_4_3 = _pick_existing(
        hydrated.get("cover_path_4_3", ""),
        os.path.join(video_dir, f"{video_stem}_cover_4x3.jpg"),
    )

    if base_cover_16_9:
        hydrated["cover_path_16_9"] = base_cover_16_9
        if not _pick_existing(hydrated.get("cover_path", "")):
            hydrated["cover_path"] = base_cover_16_9
    if base_cover_4_3:
        hydrated["cover_path_4_3"] = base_cover_4_3

    cover_assets = copy.deepcopy(hydrated.get("cover_assets") or {})
    base_assets = copy.deepcopy(cover_assets.get("base") or {})
    platform_assets = copy.deepcopy(cover_assets.get("platforms") or {})

    if base_cover_16_9:
        base_assets["16_9"] = base_cover_16_9
    if base_cover_4_3:
        base_assets["4_3"] = base_cover_4_3

    original_cover = _pick_existing(hydrated.get("orig_cover_path", ""))
    if original_cover:
        base_assets.setdefault("original", original_cover)

    title = str(hydrated.get("title", "")).strip()
    tags = hydrated.get("tags") or []
    target_list = list(targets or PLATFORM_VARIANT_TARGETS)
    for platform in target_list:
        if platform == "bilibili":
            continue
        variant_path = _pick_existing(
            (platform_assets.get(platform) or {}).get("path", ""),
            build_platform_cover_output_path(base_cover_16_9, platform),
            os.path.join(video_dir, f"{video_stem}_cover_{platform}.jpg"),
        )
        if not variant_path:
            continue
        cover_spec = PLATFORM_COVER_SPECS.get(platform, {})
        existing_asset = copy.deepcopy(platform_assets.get(platform) or {})
        existing_asset["path"] = variant_path
        existing_asset["status"] = (
            _clean_text(existing_asset.get("status", "")) or "reused_existing"
        )
        existing_asset.setdefault(
            "source_base",
            str(cover_spec.get("source_base", "16_9")).strip() or "16_9",
        )
        existing_asset.setdefault(
            "aspect_ratio",
            str(cover_spec.get("aspect_ratio", "16:9")).strip() or "16:9",
        )
        existing_asset.setdefault(
            "display_crop_ratio",
            str(cover_spec.get("display_crop_ratio", "")).strip(),
        )
        existing_asset.setdefault("variant_version", PLATFORM_COVER_VARIANT_VERSION)
        existing_asset.setdefault("cover_text", build_platform_cover_text(platform, title, tags))
        existing_asset.setdefault("style", build_platform_cover_style(platform, title, tags))
        platform_assets[platform] = existing_asset

    if base_assets or platform_assets:
        hydrated["cover_assets"] = {
            "base": base_assets,
            "platforms": platform_assets,
        }

    if targets:
        hydrated["platform_payloads"] = build_platform_payloads(hydrated, target_list)

    return hydrated


def build_platform_cover_variant_specs(metadata: dict, targets: list[str]) -> dict[str, dict]:
    base_cover_path = str(metadata.get("cover_path_16_9", "")).strip()
    if not base_cover_path:
        return {}

    title = str(metadata.get("title", "")).strip()
    tags = metadata.get("tags") or []
    specs = {}
    for platform in targets:
        if platform == "bilibili":
            continue
        cover_spec = PLATFORM_COVER_SPECS.get(platform, {})
        output_path = build_platform_cover_output_path(base_cover_path, platform)
        if not output_path:
            continue
        specs[platform] = {
            "platform": platform,
            "source_path": base_cover_path,
            "output_path": output_path,
            "source_base": str(cover_spec.get("source_base", "16_9")).strip() or "16_9",
            "aspect_ratio": str(cover_spec.get("aspect_ratio", "16:9")).strip() or "16:9",
            "display_crop_ratio": str(cover_spec.get("display_crop_ratio", "")).strip(),
            "cover_layout": str(cover_spec.get("layout", "")).strip(),
            "variant_version": PLATFORM_COVER_VARIANT_VERSION,
            "cover_text": build_platform_cover_text(platform, title, tags),
            "cover_style": build_platform_cover_style(platform, title, tags),
            "title": build_platform_title(platform, title, tags),
        }
    return specs


def should_refresh_platform_cover_asset(existing_asset: dict, spec: dict) -> bool:
    if not isinstance(existing_asset, dict) or not existing_asset:
        return True

    expected_pairs = (
        ("variant_version", "variant_version"),
        ("source_base", "source_base"),
        ("aspect_ratio", "aspect_ratio"),
        ("display_crop_ratio", "display_crop_ratio"),
        ("cover_text", "cover_text"),
        ("style", "cover_style"),
    )
    for asset_key, spec_key in expected_pairs:
        if _clean_text(str(existing_asset.get(asset_key, ""))) != _clean_text(
            str(spec.get(spec_key, ""))
        ):
            return True

    return False


def _get_platform_asset_cover_path(metadata: dict, platform: str) -> str:
    cover_assets = metadata.get("cover_assets") or {}
    platform_assets = cover_assets.get("platforms") or {}
    platform_asset = platform_assets.get(platform) or {}
    status = str(platform_asset.get("status", "")).strip().lower()
    if status and status not in SUCCESSFUL_COVER_ASSET_STATUSES:
        return ""
    value = _clean_path_text(platform_asset.get("path", ""))
    if value:
        return value
    return ""


def _get_platform_option_overrides(metadata: dict, platform: str) -> dict:
    overrides = {}

    platform_options = metadata.get("platform_options") or {}
    if isinstance(platform_options, dict):
        platform_option_values = platform_options.get(platform) or {}
        if isinstance(platform_option_values, dict):
            overrides.update(copy.deepcopy(platform_option_values))

    if platform == "bilibili":
        for key in BILIBILI_TOP_LEVEL_OPTION_FIELDS:
            value = metadata.get(key)
            if value not in (None, "", [], {}):
                overrides.setdefault(key, value)
    else:
        # Map suggested_collection to collection_name for automation platforms
        collection = metadata.get("suggested_collection") or ""
        if collection and "collection_name" not in overrides:
            overrides.setdefault("collection_name", collection)

    return overrides


def _should_refresh_payload_cover_path(
    current_cover_path: str,
    *,
    asset_cover_path: str,
    base_cover_16_9: str,
    base_cover_4_3: str,
) -> bool:
    current = _clean_path_text(current_cover_path)
    asset_cover_path = _clean_path_text(asset_cover_path)
    base_cover_16_9 = _clean_path_text(base_cover_16_9)
    base_cover_4_3 = _clean_path_text(base_cover_4_3)
    # If current path doesn't actually exist on disk, always refresh
    if current and not os.path.isfile(current):
        return True
    if not asset_cover_path:
        return not current
    if not current:
        return True
    if current == asset_cover_path:
        return False
    if current in {base_cover_16_9, base_cover_4_3}:
        return True
    return False


def build_platform_payloads(metadata: dict, targets: list[str]) -> dict[str, dict]:
    payloads = copy.deepcopy(metadata.get("platform_payloads") or {})
    title = str(metadata.get("title", "")).strip()
    description = str(metadata.get("description", "")).strip()
    tags = metadata.get("tags") or []
    base_cover_16_9 = _clean_path_text(metadata.get("cover_path_16_9", ""))
    base_cover_4_3 = _clean_path_text(metadata.get("cover_path_4_3", ""))

    for platform in targets:
        payload = _get_platform_option_overrides(metadata, platform)
        payload.update(dict(payloads.get(platform) or {}))
        platform_title = build_platform_title(platform, title, tags)
        if _should_refresh_platform_title(platform, payload.get("title", ""), title):
            payload["title"] = platform_title
        platform_description = build_platform_description(platform, title, description, tags)
        if platform_description and _should_refresh_platform_description(
            platform,
            payload.get("description", ""),
            description,
        ):
            payload["description"] = platform_description
        platform_tags = build_platform_tags(platform, title, tags)
        if platform_tags and _should_refresh_platform_tags(platform, payload.get("tags"), base_tags=tags):
            payload["tags"] = platform_tags
        payload.setdefault("cover_text", build_platform_cover_text(platform, title, tags))
        payload.setdefault("cover_style", build_platform_cover_style(platform, title, tags))

        asset_cover_path = _get_platform_asset_cover_path(metadata, platform)
        current_cover_path = _clean_path_text(payload.get("cover_path", ""))
        if _should_refresh_payload_cover_path(
            current_cover_path,
            asset_cover_path=asset_cover_path,
            base_cover_16_9=base_cover_16_9,
            base_cover_4_3=base_cover_4_3,
        ):
            payload["cover_path"] = asset_cover_path or base_cover_16_9
        else:
            payload.setdefault("cover_path", asset_cover_path or base_cover_16_9)

        if platform == "bilibili":
            if base_cover_4_3:
                existing_4_3 = _clean_path_text(payload.get("cover_path_4_3", ""))
                if not existing_4_3 or not os.path.isfile(existing_4_3):
                    payload["cover_path_4_3"] = base_cover_4_3
                else:
                    payload.setdefault("cover_path_4_3", base_cover_4_3)
            if base_cover_16_9:
                existing_16_9 = _clean_path_text(payload.get("cover_path", ""))
                if not existing_16_9 or not os.path.isfile(existing_16_9):
                    payload["cover_path"] = base_cover_16_9
                else:
                    payload.setdefault("cover_path", base_cover_16_9)

        payloads[platform] = payload

    return payloads

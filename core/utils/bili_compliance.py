"""Bilibili description compliance filter.

B站《关于违规推广行为的治理公告》禁止在简介中包含：
1. 其他平台的外部链接（YouTube/Instagram/TikTok/Twitter/X等）
2. App 订阅/付费推广链接（trybe.fit, patreon 等）
3. 引导至其他平台的 Call-to-Action

此过滤器在发布前自动剥离上述内容，保留技术/内容描述和标签。
"""

import re


# External platform domains to strip from descriptions
EXTERNAL_URL_RE = re.compile(
    r"https?://"
    r"(?:www\.)?"
    r"(?:"
    r"youtube\.com|youtu\.be|"
    r"instagram\.com|"
    r"tiktok\.com|"
    r"twitter\.com|x\.com|"
    r"facebook\.com|fb\.com|"
    r"trybe\.fit|my\.trybe\.fit|"
    r"patreon\.com|"
    r"discord(?:app)?\.(?:com|gg|io)|"
    r"gumroad\.com|"
    r"ko-fi\.com|"
    r"pinterest\.[a-z]+|"
    r"snapchat\.com|"
    r"reddit\.com|"
    r"telegram\.(?:org|me)|"
    r"twitch\.tv|"
    r"medium\.com|"
    r"substack\.com|"
    r"threads\.net|"
    r"behance\.net|"
    r"dribbble\.com|"
    r"artstation\.com|"
    r"deviantart\.com|"
    r"linkedin\.com"
    r")"
    r"\S*",
    re.IGNORECASE,
)

# Lines that are pure separators/decorations
SEPARATOR_LINE_RE = re.compile(r"^[\s\-\u2500-\u257f]{5,}$")

# Lines that are headers for external-platform sections
EXTERNAL_SECTION_HEADER_RE = re.compile(
    r"(?:"
    r"关注我|关注获取|关注作者|关注我们|关注更多|关注了解更多|持续关注|"
    r"Instagram|TikTok|Twitter|Facebook|YouTube|"
    r"宝藏歌单|播放列表.*推荐|热门必看|人气播放|"
    r"互动福利|福利优惠|限时福利|限时优惠|"
    r"进阶.*计划|蜕变.*计划|专属.*(?:App|计划|方案)|"
    r"加入.*App|专属.*App|"
    r"获取.*计划|完整.*计划|系统.*计划|"
    r"相关链接|相关资源|相关学习资源|相关.*信息|"
    r"^📱.*$|"
    r"^Discord[:\s]*$|^X\s*\(Twitter\)[:\s]*$"
    r")",
    re.IGNORECASE,
)

# Lines that are pure CTAs to comment/follow
CTA_COMMENT_RE = re.compile(
    r"在评论区.*留言|评论区.*发送|评论区.*留下|"
    r"点个三连|收藏点赞|喜欢的点个.*支持一下",
    re.IGNORECASE,
)


def filter_bilibili_description(description: str) -> str:
    """Strip B站-prohibited content from video description.

    Keeps: training instructions, technical content, hashtags, general descriptions.
    Strips: external platform URLs, app subscription links, cross-platform CTAs,
            decoration separators, external-platform section headers.
    """
    if not description:
        return description

    lines = description.split("\n")
    filtered = []

    for line in lines:
        stripped = line.strip()

        # Remove empty lines
        if not stripped:
            continue

        # Remove pure separator lines
        if SEPARATOR_LINE_RE.match(stripped):
            continue

        # Remove comment CTAs
        if CTA_COMMENT_RE.search(stripped):
            continue

        # Strip external URLs from the line FIRST (before other checks)
        # This prevents URL content (like "utm_source=YouTube") from
        # triggering section header or other pattern matches.
        cleaned_line = EXTERNAL_URL_RE.sub("", stripped).strip()

        # If after removing URLs the line is empty, skip
        if not cleaned_line:
            continue

        # Now check section headers on the URL-free line
        if EXTERNAL_SECTION_HEADER_RE.search(cleaned_line):
            continue

        # Keep meaningful content lines
        filtered.append(cleaned_line)

    result = "\n".join(filtered).strip()

    # Final cleanup: remove any leftover single-char/emoji-only lines
    result = re.sub(r"^👉?➡?️?\s*$", "", result, flags=re.MULTILINE)
    result = re.sub(r"^📱?\s*$", "", result, flags=re.MULTILINE)
    result = re.sub(r"^💪?\s*$", "", result, flags=re.MULTILINE)

    # Clean up any leftover "( )" or "[]" from stripped links
    result = re.sub(r"^\s*[([].*[)\]]\s*$", "", result, flags=re.MULTILINE)

    # Clean up lines that are just "[" or "👉 [" remnants
    result = re.sub(r"^[👉➡️▶️]*\s*\[?\s*\(?\s*\)?\s*\]?\s*$", "", result, flags=re.MULTILINE)

    # Collapse multiple consecutive newlines
    result = re.sub(r"\n{3,}", "\n\n", result)

    return result.strip()

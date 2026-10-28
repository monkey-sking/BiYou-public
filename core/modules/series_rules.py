#!/usr/bin/env python3
"""
系列（旧版视频列表）归类规则

系列 = 按**内容主题**归类，与「合集 = 按搬运源 UP 主」（organize_collections.CHANNEL_COLLECTIONS）
互为补充。一条稿件最多属于 1 个合集 + 1 个系列。

判定优先级：
  1. 标题关键词规则（SERIES_RULES）—— 最准，同一条 UE 教程不管来自哪个频道都进「UE 引擎 / 游戏开发」
  2. 频道映射（CHANNEL_SERIES）—— 只收录历史主题纯度 >= 80% 的频道
  3. 兜底 DEFAULT_SERIES

2026-09-26 建立，与线上 7 个系列一一对应（series_id 见 core/services/bili_series_api.SERIES_IDS）。
"""
import re

# 内容主题关键词规则（顺序即优先级，命中即停）
SERIES_RULES = [
    ("美食", r"空气炸锅|烤箱|食谱|美食|好吃|做法|料理|披萨|意面|烘焙|减脂餐|早餐|午餐|晚餐|土豆|鸡肉"),
    ("健身跟练", r"普拉提|皮拉提斯|瑜伽|垫上|HIIT|燃脂|腹肌|哑铃|健身|力量训练|有氧|暴汗|跟练|无跳跃|无跑跳|无器械|站立|塑形|核心训练"),
    ("3D 建模 / Blender", r"Blender|几何节点"),
    ("游戏美术与行业", r"游戏美术|AAA|3A|美术|刺客信条|独立游戏|游戏开发|游戏设计|育碧|顽皮狗|R星|引擎对比"),
    ("UE 引擎 / 游戏开发", r"UE5|UE4|UE6|UE 5|虚幻|Nanite|Lumen|MetaHuman|蓝图|PCG|GASP|UEFN|Verse|材质|动画系统|联机|运动匹配|动作匹配|关卡设计|GameDev"),
    ("AI 前沿", r"3D ?AI|AI ?3D|文生3D|图生3D|高斯泼溅|Meshy|Tripo|Hi3D|混元|3D大模型|\bAI\b|GPT|Claude|Gemini|DeepSeek|Kimi|ComfyUI|大模型|Opus|Nano ?Banana|Sora|Midjourney|Grok|AI日报|智能体|Agent"),
]

DEFAULT_SERIES = "其他"

# YouTube channel_id -> 系列（仅收录历史主题纯度 >= 80% 的频道）
CHANNEL_SERIES = {
    "UCBoW0Xb_fDtRgn0la2jm27A": "其他",
    "UCCVs2stsyZ5R4Rq4nFKMCIA": "健身跟练",
    "UCZaFZzc-o7ALqD-bVL4CmJg": "UE 引擎 / 游戏开发",
    "UCHo1CHgTBZPdl-YGKHuY9BQ": "UE 引擎 / 游戏开发",
    "UCdpWKLNfbROyoGPV46-zaUQ": "3D 建模 / Blender",
    "UC9C3t-3ocL0LiwGRD0gBJ8A": "AI 前沿",
    "UCv_n9oioNF6OpzR2dt6E4xg": "UE 引擎 / 游戏开发",
    "UCTFafhmw4HC9f2uc3FG8cfg": "游戏美术与行业",
    "UCzzbfqOuIK6vGtU3lwIRlqg": "UE 引擎 / 游戏开发",
    "UC2ggjtuuWvxrHHHiaDH1dlQ": "AI 前沿",
    "UCIKvQBHjeKm_nZ5eXG86ZpQ": "UE 引擎 / 游戏开发",
    "UCYOurFu8pPg983e91qCn2pg": "UE 引擎 / 游戏开发",
    "UCPqAMQsTxwdQfC4_GsJrwkQ": "UE 引擎 / 游戏开发",
    "UCTGOWbjuS4CN-85DBKT4Fhg": "游戏美术与行业",
    "UCr8NttLeGyLd6m4qVS2Zb8g": "UE 引擎 / 游戏开发",
    "UCJNCeQGLZCgTHcAmtxN_mXw": "游戏美术与行业",
    "UC8Icy-5bEM-ShuSihdO_ocg": "UE 引擎 / 游戏开发",
    "UC9qKZXcPClbOEH6ndpaEOkw": "3D 建模 / Blender",
    "UCmPBLkozH1irLObN0Q3_aJw": "游戏美术与行业",
    "UCcaWyZrrxMaTKtVDkexK9Ow": "健身跟练",
    "UC0gniX5_AAW74z_gDltp5rA": "UE 引擎 / 游戏开发",
    "UCIhbj5Jz_oN22LFTeb2ifZQ": "UE 引擎 / 游戏开发",
    "UCJItL3dzLhLG1FGV1wZRS2w": "健身跟练",
    "UCXp4W8jzNe280dGy_IvKX4Q": "UE 引擎 / 游戏开发",
    "UCRGb8yCnI5-upL3hT4oiOZw": "AI 前沿",
    "UCkfvcZJ6dGNUSCWOCVQnkXw": "UE 引擎 / 游戏开发",
    "UCjSthHe1Wxash3mnDCS2XYA": "游戏美术与行业",
    "UCuAXFkgsw1L7xaCfnd5JJOw": "其他",
    "UC8QghjMT7h7tAAjX9cuLx5A": "美食",
    "UCZEijE12OD9b2tIQJNqmcsw": "健身跟练",
    "UCRW08KcTVjXEmBzBsVl7XjA": "AI 前沿",
    "UCoHLpKMSi5LlOMFlxuoR1rA": "UE 引擎 / 游戏开发",
    "UCnXO5pQCNA7NwCBpLpCvmGA": "UE 引擎 / 游戏开发",
    "UCb3mBiZmLNj7ATWZ2m_Qu0w": "UE 引擎 / 游戏开发",
    "UCl-5pH-iaoOzX3eJ9__fi5A": "AI 前沿",
    "UCbfYPyITQ-7l4upoX8nvctg": "AI 前沿",
    "UCjKg7gsHCEyXtJbZYx2Xu7A": "UE 引擎 / 游戏开发",
    "UCue7TFlrt9FxXarpsl872Dg": "UE 引擎 / 游戏开发",
    "UCE4sKY6_NI0tJOVg_R7ulUg": "UE 引擎 / 游戏开发",
    "UCuhRFHmNjbrI8NgDrLP6OFA": "其他",
    "UCGKjGGjdl-GzEcFPf1EQwqw": "UE 引擎 / 游戏开发",
    "UCXloD6nri7psCbJYOBBnpRg": "UE 引擎 / 游戏开发",
    "UCG1bQ55II0VREN6Y3-XiOjg": "AI 前沿",
    "UCC8W9b_K4T3Cf3kso7Vi4Ag": "UE 引擎 / 游戏开发",
    "UCVjHpbaYfuZG4DZC3lw10JA": "UE 引擎 / 游戏开发",
}


def suggest_series(title: str, channel_id: str = "") -> str:
    """按 标题关键词 -> 频道映射 -> 兜底 的顺序推荐系列名。"""
    text = (title or "").strip()
    if text:
        for name, pattern in SERIES_RULES:
            if re.search(pattern, text, re.IGNORECASE):
                return name
    cid = (channel_id or "").strip()
    if cid and cid in CHANNEL_SERIES:
        return CHANNEL_SERIES[cid]
    return DEFAULT_SERIES

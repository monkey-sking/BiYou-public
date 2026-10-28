#!/usr/bin/env python3
"""
批量整理视频到合集
根据 AI 分类将现有视频自动分配到合集中
"""
import argparse
import json
import time
from typing import Optional
from core.utils.logger import log
from core.utils.config import load_config
from core.services.bili_collection_api import BiliCollectionAPI, get_my_videos

CONFIG = load_config()

# 按 YouTube 频道 -> B站合集映射（优先级最高，频道匹配优先级高于关键词）
# 格式: channel_id|channel_name -> 合集名称
CHANNEL_COLLECTIONS = {
    "UCBoW0Xb_fDtRgn0la2jm27A": "AEffecto",
    "UC1nYVdCZZr7XgeKlXEXAIww": "Can It Code?",
    "UCCVs2stsyZ5R4Rq4nFKMCIA": "Christian Cruz Fitness",
    "UCHo1CHgTBZPdl-YGKHuY9BQ": "Dark Dojo Studios",
    "UCdpWKLNfbROyoGPV46-zaUQ": "Default Cube",
    "UCUPUxnvz4QLozFe82C6vhgA": "Fábio Paiva",
    "UC9C3t-3ocL0LiwGRD0gBJ8A": "Gary Chen",
    "UCv_n9oioNF6OpzR2dt6E4xg": "Gorka Games",
    "UC2ggjtuuWvxrHHHiaDH1dlQ": "Hung-yi Lee",
    "UCSEoY03WFn7D0m1uMi6DxZQ": "Invector Studio",
    "UCIKvQBHjeKm_nZ5eXG86ZpQ": "Jakub W",
    "UCr8NttLeGyLd6m4qVS2Zb8g": "LocoDev",
    "UCnUeqvs-BAhOWKP1Qr-GGkQ": "Mathrock Interactive",
    "UCmPBLkozH1irLObN0Q3_aJw": "Next Level Game Art",
    "UCcaWyZrrxMaTKtVDkexK9Ow": "Nobadaddiction",
    "UCJItL3dzLhLG1FGV1wZRS2w": "Oliver Sjostrom",
    "UCXp4W8jzNe280dGy_IvKX4Q": "Pitchfork Academy",
    "UCRGb8yCnI5-upL3hT4oiOZw": "PixelArtistry",
    "UCjSthHe1Wxash3mnDCS2XYA": "Quaternius",
    "UCTfXFm3IY3wU6k5a3rctabA": "Radical Cap",
    "UC8QghjMT7h7tAAjX9cuLx5A": "Rosario Cooking",
    "UCp1e34nrTQqVXkNU5ekH9CQ": "Smart Poly",
    "UCZEijE12OD9b2tIQJNqmcsw": "Sofia Marre 普拉提",
    "UCzCo65SfjLfAkhJHx162Gsw": "Solarint (BenB)",
    "UCRW08KcTVjXEmBzBsVl7XjA": "Stefan 3D AI",
    "UCA1AYYw-Tc0sTFY7R8pxNCg": "Summse",
    "UCoHLpKMSi5LlOMFlxuoR1rA": "Tank Control Games",
    "UCnXO5pQCNA7NwCBpLpCvmGA": "ThatsUnreal",
    "UCl-5pH-iaoOzX3eJ9__fi5A": "Top 3D AI Tutorials",
    "UCbfYPyITQ-7l4upoX8nvctg": "Two Minute Papers",
    "UCQv4mwerZLQVe3wPbsMc-qw": "Unreal University",
    "UCjKg7gsHCEyXtJbZYx2Xu7A": "Unreal X Tutorials",
    "UCE4sKY6_NI0tJOVg_R7ulUg": "Viktor Khomuttsov",
    "UCiflAFh2USUg0Kblo5RHADQ": "XR AI Spotlight",
    "UCXloD6nri7psCbJYOBBnpRg": "Zero2GameDev",
    "UC-tNLOSw67NV08mVY-t3SOw": "duongunreal",
    "UCKhaZGk_YMxHFqUIUo4CxVg": "pinkpocketTV",
    "UCC8W9b_K4T3Cf3kso7Vi4Ag": "virtucci",
    "UCVjHpbaYfuZG4DZC3lw10JA": "용개발 (Yong)",
    # 2026-09-27 补：tracker 里有稿件但漏登记的频道（否则新稿全落「精选合辑（多源）」）
    "UC9qKZXcPClbOEH6ndpaEOkw": "Milad Savar",
    "UCb3mBiZmLNj7ATWZ2m_Qu0w": "Thomas Halpin",
}

# 预定义的合集分类规则
# 话题型规则已废弃（合集改为按搬运源 UP 主划分）。
# 频道未命中 CHANNEL_COLLECTIONS 时，统一进收容合集，不再按关键词新建话题合集。
COLLECTION_RULES = []

DEFAULT_COLLECTION = "精选合辑（多源）"


def classify_video(title: str, keywords_groups: list[dict]) -> str:
    """
    根据标题关键词分类视频
    Args:
        title: 视频标题
        keywords_groups: 分类规则列表
    Returns: 合集名称
    """
    title_lower = title.lower()
    
    for group in keywords_groups:
        for kw in group['keywords']:
            if kw.lower() in title_lower:
                return group['name']
    
    return DEFAULT_COLLECTION


def classify_video_with_ai(title: str, description: str = "") -> str:
    """
    使用 AI 进行更智能的分类
    Args:
        title: 视频标题
        description: 视频描述
    Returns: 合集名称
    """
    try:
        import google.generativeai as genai
        from core.utils.config import get_gemini_key
        
        genai.configure(api_key=get_gemini_key())
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        collection_names = [r['name'] for r in COLLECTION_RULES] + [DEFAULT_COLLECTION]
        
        prompt = f"""你是一个视频分类助手。根据视频标题，将其分配到最合适的合集中。

可选合集：
{chr(10).join(f"- {name}" for name in collection_names)}

视频标题：{title}

只回复合集名称，不要任何其他内容。"""

        response = model.generate_content(prompt)
        suggested = response.text.strip()
        
        # 验证返回的合集名称
        if suggested in collection_names:
            return suggested
        
        # 模糊匹配
        for name in collection_names:
            if name in suggested or suggested in name:
                return name
        
        # 如果AI生成了全新合集名字且合法
        if suggested and len(suggested) < 20:
             return suggested
             
        return DEFAULT_COLLECTION
        
    except Exception as e:
        log.warning(f"AI 分类失败，使用规则分类: {e}")
        return classify_video(title, COLLECTION_RULES)


def organize_all_videos(dry_run: bool = True, use_ai: bool = False) -> dict:
    """
    整理所有视频到合集
    Args:
        dry_run: 如果为 True，只显示计划不执行
        use_ai: 是否使用 AI 分类
    Returns: 整理结果统计
    """
    print("\n" + "="*60)
    print("📁 视频合集整理工具")
    print("="*60)
    
    # 初始化 API
    api = BiliCollectionAPI()
    
    # 获取所有视频
    print("\n🔍 正在获取视频列表...")
    all_videos = []
    page = 1
    page_size = 50
    
    while True:
        videos, total = get_my_videos(api.session, page, page_size)
        all_videos.extend(videos)
        print(f"   已获取 {len(all_videos)}/{total} 个视频")
        
        if len(all_videos) >= total or not videos:
            break
        page += 1
        time.sleep(0.5)  # 避免请求过快
    
    print(f"\n📊 共找到 {len(all_videos)} 个视频")
    
    # 获取现有合集
    print("\n📁 正在获取现有合集...")
    existing_collections = api.get_my_collections()
    existing_names = {c['title']: c for c in existing_collections}
    print(f"   现有合集: {list(existing_names.keys()) if existing_names else '(无)'}")
    
    # 分类视频
    print("\n🏷️  正在分类视频...")
    classification = {}  # {合集名: [视频列表]}
    
    for video in all_videos:
        title = video['title']
        
        if use_ai:
            collection_name = classify_video_with_ai(title)
        else:
            collection_name = classify_video(title, COLLECTION_RULES)
        
        if collection_name not in classification:
            classification[collection_name] = []
        classification[collection_name].append(video)
    
    # 显示分类结果
    print("\n" + "-"*60)
    print("📋 分类计划:")
    print("-"*60)
    
    for name, videos in sorted(classification.items(), key=lambda x: -len(x[1])):
        exists_tag = "✓" if name in existing_names else "NEW"
        print(f"\n📁 {name} [{exists_tag}] ({len(videos)} 个视频)")
        for v in videos[:5]:  # 只显示前 5 个
            print(f"   - {v['title'][:50]}...")
        if len(videos) > 5:
            print(f"   ... 还有 {len(videos) - 5} 个视频")
    
    if dry_run:
        print("\n" + "="*60)
        print("🔔 预览模式 - 未执行任何操作")
        print("   使用 --execute 参数执行实际操作")
        print("="*60)
        return {"plan": classification, "executed": False}
    
    # 执行整理
    print("\n" + "="*60)
    print("🚀 开始执行整理...")
    print("="*60)
    
    stats = {"created": 0, "added": 0, "failed": 0}
    
    for name, videos in classification.items():
        # 获取或创建合集
        if name in existing_names:
            collection = existing_names[name]
            print(f"\n📁 使用现有合集: {name} (ID: {collection['id']})")
        else:
            desc = next((r['description'] for r in COLLECTION_RULES if r['name'] == name), "")
            collection = api.create_collection(name, desc)
            if not collection:
                print(f"❌ 创建合集失败: {name}")
                stats['failed'] += len(videos)
                continue
            stats['created'] += 1
            existing_names[name] = collection
        
        # 添加视频到合集
        for video in videos:
            try:
                # 使用原始视频标题作为单集标题
                success = api.add_video_to_collection(
                    collection['id'], 
                    video['aid'], 
                    title=video['title']
                )
                if success:
                    stats['added'] += 1
                    print(f"   ✅ {video['title'][:40]}...")
                else:
                    stats['failed'] += 1
                time.sleep(2.0)  # 增加延迟以避免 Bilibili 接口频率限制
            except Exception as e:
                log.error(f"添加视频失败: {e}")
                stats['failed'] += 1
    
    print("\n" + "="*60)
    print("✅ 整理完成!")
    print(f"   创建合集: {stats['created']} 个")
    print(f"   添加视频: {stats['added']} 个")
    print(f"   失败: {stats['failed']} 个")
    print("="*60)
    
    return {"stats": stats, "executed": True}


def main():
    parser = argparse.ArgumentParser(description="批量整理视频到合集")
    parser.add_argument("--execute", action="store_true", 
                        help="执行实际操作 (默认为预览模式)")
    parser.add_argument("--ai", action="store_true",
                        help="使用 AI 进行智能分类 (更准确但更慢)")
    args = parser.parse_args()
    
    organize_all_videos(dry_run=not args.execute, use_ai=args.ai)


if __name__ == "__main__":
    main()

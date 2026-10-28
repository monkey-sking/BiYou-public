#!/usr/bin/env python3
"""
B站活动全量收集 + 分类匹配脚本（2026-08-10 升级版）。

旧版 sync_bili_activities.py 只按关键词搜索，UE5/游戏开发 等关键词搜不到
带 mission_id 的活动时返回空（B站当前无该分类活动）。新版：
  1. 遍历关键词收集当前所有带 mission_id 的创作激励活动（全量池）
  2. 按分类关键词匹配最合适的活动
  3. 输出 data/active_missions.json（分类 → mission_id）

用法:
    python3 scripts/sync_bili_activities_v2.py            # 同步并保存
    python3 scripts/sync_bili_activities_v2.py --list     # 只列出不保存
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.services.bili_api import create_session, get_bilibili_credentials, search_mission_activities

# 内容类别 → 搜索关键词（按优先级排序）
CATEGORY_KEYWORDS = {
    "AI日报": ["AI", "科技"],
    "知识科普": ["知识", "科普", "科学"],
    "健身运动": ["健身", "运动", "训练"],
    "UE5/游戏开发": ["UE5", "游戏开发", "虚幻引擎", "游戏"],
    "美食": ["美食", "烹饪"],
    "通用": ["创作", "日常"],
}

# 全量收集关键词（尽量覆盖所有分类）
ALL_KEYWORDS = [
    "AI", "科技", "知识", "科普", "健身", "运动", "训练",
    "UE5", "游戏开发", "虚幻引擎", "游戏", "美食", "烹饪",
    "创作", "日常", "CS2", "影视", "动画", "3D", "建模",
    "音乐", "舞蹈", "生活", "Vlog", "教育", "财经", "汽车",
    "数码", "摄影",
]

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "active_missions.json")


def collect_all_activities(session) -> dict:
    """遍历关键词收集所有带 mission_id 的活动。返回 {mission_id: name}。
    2026-08-10: 翻页收集（offset 0/10/20…），旧版只取第一页导致
    结果漂移（同关键词每次返回不同活动），现有配置无法稳定保留。"""
    all_acts = {}
    for kw in ALL_KEYWORDS:
        try:
            for offset in (0, 10, 20, 30):
                try:
                    acts = search_mission_activities(kw, session, offset=offset)
                except TypeError:
                    # 兼容旧签名（无 offset 参数）
                    acts = search_mission_activities(kw, session)
                if not acts:
                    break
                for a in acts:
                    mid = a.get("mission_id", 0)
                    name = a.get("name", "")
                    if mid > 0 and mid not in all_acts:
                        all_acts[mid] = name
                # 如果这一页不足 10 条，说明没有更多了
                if len(acts) < 10:
                    break
        except Exception:
            continue
    return all_acts


def match_category(category, all_acts, session) -> dict:
    """按分类关键词从全量池匹配活动。优先精确关键词，其次名称包含。"""
    keywords = CATEGORY_KEYWORDS.get(category, [])
    # 1. 精确关键词搜索（B站 话题搜索，可能返回未收录的）
    for kw in keywords:
        try:
            acts = search_mission_activities(kw, session, offset=0)
            for a in acts:
                mid = a.get("mission_id", 0)
                name = a.get("name", "")
                if mid > 0 and mid in all_acts:
                    # UE5/游戏开发 类严格匹配：活动名必须含 UE/虚幻/游戏开发/引擎
                    # （"游戏"太宽泛会撞上 CS2 等活动）
                    if category == "UE5/游戏开发":
                        # 严格匹配：活动名必须含 UE/虚幻/游戏开发/引擎
                        # 排除只有"游戏"的（会撞上 CS2 等活动）
                        if not any(t in name for t in ("UE", "虚幻", "游戏开发", "引擎")):
                            continue
                    return {"mission_id": mid, "name": name, "keyword": kw}
        except Exception:
            continue
    # 2. 从全量池按名称包含匹配（要求活动名包含分类特定词，避免宽泛词误匹配）
    for kw in keywords:
        for mid, name in all_acts.items():
            if kw.lower() in name.lower():
                # UE5/游戏开发 类：活动名必须含 UE/虚幻/游戏开发/引擎
                if category == "UE5/游戏开发":
                    if not any(t in name for t in ("UE", "虚幻", "游戏开发", "引擎")):
                        continue
                return {"mission_id": mid, "name": name, "keyword": kw}
    # 3. 兜底：UE5/游戏开发 类不接受 CS2 等明显不相关活动 —— 宁缺毋滥
    if category == "UE5/游戏开发":
        return {"mission_id": 0, "name": "", "keyword": ""}
    return {"mission_id": 0, "name": "", "keyword": ""}


def scan_activities() -> dict:
    """收集全量 + 分类匹配。优先保留现有 active_missions.json 的有效配置。"""
    cookies = get_bilibili_credentials()
    session = create_session(cookies)

    all_acts = collect_all_activities(session)

    # 读取现有配置，优先保留已生效的 mission_id（避免每次匹配结果漂移）
    existing = {}
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            pass

    result = {}
    for category in CATEGORY_KEYWORDS:
        old = existing.get(category, {})
        old_mid = old.get("mission_id", 0)
        old_name = old.get("name", "")
        # 现有有效配置且还在全量池中 → 保留
        if old_mid > 0 and old_mid in all_acts:
            result[category] = {
                "mission_id": old_mid,
                "name": old_name or all_acts[old_mid],
                "keyword": old.get("keyword", ""),
                "status": old.get("status"),
            }
        else:
            # 否则重新匹配
            result[category] = match_category(category, all_acts, session)

    session.close()
    return result


def main():
    parser = argparse.ArgumentParser(description="B站活动全量收集+匹配")
    parser.add_argument("--list", action="store_true", help="只列出不保存")
    args = parser.parse_args()

    print("🔍 收集B站当前有效活动...")
    activities = scan_activities()

    print(f"\n{'类别':12s} | mission_id | 活动名称")
    print("-" * 50)
    for cat, info in activities.items():
        mid = info.get("mission_id", 0)
        name = info.get("name", "")
        status = "✅" if mid else "❌"
        print(f"{status} {cat:10s} | {mid:>10} | {name}")

    if not args.list:
        os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
        # 保留之前有效结果：只覆盖成功获取的分类
        existing = {}
        if os.path.exists(OUTPUT_PATH):
            try:
                with open(OUTPUT_PATH) as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                pass
        for cat, info in activities.items():
            if info.get("mission_id", 0) > 0:
                existing[cat] = info
            elif cat not in existing:
                existing[cat] = info
        import tempfile, shutil
        tmp = tempfile.NamedTemporaryFile(mode="w", dir=os.path.dirname(OUTPUT_PATH), delete=False, suffix=".tmp", encoding="utf-8")
        json.dump(existing, tmp, ensure_ascii=False, indent=2)
        tmp.close()
        shutil.move(tmp.name, OUTPUT_PATH)
        print(f"\n✅ 已保存到: {OUTPUT_PATH}")
    else:
        print(f"\n📋 仅列出，未保存")


if __name__ == "__main__":
    main()

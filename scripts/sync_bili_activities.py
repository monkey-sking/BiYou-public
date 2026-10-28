#!/usr/bin/env python3
"""
B站活动ID自动同步脚本。

每天扫描B站当前有效活动(mission_id > 0)，按内容类别映射。
输出到 data/active_missions.json 供 BiYou 和 AI日报 使用。

用法:
    python3 scripts/sync_bili_activities.py          # 正常同步
    python3 scripts/sync_bili_activities.py --list   # 只列出不保存
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.services.bili_api import create_session, get_bilibili_credentials, search_mission_activities

# 内容类别 → 搜索关键词（按优先级排序）
CATEGORY_KEYWORDS = {
    "AI日报": ["我们AI联盟", "花生AI创作", "AI", "科技"],
    "知识科普": ["知识", "科技", "科普"],
    "健身运动": ["健身", "运动", "训练"],
    "UE5/游戏开发": ["UE5", "游戏开发", "虚幻引擎"],
    "美食": ["美食", "烹饪"],
    "通用": ["创作", "日常"],
}

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "active_missions.json")


def scan_activities() -> dict:
    """扫描B站当前有效活动，按类别返回最佳匹配。"""
    cookies = get_bilibili_credentials()
    session = create_session(cookies)

    result = {}
    seen_ids = set()

    for category, keywords in CATEGORY_KEYWORDS.items():
        best = None
        for kw in keywords:
            acts = search_mission_activities(kw, session)
            for a in acts:
                mid = a.get("mission_id", 0)
                name = a.get("name", "")
                if mid and mid not in seen_ids:
                    if best is None:
                        best = {"mission_id": mid, "name": name, "keyword": kw}
                    break
            if best:
                # 标记最佳匹配为已用，但不同类别可以共享活动
                pass

        if best:
            result[category] = best
        else:
            result[category] = {"mission_id": 0, "name": "", "keyword": ""}

    session.close()
    return result


def main():
    parser = argparse.ArgumentParser(description="B站活动ID同步")
    parser.add_argument("--list", action="store_true", help="只列出不保存")
    args = parser.parse_args()

    print("🔍 扫描B站当前有效活动...")
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
        # 保留之前的有效结果：只覆盖成功获取的分类，API 失败的分类不动
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

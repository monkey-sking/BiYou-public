#!/usr/bin/env python3
"""B站私信资源意图判定 —— 回归测试。

跑法: /opt/homebrew/bin/python3.11 scripts/test_dm_intent.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.bili_dm.monitor import has_resource_intent, _extract_query

# (内容, 是否应触发)
CASES = [
    # ── 必须【不触发】的：寒暄/致谢/评价/无关 ──────────────────
    ("大佬你好", False),
    ("谢谢大佬", False),
    ("这个视频做得不错", False),
    ("你好", False),
    ("在吗", False),
    ("关注你了", False),
    ("已三连", False),
    ("已转3", False),
    ("礼貌问一下您做什么工作的", False),
    ("你分享的观点我认同", False),
    ("辛苦了", False),
    ("支持一下", False),
    ("厉害了大佬", False),
    ("催更", False),
    ("做饭，健身", False),
    ("已投币", False),

    # ── 必须【触发】的：真求资源 ──────────────────────────────
    ("求个风衣的链接", True),
    ("求分享个 HIP", True),
    ("已三连～求大佬求分享个【Houdini 波浪曲线】hip", True),
    ("大佬 那个3D模型资源能发一下吗", True),
    ("波浪曲线的hip在哪里获取", True),
    ("有agls1.8的文件吗", True),
    ("集成合集在哪下", True),
    ("具体的教程在哪里呢，有出处吗", True),
    ("博主，这个有高清些的视频没", True),
    ("求资源", True),
    ("发我一份素材", True),
    ("网盘链接有吗", True),
    ("这个模型文件能给我吗", True),
    ("素材在哪下载", True),
    ("想要这个工程的源文件", True),
]


def main():
    fails = []
    print("=" * 68)
    print("B站私信资源意图判定 · 回归测试")
    print("=" * 68)

    print("\n【不该触发的（寒暄/致谢/评价）】")
    for text, expect in CASES:
        if expect:
            continue
        got = has_resource_intent(text)
        ok = got == expect
        if not ok:
            fails.append((text, expect, got))
        print(f"  {'✅' if ok else '❌'} 触发={got!s:5} | {text!r}")

    print("\n【必须触发的（真求资源）】")
    for text, expect in CASES:
        if not expect:
            continue
        got = has_resource_intent(text)
        ok = got == expect
        if not ok:
            fails.append((text, expect, got))
        q = _extract_query(text)
        print(f"  {'✅' if ok else '❌'} 触发={got!s:5} query={q!r:32} | {text!r}")

    print("\n" + "=" * 68)
    if fails:
        print(f"❌ {len(fails)} 条失败:")
        for t, e, g in fails:
            print(f"   期望={e} 实际={g} | {t!r}")
        return 1
    print(f"✅ 全部 {len(CASES)} 条通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

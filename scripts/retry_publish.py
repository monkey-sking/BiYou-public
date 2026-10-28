#!/usr/bin/env python3
"""
Retry failed multi-platform publishes from history records.

Usage:
    python3 scripts/retry_publish.py                    # Retry all recent failures (dry-run)
    python3 scripts/retry_publish.py --run              # Actually retry
    python3 scripts/retry_publish.py --platforms douyin,xiaohongshu  # Only specific platforms
    python3 scripts/retry_publish.py --last 3           # Only last 3 failed records
"""
import argparse
import json
import os
import sys

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from core.utils.config import load_config
from core.utils.logger import log

CONFIG = load_config()
HISTORY_DIR = os.path.join(PROJECT_ROOT, "history")

ALL_PLATFORMS = ["douyin", "xiaohongshu", "wechat_channels", "kuaishou"]


def list_failed_records(platforms, last_n=0):
    """List history records that have failures on the specified platforms."""
    if not os.path.isdir(HISTORY_DIR):
        print(f"❌ History directory not found: {HISTORY_DIR}")
        return []

    files = sorted(
        (f for f in os.listdir(HISTORY_DIR) if f.endswith(".json") and f != "publishing_jobs.json"),
        key=lambda f: os.path.getmtime(os.path.join(HISTORY_DIR, f)),
        reverse=True,
    )

    records = []
    for fname in files:
        path = os.path.join(HISTORY_DIR, fname)
        try:
            with open(path, encoding="utf-8") as fp:
                d = json.load(fp)
        except Exception:
            continue

        pub = d.get("publish_records") or {}
        video_path = d.get("video_path", "")
        video_ok = os.path.isfile(video_path) if video_path else False

        # Collect platforms that failed (not published/scheduled)
        failed_platforms = []
        for plat in platforms:
            info = pub.get(plat, {})
            if not info:
                # No record at all — treat as pending (needs publishing)
                failed_platforms.append(plat)
            elif info.get("status", "") not in ("published", "scheduled"):
                failed_platforms.append(plat)

        if not failed_platforms:
            continue

        records.append({
            "file": fname,
            "path": path,
            "title": d.get("title", "N/A")[:70],
            "video_path": video_path,
            "video_ok": video_ok,
            "failed_platforms": failed_platforms,
            "errors": {
                plat: (pub.get(plat) or {}).get("last_error", "(no record)")[:120]
                for plat in failed_platforms
            },
            "metadata": d,
        })

    if last_n > 0:
        records = records[:last_n]

    return records


def main():
    parser = argparse.ArgumentParser(description="Retry failed multi-platform publishes")
    parser.add_argument("--run", action="store_true", help="Actually run (default is dry-run)")
    parser.add_argument("--platforms", type=str, default="douyin,xiaohongshu,wechat_channels",
                        help="Comma-separated list of platforms to retry (default: douyin,xiaohongshu,wechat_channels)")
    parser.add_argument("--last", type=int, default=0, help="Only process the last N failed records (0=all)")
    parser.add_argument("--all-platforms", action="store_true",
                        help="Include kuaishou in the retry targets as well")
    args = parser.parse_args()

    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    if args.all_platforms:
        platforms = ALL_PLATFORMS

    dry_run = not args.run

    print("=" * 60)
    if dry_run:
        print("⚡️ [Dry Run] 重新发布预览")
    else:
        print("🚀 重新发布 (实际执行)")
    print("=" * 60)
    print(f"  目标平台: {', '.join(platforms)}")
    print()

    # Import the publish function
    from main import run_multi_platform_publish

    records = list_failed_records(platforms, last_n=args.last)
    if not records:
        print("✅ 没有找到需要重试的记录！")
        return

    # Filter to records with existing video files
    runnable = [r for r in records if r["video_ok"]]
    skipped = [r for r in records if not r["video_ok"]]

    if skipped:
        print(f"⚠️ 跳过 {len(skipped)} 条记录（视频文件不存在）：")
        for r in skipped:
            print(f"   - {r['title']}")
        print()

    print(f"📋 找到 {len(runnable)} 条可重试的记录：")
    for i, r in enumerate(runnable, 1):
        print(f"\n  {i}. {r['title']}")
        print(f"     文件: {r['file']}")
        print(f"     失败平台: {', '.join(r['failed_platforms'])}")
        for plat, err in r["errors"].items():
            print(f"     {plat}: {err}")

    if not runnable:
        print("\n❌ 没有可执行的记录。")
        return

    if dry_run:
        print("\n" + "=" * 60)
        print("ℹ️  以上为预览。使用 --run 参数实际执行重试。")
        print("=" * 60)
        return

    # Actually run the retries
    print("\n" + "=" * 60)
    print("🚀 开始重试发布...")
    print("=" * 60)

    success_count = 0
    fail_count = 0

    for i, r in enumerate(runnable, 1):
        metadata = r["metadata"]
        metadata_path = r["path"]

        print(f"\n{'─' * 50}")
        print(f"📹 [{i}/{len(runnable)}] {r['title']}")
        print(f"   失败平台: {', '.join(r['failed_platforms'])}")
        print(f"{'─' * 50}")

        try:
            success = run_multi_platform_publish(
                metadata,
                metadata_path,
                schedule_time=0,
                dry_run=False,
            )
            if success:
                log.info(f"✅ 重试成功: {r['title']}")
                success_count += 1
            else:
                log.error(f"❌ 重试失败: {r['title']}")
                fail_count += 1
        except Exception as e:
            log.error(f"❌ 重试异常: {r['title']} - {e}")
            fail_count += 1

    print("\n" + "=" * 60)
    print("📊 重试结果汇总")
    print("=" * 60)
    print(f"  ✅ 成功: {success_count}")
    print(f"  ❌ 失败: {fail_count}")
    print(f"  📋 总计: {len(runnable)}")


if __name__ == "__main__":
    main()

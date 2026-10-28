#!/usr/bin/env python3
"""Poll Bilibili private messages and reply with locally stored resource links.

Usage:
    python scripts/bili_resource_dm_monitor.py [--dry-run]

Options:
    --dry-run       Poll once without sending replies (prints what would be sent).
    --interval N    Poll interval in seconds (default: 300 = 5 minutes).
"""

import argparse
import sys

from core.bili_dm.monitor import ResourceDMMonitor
from core.kb.database import search_links


def main():
    parser = argparse.ArgumentParser(
        description="B站私信资源链接自动回复监控"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Poll once and print what would be replied without sending.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Poll interval in seconds (default: 300).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Poll once and exit (useful for cron).",
    )
    args = parser.parse_args()

    monitor = ResourceDMMonitor.from_bilibili_cookies(
        sleep_seconds=args.interval,
    )

    if args.dry_run:
        print("🔍 DRY RUN: Checking for resource-related messages...")
        for msg in monitor.client.fetch_new_messages():
            from core.bili_dm.monitor import _extract_query
            query = _extract_query(msg.get("content", ""))
            if not query:
                continue
            matches = search_links(query, limit=1)
            if matches:
                print(f"  → Would reply to uid={msg['uid']}: {matches[0]['video_title']}")
            else:
                print(f"  → No KB match for uid={msg['uid']} query='{query}'")
        print("✅ Dry run complete.")
        return

    if args.once:
        sent = monitor.poll_once()
        print(f"✅ Poll once complete, sent {sent} replies.")
        return

    monitor.run_forever()


if __name__ == "__main__":
    main()

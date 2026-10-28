#!/usr/bin/env python3
import argparse
import copy
import json
import os
import sys

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.utils.config import load_config
from core.workflows.channel_daily_publish import ChannelDailyPublishWorkflow


def build_config_with_overrides(args) -> dict:
    config = load_config()
    merged = copy.deepcopy(config)
    section = copy.deepcopy(merged.get("channel_daily_publish", {}) or {})

    if args.channel_url:
        section["channel_url"] = args.channel_url
    if args.publish_time:
        section["publish_time"] = args.publish_time
    if args.timezone:
        section["timezone"] = args.timezone
    if args.sync_window is not None:
        section["sync_window"] = args.sync_window
    if args.state_file:
        section["state_file"] = args.state_file

    merged["channel_daily_publish"] = section
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync a YouTube channel into local backlog and schedule one Bilibili publish per day."
    )
    parser.add_argument("command", choices=["sync", "publish", "run"], help="Workflow command")
    parser.add_argument("--channel-url", type=str, help="Override the configured YouTube channel videos URL")
    parser.add_argument("--publish-time", type=str, help="Override the configured daily publish time (HH:MM)")
    parser.add_argument("--timezone", type=str, help="Override the configured timezone")
    parser.add_argument("--sync-window", type=int, help="Override how many latest channel items to inspect")
    parser.add_argument("--state-file", type=str, help="Override the backlog state file path")
    args = parser.parse_args()

    workflow = ChannelDailyPublishWorkflow(config=build_config_with_overrides(args))
    if not workflow.channel_url:
        print("❌ Missing channel URL. Configure channel_daily_publish.channel_url or pass --channel-url.")
        return 1

    if args.command == "sync":
        state = workflow.sync()
        print(json.dumps({"backlog_items": len(state.get("items", {}))}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "publish":
        item = workflow.publish()
        print(json.dumps({"scheduled_item": item}, ensure_ascii=False, indent=2))
        return 0

    state = workflow.sync()
    item = workflow.publish()
    print(
        json.dumps(
            {
                "backlog_items": len(state.get("items", {})),
                "scheduled_item": item,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

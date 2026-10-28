#!/usr/bin/env python3.11
"""批量处理缺少字幕的视频历史文件。扫描 Download/Videos 下的 .json 文件。
也适用于视频上传后投稿评论的修正。

用法: scripts/scan_missing_subtitles.py [--recent-n N] [--dry-run]
"""
import glob, json, os, sys, time

VIDEO_DIR = "${HOME}/Downloads/Videos"
SUBTITLE_SCRIPT = os.path.expanduser("~/.hermes/scripts/biyou_retry_subtitles.py")

def find_pending_videos(limit=20):
    """找需要字幕但还没处理过的 history 文件"""
    pending = []
    for f in sorted(glob.glob(os.path.join(VIDEO_DIR, "*.json")), key=os.path.getmtime, reverse=True):
        if f.endswith(".info.json"):
            continue
        try:
            with open(f) as fh:
                data = json.load(fh)
            status = data.get("subtitle_uploaded")
            if status is None:
                bvid = data.get("uploaded_bvid") or data.get("publish_records", {}).get("bilibili", {}).get("platform_content_id")
                if bvid:
                    pending.append((f, os.path.getmtime(f), bvid))
        except:
            pass
        if len(pending) >= limit:
            break
    return pending


def main():
    dry_run = "--dry-run" in sys.argv
    recent_n = 20
    for a in sys.argv[1:]:
        if a.startswith("--recent-n="):
            recent_n = int(a.split("=")[1])
    
    pending = find_pending_videos(limit=recent_n)
    if not pending:
        print("✅ 没有等待字幕的视频")
        return
    
    print(f"📋 发现 {len(pending)} 个视频需要字幕:")
    for path, mtime, bvid in pending:
        name = os.path.basename(path).replace(".json", "")
        print(f"  📹 {name[:60]}")
        print(f"     BVID: {bvid}, 修改: {time.strftime('%m-%d %H:%M', time.localtime(mtime))}")
    
    if dry_run:
        print("\n🏁 DRY-RUN: 以上是待处理视频，未实际执行")
        return
    
    for path, mtime, bvid in pending:
        print(f"\n  → 处理 {os.path.basename(path)[:50]}...")
        # Set env for subtitle script
        env = os.environ.copy()
        env["BIYOU_SUBTITLE_HISTORY_PATH"] = path
        import subprocess
        result = subprocess.run(
            ["/opt/homebrew/bin/python3.11", SUBTITLE_SCRIPT],
            capture_output=True, text=True, timeout=120,
            env=env,
        )
        output = (result.stdout + result.stderr).strip()
        if output:
            print(f"     {output[:200]}")
        elif result.returncode == 0:
            print("     ✅ 静默完成")
        else:
            print(f"     ⚠️  退出码 {result.returncode}")
        time.sleep(5)  # 避免触发限流
    
    print(f"\n✅ 处理完成 {len(pending)} 个视频")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Subtitle Sync Workflow
Scans video directory for videos that have been uploaded (have bvid) 
but missing subtitles, and uploads them.
"""
import os
import json
import argparse
from core.utils.config import load_config
from core.utils.logger import log
from core.services.bili_api import get_bilibili_credentials, create_session, upload_subtitle
from core.publishing.records import get_bilibili_publish_ids

CONFIG = load_config()
VIDEO_DIR = CONFIG.get('video_source_dir')
METADATA_FILE = CONFIG.get('metadata_file')
VERBOSE_CONSOLE_LOGS = bool(CONFIG.get("verbose_console_logs", False))


def _console(msg):
    print(msg)


def _verbose(msg):
    if VERBOSE_CONSOLE_LOGS:
        print(msg)

def get_metadata_files(source_dir):
    """
    Find all .json metadata files in the source directory.
    """
    if not source_dir or not os.path.exists(source_dir):
        return []
    
    files = []
    for f in os.listdir(source_dir):
        if f.endswith('.json') and not f.endswith('.info.json'): # Exclude yt-dlp info jsons if they clutter
            # Actually our metadata files are typically VideoName.json
            # yt-dlp produces VideoName.info.json. 
            # We want the ones managed by fetcher/uploader.
            # fetcher.py produces {base_name}.json.
            # Let's simple check if it has 'bvid' or 'uploaded_bvid' later.
            files.append(os.path.join(source_dir, f))
    return files

def sync_subtitles(dry_run=False):
    """
    Main sync logic
    """
    _console(f"📂 字幕同步：扫描目录 {VIDEO_DIR}")
    
    meta_files = get_metadata_files(VIDEO_DIR)
    
    # Also scan history dir
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    HISTORY_DIR = os.path.join(PROJECT_ROOT, 'history')
    if os.path.exists(HISTORY_DIR):
        _verbose(f"📂 扫描历史目录: {HISTORY_DIR}")
        history_files = get_metadata_files(HISTORY_DIR)
        meta_files.extend(history_files)

    # Deduplicate in case same file appears in multiple scan roots
    meta_files = list(dict.fromkeys(meta_files))
    _console(f"🔎 共发现 {len(meta_files)} 个元数据文件")
    
    # Init session
    cookie_data = get_bilibili_credentials()
    if not cookie_data:
        _console("❌ 登录失败，无法继续字幕同步。")
        return
    
    session = create_session(cookie_data)
    
    count_scanned = 0
    count_with_bvid = 0
    count_missing_subtitle = 0
    count_processed = 0
    count_uploaded = 0
    count_failed = 0
    seen_targets = set()
    
    for meta_path in meta_files:
        count_scanned += 1
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            _verbose(f"⚠️ 元数据读取失败: {meta_path} ({e})")
            continue
            
        bvid, aid = get_bilibili_publish_ids(data)
        if not bvid:
            continue
        count_with_bvid += 1
            
        # Check if already uploaded subtitle
        if data.get('subtitle_uploaded'):
            _verbose(f"⏭️ 已上传字幕，跳过: {bvid}")
            continue
            
        # Check if local subtitle exists
        # Assuming metadata file is in same dir as video/subtitle
        base_path = os.path.splitext(meta_path)[0] 
        # But wait, metadata path depends on how it was saved.
        # Fetcher saves it as: videos/VideoName.json
        # Subtitles should be: videos/VideoName.zh-Hans.vtt
        
        candidates = [
            f"{base_path}.zh-Hans.vtt",
            f"{base_path}.zh-Hans.srt",
            f"{base_path}.zh.vtt",
            f"{base_path}.zh.srt",
            f"{base_path}.vtt",
            f"{base_path}.srt"
        ]
        
        sub_path = None
        for p in candidates:
            if os.path.exists(p):
                sub_path = p
                break
        
        # If not found in same dir, check VIDEO_DIR using filename
        if not sub_path and VIDEO_DIR and os.path.exists(VIDEO_DIR):
             filename = os.path.basename(base_path)
             for ext in ['.zh-Hans.vtt', '.zh-Hans.srt', '.zh.vtt', '.zh.srt', '.vtt', '.srt']:
                 candidate = os.path.join(VIDEO_DIR, filename + ext)
                 if os.path.exists(candidate):
                     sub_path = candidate
                     break
        
        if not sub_path:
            count_missing_subtitle += 1
            _verbose(f"⚠️ 未找到字幕文件: {bvid}")
            continue

        target_key = (bvid, os.path.basename(sub_path))
        if target_key in seen_targets:
            _verbose(f"⏭️ 重复字幕任务，跳过: {bvid} <- {os.path.basename(sub_path)}")
            continue
        seen_targets.add(target_key)
            
        count_processed += 1
        _verbose(f"🎬 字幕候选: {bvid} <- {os.path.basename(sub_path)}")
        
        if dry_run:
            _console(f"⚡️ [Dry Run] 将上传字幕: {bvid} <- {os.path.basename(sub_path)}")
            continue
            
        # Upload
        success = upload_subtitle(bvid, sub_path, session, aid=aid, verbose=VERBOSE_CONSOLE_LOGS)
        
        if success:
            data['subtitle_uploaded'] = True
            try:
                with open(meta_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)
                _verbose("✅ 元数据已更新")
                _console(f"✅ 字幕上传成功: {bvid}")
                count_uploaded += 1
            except Exception as e:
                count_failed += 1
                _console(f"❌ 写回元数据失败: {bvid} ({e})")
        else:
            count_failed += 1
            _console(f"❌ 字幕上传失败: {bvid}")
            
    _console("\n" + "="*50)
    _console("🎉 字幕同步完成")
    _console(f"   扫描元数据: {count_scanned}")
    _console(f"   含 bvid: {count_with_bvid}")
    _console(f"   缺少字幕: {count_missing_subtitle}")
    _console(f"   待处理数: {count_processed}")
    _console(f"   成功数: {count_uploaded}")
    _console(f"   失败数: {count_failed}")

def main():
    parser = argparse.ArgumentParser(description='Bilibili Subtitle Sync Tool')
    parser.add_argument('-n', '--dry-run', action='store_true', help='Dry run')
    args = parser.parse_args()
    
    sync_subtitles(dry_run=args.dry_run)

if __name__ == "__main__":
    main()

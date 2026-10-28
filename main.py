#!/usr/bin/env python3
"""
Unified Automation Runner
Orchestrates the entire workflow:
1. Fetch & Translate Metadata
2. Process Cover (Gemini Web)
3. Upload to Bilibili
"""
import argparse
import sys
import os
import atexit
import json
import logging
import asyncio
import concurrent.futures
import time
import copy
import threading
import yaml
from datetime import datetime, timedelta, timezone
from core.utils.logger import log
from core.utils.gemini_patch import apply_gemini_patches
from core.utils.workflow_signals import WorkflowSignalRecorder
from core.publishing.platform_payloads import (
    PLATFORM_VARIANT_TARGETS,
    build_platform_cover_variant_specs,
    build_platform_payloads,
)

# Apply patches to third-party libraries before other logic
apply_gemini_patches()

import signal
import subprocess
import psutil # You might not have psutil installed, better use pkill via subprocess


# Logger is already initialized in core.utils.logger

from core.utils.config import PROJECT_ROOT, load_config

CONFIG = load_config()
METADATA_FILE = CONFIG['metadata_file']

# Import modules
try:
    from core.modules.fetcher import (
        get_latest_video,
        process_metadata,
        should_refresh_existing_collection,
    )
    from core.modules.uploader import run_upload
    from core.modules.commenter import post_comment_flow
    from core.modules.downloader import download_video_stream, fetch_video_info
    from core.modules.subtitle_sync import sync_subtitles
    from core.workflows.cover_workflow import CoverWorkflow
    from core.utils.i18n import print_t, i18n
    from core.utils.url_utils import parse_youtube_video_id, is_supported_video_url, split_supported_video_urls
    from core.services.bili_api import search_mission_activities, create_session, get_bilibili_credentials
    from core.utils.video_utils import verify_video_integrity
    from core.utils.time_utils import parse_schedule_time
    from core.publishing.manager import PublishManager
    from core.publishing.models import PublishJob, ScheduleIntent
    from core.publishing.registry import build_registry
    from core.publishing.adapters.bilibili import BilibiliAdapter
    from core.publishing.adapters.douyin import DouyinAdapter
    from core.publishing.adapters.douyin import DouyinAutomationAdapter
    from core.publishing.adapters.douyin_sau import DouyinSAUAdapter
    from core.publishing.adapters.kuaishou import KuaishouAdapter
    from core.publishing.adapters.kuaishou import KuaishouAutomationAdapter
    from core.publishing.adapters.kuaishou_sau import KuaishouSAUAdapter
    from core.publishing.adapters.wechat_channels_sau import WechatChannelsSAUAdapter
    from core.publishing.adapters.xiaohongshu_sau import XiaohongshuSAUAdapter
    from core.publishing.adapters.wechat_channels import WechatChannelsAdapter
    from core.publishing.adapters.xiaohongshu import XiaohongshuAdapter
    from core.services.douyin_api import DouyinApiClient
    from core.services.kuaishou_api import KuaishouApiClient
except ImportError as e:
    log.critical(f"Import error: {e}")
    log.error("Please ensure you are running from the project root or have PYTHONPATH set.")
    sys.exit(1)


# ---------------------------------------------------------------------------
# 单视频并发锁 (2026-09-30)
#
# 起因：2026-09-30 07:31:44 与 07:32:37 两个 BiYou 进程并发跑了同一支 YouTube
# 视频 (uVAkIOy2Qwk)，各自完整走完 下载→封面→上传，最后在 B 站投出两条一模一样的
# 稿件 (BV1ghaR6pEz1 / BV1ghaR6pErS)。
#
# 为什么不靠 check_history_dedup：历史归档是在**上传成功后**才写入的，两个进程
# 同时启动时谁都还看不到对方那条记录（典型 TOCTOU）。而各个入口（WebSub watchdog /
# RSS 兜底 / 队列补跑 / 手动调用）之间只有 "pgrep run-by.sh" 这种软检查，拿不到
# "这支视频此刻正被另一个进程处理" 的原子事实。
#
# 因此这里用 mkdir(2) 的原子性做「一支视频一把锁」：先抢到锁的进程才能跑，
# 后来者若发现持有者还活着就直接跳过，持有者已死则清理陈旧锁后重抢。
# ---------------------------------------------------------------------------
VIDEO_LOCK_ROOT = os.path.expanduser("~/.hermes/biyou/locks")
_HELD_VIDEO_LOCKS = []
_VIDEO_LOCKS_RELEASED = False


def _video_lock_key(url: str) -> str:
    """锁文件名：优先 YouTube video id，解析不出时退化为 URL 的 sha1。"""
    try:
        vid = parse_youtube_video_id(url)
    except Exception:
        vid = None
    if vid:
        return vid
    import hashlib
    return "u" + hashlib.sha1((url or "").encode("utf-8")).hexdigest()[:16]


def _video_lock_holder_pid(lock_dir: str):
    """返回持有者 PID；锁不存在/内容损坏/进程已死则返回 None。"""
    try:
        with open(os.path.join(lock_dir, "owner")) as f:
            pid = int(f.read().strip().split("\t")[0])
    except Exception:
        return None
    try:
        os.kill(pid, 0)
        return pid
    except Exception:
        return None


def acquire_video_lock(url: str):
    """原子抢占一支视频的运行锁。

    返回 (lock_dir, ok, info)。ok=False 表示另一进程正在处理同一支视频。
    """
    try:
        os.makedirs(VIDEO_LOCK_ROOT, exist_ok=True)
    except Exception as e:
        log.warning(f"⚠️ 视频锁目录创建失败，降级为不加锁继续: {e}")
        return None, True, ""

    lock_dir = os.path.join(VIDEO_LOCK_ROOT, f"{_video_lock_key(url)}.lock")
    for attempt in range(2):
        try:
            os.mkdir(lock_dir)  # atomic: 目录已存在则抛 FileExistsError
            with open(os.path.join(lock_dir, "owner"), "w") as f:
                f.write(f"{os.getpid()}\t{datetime.now().isoformat(timespec='seconds')}\t{url}\n")
            _HELD_VIDEO_LOCKS.append(lock_dir)
            atexit.register(release_video_locks)
            return lock_dir, True, ""
        except FileExistsError:
            pid = _video_lock_holder_pid(lock_dir)
            if pid:
                try:
                    with open(os.path.join(lock_dir, "owner")) as f:
                        info = f.read().strip()
                except Exception:
                    info = f"pid={pid}"
                return lock_dir, False, info
            if attempt == 0:
                import shutil
                shutil.rmtree(lock_dir, ignore_errors=True)
                continue
            return lock_dir, False, "stale_lock"
        except Exception as e:
            log.warning(f"⚠️ 视频锁获取异常，降级为不加锁继续: {e}")
            return None, True, ""
    return lock_dir, False, "unknown"


def release_video_locks():
    """释放本进程持有的全部视频锁（幂等）。"""
    global _VIDEO_LOCKS_RELEASED
    if _VIDEO_LOCKS_RELEASED:
        return
    _VIDEO_LOCKS_RELEASED = True
    import shutil
    for lock_dir in list(_HELD_VIDEO_LOCKS):
        try:
            shutil.rmtree(lock_dir, ignore_errors=True)
        except Exception:
            pass
    _HELD_VIDEO_LOCKS.clear()


def _shadow_cover_gate(cover_path, title):
    """2026-10-01 shadow: 把最终放行的封面丢给 jev_cover_shadow 后台记录判定。

    借鉴《给记忆系统装上反射弧》的 shadow 上线法：只记录、不阻塞、不改流程。
    用 detach 的 Popen（start_new_session），BiYou 主进程后续退出也不影响它写完记录。
    shadow 永不影响主流程 —— 任何异常都吞掉。
    """
    try:
        script = os.path.expanduser("~/.hermes/scripts/jev_cover_shadow.py")
        if not os.path.exists(script):
            return
        if not cover_path or not os.path.exists(cover_path):
            return
        if not title or not str(title).strip():
            return  # 没标题就没法判「封面文字是否匹配标题」，记了也是噪音
        subprocess.Popen(
            [sys.executable, script, cover_path, title or ""],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def save_history_entry(meta, config):
    """
    Save metadata to history directory. 
    Can be called multiple times to update status.
    """
    video_path = meta.get('video_path')
    title = meta.get('title')
    
    if not video_path and not title:
        return

    # Determine filename
    if video_path:
        base_name = os.path.splitext(os.path.basename(video_path))[0]
    else:
        # If no video path yet, simplify title
        import re
        base_name = re.sub(r'[\\/*?:"<>|]', "", title).strip()

    # Use project_root from config (defined in config.py as parent of automation)
    from core.utils.config import PROJECT_ROOT
    history_dir = os.path.join(config.get('project_root', PROJECT_ROOT), 'history')
    history_dir = os.path.abspath(history_dir)
    if not os.path.exists(history_dir):
        os.makedirs(history_dir)
    
    history_path = os.path.join(history_dir, f"{base_name}.json")

    merged_meta = {}
    if os.path.exists(history_path):
        try:
            with open(history_path, 'r', encoding='utf-8') as f:
                existing_meta = json.load(f)
            merged_meta = merge_metadata_records(existing_meta, meta)
        except Exception as e:
            log.warning(f"⚠️ Failed to load existing history entry for merge: {e}")
            merged_meta = merge_metadata_records({}, meta)
    else:
        merged_meta = merge_metadata_records({}, meta)

    with open(history_path, 'w', encoding='utf-8') as f:
        json.dump(merged_meta, f, ensure_ascii=False, indent=4)


def check_history_dedup(yt_id: str, config: dict) -> tuple:
    """Check if a YouTube video was already processed and decide how to handle it.

    Returns: (action: str, detail: str, title: str)
      action = 'skip' → already published, skip entirely
      action = 'flag_skip' → manually flagged (political risk, manual skip), skip with reason
      action = '' → not found or not published, let through (may resume partial progress)
    """
    if not yt_id:
        return "", "", ""

    from core.utils.config import PROJECT_ROOT
    history_dir = os.path.join(config.get('project_root', PROJECT_ROOT), 'history')
    history_dir = os.path.abspath(history_dir)
    if not os.path.isdir(history_dir):
        return "", "", ""

    for fname in os.listdir(history_dir):
        if not fname.endswith('.json') or fname == 'channel_daily_publish.json':
            continue
        fpath = os.path.join(history_dir, fname)
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue

        # Match by video ID at top-level or inside info_dict
        record_id = data.get('id') or (data.get('info_dict') or {}).get('id')
        if record_id != yt_id:
            continue

        title = data.get('title', '') or ''

        # Check manual flags first
        flags = data.get('flags') or data.get('manual_flags') or {}
        if flags.get('manual_skip'):
            reason = flags.get('reason') or flags.get('manual_skip_reason') or '人工标记跳过'
            return 'flag_skip', reason, title
        if flags.get('political_risk'):
            reason = flags.get('reason') or '政治风险'
            return 'flag_skip', reason, title

        # Check if successfully published to Bilibili
        pub = data.get('publish_records', {}) or {}
        bili = pub.get('bilibili', {}) or {}
        bvid = bili.get('platform_content_id') or data.get('uploaded_bvid') or ''
        aid = bili.get('aid') or data.get('uploaded_aid') or ''
        if bvid or aid:
            return 'skip', bvid, title

        # Effect sandwich: 已结算的 bili_upload → 视为已发布（bvid 在 effect.result，
        # 覆盖“结算后、归档前崩溃”的窗口，避免重复上传）
        for e in (data.get('effects') or []):
            if (isinstance(e, dict) and e.get('kind') == 'bili_upload'
                    and e.get('status') == 'settled'
                    and (e.get('result') or {}).get('bvid')):
                return 'skip', e['result']['bvid'], title

        # Effect sandwich: 存在未结算的外部副作用 intent（上次可能崩溃在
        # submit/发评论中途）→ 阻止盲目重跑，避免重复视频/重复评论。
        try:
            from core.utils.effects import find_pending, is_enabled
            if is_enabled(config) and find_pending(data):
                pending_kinds = [str(e.get('kind')) for e in (data.get('effects') or [])
                                 if isinstance(e, dict) and e.get('status') == 'pending']
                return 'intent_pending', ','.join(pending_kinds) or 'pending_effect', title
        except Exception:
            pass

        # In progress / partial — let through, pipeline will reuse assets
        return '', '', title

    return "", "", ""


def merge_metadata_records(base_meta, incoming_meta):
    merged = {}
    if isinstance(base_meta, dict):
        merged.update(base_meta)
    if isinstance(incoming_meta, dict):
        merged.update(incoming_meta)

    for nested_key in ("publish_records", "platform_payloads"):
        base_nested = base_meta.get(nested_key, {}) if isinstance(base_meta, dict) else {}
        incoming_nested = incoming_meta.get(nested_key, {}) if isinstance(incoming_meta, dict) else {}
        combined_nested = {}
        if isinstance(base_nested, dict):
            combined_nested.update(base_nested)
        if isinstance(incoming_nested, dict):
            combined_nested.update(incoming_nested)
        if combined_nested:
            merged[nested_key] = combined_nested

    return merged


def load_cached_urls(cache_file):
    """Load pending URLs from cache, supporting legacy single-URL format."""
    if not os.path.exists(cache_file):
        return []

    try:
        with open(cache_file, 'r', encoding='utf-8') as f:
            raw = f.read().strip()
    except Exception:
        return []

    if not raw:
        return []

    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [u for u in data if isinstance(u, str) and u.strip()]
        if isinstance(data, str) and data.strip():
            return [data.strip()]
    except Exception:
        pass

    return [raw]


def save_cached_urls(cache_file, urls):
    """Persist only pending/unuploaded URLs; clear cache when none remain."""
    urls = [u for u in urls if isinstance(u, str) and u.strip()]

    try:
        if urls:
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(urls, f, ensure_ascii=False, indent=2)
        elif os.path.exists(cache_file):
            os.remove(cache_file)
    except Exception as e:
        log.warning(f"⚠️ URL 缓存更新失败: {e}")


def _write_pending_cache(cache_file, pending_urls, lock=None):
    """Persist current pending URL set in a stable order."""
    if lock:
        with lock:
            urls = sorted([u for u in pending_urls if u])
    else:
        urls = sorted([u for u in pending_urls if u])
    save_cached_urls(cache_file, urls)


def _print_invalid_url_summary(invalid_urls, include_count=False):
    """Print a concise summary for skipped non-video URLs."""
    if not invalid_urls:
        return

    if include_count:
        print_t('url_invalid_summary', len(invalid_urls))
    print(i18n.get('url_invalid_list_title'))
    for invalid_url in invalid_urls:
        print(f"     - {invalid_url}")


def _schedule_time_to_iso(schedule_time):
    if not schedule_time:
        return ""
    return datetime.fromtimestamp(schedule_time, tz=timezone.utc).astimezone().isoformat()


def _resolve_project_path(path):
    if not path:
        return ""
    expanded = os.path.expanduser(path)
    if os.path.isabs(expanded):
        return expanded
    return os.path.abspath(os.path.join(PROJECT_ROOT, expanded))


def _resolve_publish_targets(publishing_config):
    raw_targets = publishing_config.get("default_targets", ["bilibili"])
    if isinstance(raw_targets, str):
        raw_targets = [raw_targets]

    targets = []
    seen = set()
    for raw_target in raw_targets or []:
        if not isinstance(raw_target, str):
            continue
        target = raw_target.strip()
        if not target or target in seen:
            continue
        targets.append(target)
        seen.add(target)

    return targets or ["bilibili"]


def _load_publishing_automation_config(config):
    inline_config = config.get("publishing_automation")
    if isinstance(inline_config, dict):
        return inline_config

    publishing_config = config.get("publishing", {})
    automation_path = publishing_config.get(
        "automation_config_file",
        "config/publishing_automation.yaml",
    )
    resolved_path = _resolve_project_path(automation_path)
    if not resolved_path or not os.path.exists(resolved_path):
        return {}

    with open(resolved_path, "r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    if not isinstance(loaded, dict):
        raise ValueError("publishing automation config must be a mapping")
    return loaded


def _get_automation_platform_config(platform, adapter_config, automation_config):
    merged = {}
    file_config = automation_config.get(platform, {})
    inline_config = adapter_config.get("automation", {})

    if isinstance(file_config, dict):
        merged.update(file_config)
    if isinstance(inline_config, dict):
        merged.update(inline_config)

    publish_url = str(merged.get("publish_url", "")).strip()
    locators = merged.get("locators", {})

    if not publish_url:
        raise ValueError(f"publishing automation config missing {platform}.publish_url")
    if not isinstance(locators, dict) or not locators:
        raise ValueError(f"publishing automation config missing {platform}.locators")

    merged["publish_url"] = publish_url
    merged["locators"] = locators
    return merged


def _build_publish_adapter(platform, config, publishing_config, automation_config):
    adapters_config = publishing_config.get("adapters", {})
    adapter_config = adapters_config.get(platform, {})

    if adapter_config.get("enabled") is False:
        raise ValueError(f"publishing target '{platform}' is disabled")

    if platform == "bilibili":
        mode = adapter_config.get("mode", "api")
        if mode != "api":
            raise ValueError("bilibili publishing only supports api mode")
        return BilibiliAdapter()

    if platform == "douyin":
        mode = adapter_config.get("mode", "api")
        if mode == "api":
            access_token = str(adapter_config.get("access_token", "")).strip()
            if not access_token:
                raise ValueError("publishing.adapters.douyin.access_token is required")
            return DouyinAdapter(client=DouyinApiClient(access_token=access_token))
        if mode == "automation":
            selectors = _get_automation_platform_config(
                platform, adapter_config, automation_config
            )
            return DouyinAutomationAdapter(config=config, selectors=selectors)
        if mode == "sau":
            selectors = _get_automation_platform_config(
                platform, adapter_config, automation_config
            )
            return DouyinSAUAdapter(config=config, selectors=selectors)
        raise ValueError("douyin publishing mode must be api, automation, or sau")

    if platform == "kuaishou":
        mode = adapter_config.get("mode", "api")
        if mode == "api":
            access_token = str(adapter_config.get("access_token", "")).strip()
            app_id = str(adapter_config.get("app_id", "")).strip()
            if not access_token:
                raise ValueError("publishing.adapters.kuaishou.access_token is required")
            if not app_id:
                raise ValueError("publishing.adapters.kuaishou.app_id is required")
            return KuaishouAdapter(
                client=KuaishouApiClient(access_token=access_token, app_id=app_id)
            )
        if mode == "automation":
            selectors = _get_automation_platform_config(
                platform, adapter_config, automation_config
            )
            return KuaishouAutomationAdapter(config=config, selectors=selectors)
        if mode == "sau":
            selectors = _get_automation_platform_config(
                platform, adapter_config, automation_config
            )
            return KuaishouSAUAdapter(config=config, selectors=selectors)
        raise ValueError("kuaishou publishing mode must be api, automation, or sau")

    if platform == "wechat_channels":
        mode = adapter_config.get("mode", "automation")
        if mode == "sau":
            selectors = _get_automation_platform_config(
                platform, adapter_config, automation_config
            )
            return WechatChannelsSAUAdapter(config=config, selectors=selectors)
        if mode != "automation":
            raise ValueError("wechat_channels publishing mode must be automation or sau")
        selectors = _get_automation_platform_config(
            platform, adapter_config, automation_config
        )
        return WechatChannelsAdapter(config=config, selectors=selectors)

    if platform == "xiaohongshu":
        mode = adapter_config.get("mode", "automation")
        if mode == "sau":
            selectors = _get_automation_platform_config(
                platform, adapter_config, automation_config
            )
            return XiaohongshuSAUAdapter(config=config, selectors=selectors)
        if mode != "automation":
            raise ValueError("xiaohongshu publishing mode must be automation or sau")
        selectors = _get_automation_platform_config(
            platform, adapter_config, automation_config
        )
        return XiaohongshuAdapter(config=config, selectors=selectors)

    raise ValueError(f"Unsupported publishing target: {platform}")


def get_bilibili_publish_presence(meta):
    from core.publishing.records import get_bilibili_publish_ids

    bvid, _ = get_bilibili_publish_ids(meta)
    return bool(bvid)


def _filter_pending_publish_targets(metadata, targets):
    publish_records = metadata.get("publish_records") or {}
    pending = []
    for target in targets:
        if target == "bilibili" and get_bilibili_publish_presence(metadata):
            continue
        record = publish_records.get(target) or {}
        if record.get("status") in {"published", "scheduled"}:
            continue
        pending.append(target)
    return pending


def run_multi_platform_publish(metadata, metadata_path, schedule_time=0, dry_run=False):
    try:
        publishing_config = CONFIG.get("publishing", {})
        configured_targets = _resolve_publish_targets(publishing_config)
        job_targets = _filter_pending_publish_targets(metadata, configured_targets)
        if not job_targets:
            log.info("✅ All configured publish targets are already completed; skipping.")
            return True
        automation_config = _load_publishing_automation_config(CONFIG)

        is_preview = os.environ.get('BIYOU_PREVIEW') == '1'
        if is_preview:
            # Preview mode: only run browser-automation platforms, skip API-only
            api_only = {t for t in job_targets if publishing_config.get("adapters", {}).get(t, {}).get("mode") == "api"}
            if api_only:
                log.info(f"⏭️  预览模式跳过 API 平台: {', '.join(api_only)}")
                job_targets = [t for t in job_targets if t not in api_only]
            if not job_targets:
                log.info("⏭️  预览模式下没有浏览器自动化平台可预览。")
                return True

        adapters = [
            _build_publish_adapter(
                platform=target,
                config=CONFIG,
                publishing_config=publishing_config,
                automation_config=automation_config,
            )
            for target in job_targets
        ]

        schedule_intent = ScheduleIntent(
            mode="publish_at" if schedule_time else "publish_now",
            publish_at=_schedule_time_to_iso(schedule_time),
        )

        tags = metadata.get("tags", [])
        if not isinstance(tags, list):
            tags = [str(tags)] if tags else []

        platform_payloads = build_platform_payloads(metadata, job_targets)

        job = PublishJob(
            platform_targets=job_targets,
            video_path=metadata.get("video_path", ""),
            title=metadata.get("title", ""),
            description=metadata.get("description", ""),
            cover_path=metadata.get("cover_path_16_9", ""),
            tags=tags,
            schedule_intent=schedule_intent,
            metadata_path=metadata_path,
            metadata=metadata,
            platform_payloads=platform_payloads,
        )

        registry = build_registry(*adapters)
        if dry_run:
            print("\n" + "="*60)
            log.info("⚡️ [Dry Run] 多平台发布预览")
            print("="*60)
            print(f"  📹 视频: {os.path.basename(job.video_path)}")
            print(f"  📝 标题: {job.title}")
            print(f"  🏷️  标签: {', '.join(job.tags[:8])}{'...' if len(job.tags) > 8 else ''}")
            print(f"  ⏰ 调度: {schedule_intent.mode}" + (f" ({schedule_intent.publish_at})" if schedule_intent.publish_at else ""))
            print(f"  🎯 目标平台: {', '.join(job.platform_targets)}")

            # Publish records status
            publish_records = metadata.get("publish_records") or {}
            if publish_records:
                done = [p for p, r in publish_records.items() if r.get("status") in ("published", "scheduled")]
                if done:
                    print(f"  ✅ 已发布: {', '.join(done)}")

            for platform in job.platform_targets:
                payload = platform_payloads.get(platform, {})
                adapter = registry.get(platform)
                caps = getattr(adapter, 'capabilities', None) if adapter else None
                print(f"\n  ── {platform} {'─'*(40 - len(platform))}")
                p_title = payload.get("title", job.title)
                print(f"    标题: {p_title}")
                p_desc = payload.get("description", job.description)
                print(f"    描述: {p_desc[:100]}{'...' if len(p_desc) > 100 else ''}")
                p_tags = payload.get("tags", job.tags)
                if p_tags:
                    print(f"    标签: {', '.join(str(t) for t in p_tags)}")
                # Bilibili-specific fields
                if platform == "bilibili":
                    cat = payload.get("category", "")
                    if cat:
                        print(f"    分区: {cat}")
                    mission = payload.get("mission_id", "")
                    topic_kw = payload.get("topic_keyword", "")
                    if mission:
                        print(f"    活动: #{mission} (关键词: {topic_kw})")
                # Collection (all platforms)
                collection = payload.get("suggested_collection", "") or payload.get("collection_name", "")
                if collection:
                    print(f"    合集: {collection}")
                cover = payload.get("cover_path", "")
                cover_exists = os.path.isfile(cover) if cover else False
                print(f"    封面: {os.path.basename(cover) if cover else '(无)'} {'✅' if cover_exists else '❌ 文件不存在' if cover else ''}")
                if caps:
                    print(f"    能力: cover={caps.supports_cover} tags={caps.supports_tags} schedule={caps.supports_native_schedule}")
            print("="*60 + "\n")
            return True

        manager = PublishManager(registry)
        updated_metadata = manager.execute(job)

        if metadata_path:
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(updated_metadata, f, ensure_ascii=False, indent=4)

        return True
    except Exception as e:
        log.error(f"❌ Multi-platform publish failed: {e}")
        return False


def ensure_browser_config(auto_confirm=False):
    """Ensure browser is configured in config.yaml"""
    if not CONFIG.get('browser'):
        from core.utils.cookie import select_browser
        from core.utils.config import update_config
        
        print("\n" + "="*50)
        print_t('init_setup_browser')
        print("="*50)
        print_t('select_browser_prompt')
        print_t('browser_yt')
        print_t('browser_gemini')
        print_t('browser_bili')
        
        browser_choice = select_browser(i18n.get('select_default_browser'), auto_confirm=auto_confirm)
        
        if browser_choice:
            if update_config('browser', browser_choice):
                print_t('browser_configured', browser_choice)
                CONFIG['browser'] = browser_choice
            else:
                print_t('config_save_fail')
                CONFIG['browser'] = browser_choice
        else:
            print_t('browser_none')
            CONFIG['browser'] = 'chrome'
    else:
        print_t('browser_using', CONFIG['browser'])


def step_download_video(args, executor):
    """Step 0: Download Video"""
    # Step 0-age: 视频年龄门禁 —— 超过 48 小时的旧视频直接跳过（env OVERRIDE_MAX_AGE 可覆盖）
    MAX_VIDEO_AGE_HOURS = int(os.environ.get("OVERRIDE_MAX_AGE", "48"))
    import subprocess, json
    try:
        age_check = subprocess.run(
            ["yt-dlp",
             "--cookies", os.path.expanduser("~/.hermes/biyou/yt_cookies.txt"),
             "--proxy", "http://127.0.0.1:7890",
             "--dump-json", "--no-download",
             # 2026-09-27: SABR 反爬 —— 不带 player_client 链会报
             # 「The page needs to be reloaded」，age_check 失败 → 年龄门禁被跳过。
             "--extractor-args", "youtube:player_client=default,web_embedded,web_safari,mweb",
             args.url[0] if isinstance(args.url, list) else args.url],
            capture_output=True, text=True, timeout=30
        )
        if age_check.returncode == 0:
            info = json.loads(age_check.stdout)
            upload_date = info.get("upload_date", "")
            if upload_date:
                from datetime import datetime, timezone
                uploaded = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                age_hours = (now - uploaded).total_seconds() / 3600
                if age_hours > MAX_VIDEO_AGE_HOURS:
                    print(f"⏭ 视频超过 {MAX_VIDEO_AGE_HOURS}h（实际 {age_hours:.1f}h），跳过处理")
                    print(f"   上传于: {upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]} | {info.get('title','')[:50]}")
                    return None, None
    except Exception as e:
        log.warning(f"⚠️ 视频年龄检查失败（放行）: {e}")

    if args.url:
        print("\n" + "="*40)
        print_t('step_0')
        print("="*40)
        
        # Step 0a: Fast Fetch
        print_t('step_0a')
        info_result = fetch_video_info(args.url)
        
        if not info_result:
            print_t('step_0_fail')
            return None, None
        
        print_t('step_0_success', info_result['title'])
        video_path_expected = info_result.get('video_path')

        # Browser-sniff metadata may arrive before yt-dlp writes any local thumbnail file.
        # Persist the source thumbnail early so the cover step always has a stable input.
        try:
            from core.modules.fetcher import download_original_cover_from_url

            if video_path_expected and info_result.get('thumbnail'):
                download_original_cover_from_url(
                    video_path_expected,
                    info_result.get('thumbnail'),
                )
        except Exception as e:
            log.warning(f"⚠️ 无法提前保存源封面: {e}")
        
        # Save preliminary history
        try:
             save_history_entry(info_result, CONFIG)
        except Exception as e:
             print_t('cleanup_fail', f"Early history save failed: {e}")

        # Step 0b: Check if Video Exists (Skip Download)
        video_dir = os.path.dirname(video_path_expected)
        # video_path_expected from yt-dlp might have extension based on predictions, 
        # but we want to check what actually exists (mp4, webm, mkv...)
        
        # Get base filename without extension. 
        # We can't trust os.path.splitext on video_path_expected blindly if it guessed wrong ext,
        # but fetch_video_info usually gives a Constructed path.
        # Let's rely on info_result['title'] to construct base path search, or better:
        # info_result should ideally give us a generic template or we search dir.
        
        # Best approach: Check the specific path predicted first
        candidates = [video_path_expected]
        
        # Also check other extensions for the same base name
        base_no_ext = os.path.splitext(video_path_expected)[0]
        for ext in ['.mp4', '.webm', '.mkv']:
            if ext != os.path.splitext(video_path_expected)[1]:
                 candidates.append(base_no_ext + ext)

        found_local = None
        for cand in candidates:
            if os.path.exists(cand) and os.path.getsize(cand) > 1024: # Check > 1KB to avoid empty files
                found_local = cand
                break
        
        if found_local:
             print_t('dl_skip_exists', os.path.basename(found_local))
             # --- Mandatory Full Verification on Resume ---
             log.info(f"🔄 正在对已存在的视频进行全量完整性校验: {os.path.basename(found_local)}")
             if verify_video_integrity(found_local):
                 print_t('dl_confirmed', os.path.basename(found_local))
                 return None, found_local
             else:
                 log.warning(f"⚠️ 已存在的视频文件损坏，准备重新下载: {os.path.basename(found_local)}")
                 try:
                     os.remove(found_local)
                     log.info(f"🗑️ 已清理损坏的文件。")
                 except Exception as e:
                     log.error(f"❌ 无法清理损坏的文件: {e}")
                 # Proceed to download
        
        # Step 0c: Start Download in Background
        print_t('workflow_bg_download')
        return executor.submit(download_video_stream, args.url, info=info_result), video_path_expected
        
    elif not args.skip_fetch:
        print_t('step_0_no_url')
        return None, None
    return None, None


def step_fetch_metadata(args, target_path=None):
    """Step 1: Fetch Metadata"""
    if not args.skip_fetch:
        print("\n" + "="*40)
        print_t('step_1')
        print("="*40)
        print("="*40)
        
        # 1. Get Video Info
        video_info = get_latest_video(target_path)
        if not video_info:
            log.error("No video found for processing.")
            return None

        # 2. Check for existing metadata (Resume Logic)
        video_path = video_info.get('video_path', '')
        if video_path:
            video_name = os.path.splitext(os.path.basename(video_path))[0]
            video_meta_file = os.path.join(os.path.dirname(video_path), f"{video_name}.json")
            history_meta_file = os.path.join(PROJECT_ROOT, 'history', f"{video_name}.json")

            if not os.path.exists(video_meta_file) and os.path.exists(history_meta_file):
                try:
                    with open(history_meta_file, 'r', encoding='utf-8') as hf:
                        history_meta = json.load(hf)
                    restored_meta = merge_metadata_records(history_meta, {})
                    if restored_meta.get('video_path') != video_path:
                        restored_meta['video_path'] = video_path
                    with open(video_meta_file, 'w', encoding='utf-8') as wf:
                        json.dump(restored_meta, wf, ensure_ascii=False, indent=4)
                    log.info("✅ 已从 history 恢复本地元数据文件。")
                except Exception as e:
                    log.warning(f"⚠️ 从 history 恢复本地元数据失败: {e}")

            if os.path.exists(video_meta_file):
                try:
                    with open(video_meta_file, 'r', encoding='utf-8') as f:
                        existing_meta = json.load(f)
                    if os.path.exists(history_meta_file):
                        try:
                            with open(history_meta_file, 'r', encoding='utf-8') as hf:
                                history_meta = json.load(hf)
                            merged_existing_meta = merge_metadata_records(history_meta, existing_meta)
                            if merged_existing_meta != existing_meta:
                                existing_meta = merged_existing_meta
                                with open(video_meta_file, 'w', encoding='utf-8') as wf:
                                    json.dump(existing_meta, wf, ensure_ascii=False, indent=4)
                                log.info("✅ 已从 history 同步补全本地元数据状态。")
                        except Exception as e:
                            log.warning(f"⚠️ 从 history 补全本地元数据失败: {e}")
                    from core.services.bili_activity import should_refresh_existing_mission
                    
                    print("\n" + "*"*50)
                    print_t('fetch_resume_found')
                    print(f"   已翻译标题: {existing_meta.get('title')}")
                    
                    missing_mission = not existing_meta.get('mission_id') and not args.skip_fetch
                    should_refresh_mission = should_refresh_existing_mission(existing_meta)
                    should_refresh_collection = should_refresh_existing_collection(existing_meta)
                    is_incomplete = (
                        missing_mission
                        or should_refresh_mission
                        or should_refresh_collection
                    )
                    
                    if missing_mission:
                        log.info(f"⚠️ 发现已有元数据但缺失 B站活动/话题 ID (mission_id)。")
                    if should_refresh_mission:
                        log.info("⚠️ 发现已有元数据中的活动/话题与当前内容不匹配，将重新分析。")
                    if should_refresh_collection:
                        log.info("⚠️ 发现已有元数据中的合集推荐已过时，将重新分析。")

                    # Prompt Logic
                    if args.dry_run:
                        # Dry-run never re-processes — just preview with existing materials
                        if is_incomplete:
                            log.info("⚡️ [Dry Run] 元数据可能过时，但 dry-run 模式下直接复用预览。")
                        log.info("⚡️ [Dry Run] Resuming from existing metadata...")
                        resume = 'y'
                    elif args.yes:
                        if is_incomplete:
                            log.info("⚡️ [Auto] Detected stale metadata, re-processing...")
                            resume = 'n'
                        else:
                            log.info("⚡️ [Auto] Resuming from existing metadata...")
                            resume = 'y'
                    else:
                        from core.utils.i18n import i18n
                        with i18n.suspend_background_logging():
                             print("\n" + "="*50)
                             if missing_mission:
                                 print("💡 建议：该文件缺失「活动/话题」信息，输入 'n' 将重新进行 AI 分析补全。")
                             if should_refresh_mission:
                                 print("💡 建议：该文件当前绑定的话题与内容不匹配，输入 'n' 将重新分析并改写话题。")
                             if should_refresh_collection:
                                 print("💡 建议：该文件当前推荐的合集可能已经过时，输入 'n' 将重新分析并刷新合集推荐。")
                             print(i18n.get('fetch_resume_prompt'))
                             print(i18n.get('fetch_resume_hint'))
                             print("="*50)
                             print(f"{i18n.get('prompt_input')} [Y/n]: ", end='', flush=True)
                             try:
                                 resume = input().strip().lower()
                             except EOFError:
                                 resume = 'y'

                    if resume != 'n':
                        try:
                            resolved_video_path = video_info.get('video_path') or existing_meta.get('video_path', '')
                            candidate_paths = []
                            if resolved_video_path:
                                candidate_paths.append(resolved_video_path)
                                base_no_ext = os.path.splitext(resolved_video_path)[0]
                                for ext in ['.mp4', '.mkv', '.webm', '.m4v']:
                                    cand = base_no_ext + ext
                                    if cand not in candidate_paths:
                                        candidate_paths.append(cand)

                            repaired_video_path = None
                            for cand in candidate_paths:
                                if cand and os.path.exists(cand) and os.path.getsize(cand) > 1024:
                                    repaired_video_path = os.path.abspath(cand)
                                    break

                            if repaired_video_path and existing_meta.get('video_path') != repaired_video_path:
                                existing_meta['video_path'] = repaired_video_path
                                with open(video_meta_file, 'w', encoding='utf-8') as wf:
                                    json.dump(existing_meta, wf, ensure_ascii=False, indent=4)
                                log.info(f"✅ 已修正复用元数据中的视频路径: {repaired_video_path}")
                        except Exception as e:
                            log.warning(f"⚠️ 复用元数据时修正 video_path 失败: {e}")

                        log.info("✅ 已复用元数据，跳过翻译步骤。")
                        return video_meta_file

                except Exception as e:
                    print(f"⚠️ 检查已有元数据时出错: {e}")

        # 3. Process (Translate -> Analyze -> Save) - REMOVED direction arg (always y2b)
        # 3. Process (Translate -> Analyze -> Save)
        return process_metadata(video_info)
    
    else:
        print_t('step_1_skip')
        # Try to find latest metadata
        # --- Strict Target Logic ---
        if target_path:
             video_name = os.path.splitext(os.path.basename(target_path))[0]
             target_meta = os.path.join(os.path.dirname(target_path), f"{video_name}.json")
             if os.path.exists(target_meta):
                 log.info(f"✅ 使用指定视频匹配的元数据: {os.path.basename(target_meta)}")
                 return target_meta
             else:
                 log.error(f"❌ 未找到指定视频的元数据，且跳过了抓取步骤: {target_path}")
                 return None
        
        # Original fallback (only for global skip-fetch mode without specific target)
        try:
             video_dir = CONFIG.get('video_source_dir')
             if os.path.exists(video_dir):
                 files = [f for f in os.listdir(video_dir) if f.endswith('.json') and not f.endswith('.info.json')]
                 files.sort(key=lambda x: os.path.getmtime(os.path.join(video_dir, x)), reverse=True)
                 if files:
                     metadata_path = os.path.join(video_dir, files[0])
                     print_t('workflow_using_meta', files[0])
                     return metadata_path
        except:
            pass
    return None


def step_process_cover(args, metadata_path, download_future):
    """Step 2: Process Cover (Workflow Orchestration)"""
    if not args.skip_cover and metadata_path:
        print("\n" + "="*40)
        print_t('step_2')
        print("="*40)
        # CoverWorkflow imported at top level
        
        try:
            from core.modules.fetcher import load_metadata, save_metadata
            from core.services.llm.factory import LLMFactory


            metadata = load_metadata(path=metadata_path)
            video_path = metadata.get('video_path')

            def _has_expected_aspect(path, expected_ratio, tolerance=0.03):
                if not path or not os.path.isfile(path):
                    return False
                try:
                    from PIL import Image
                    width, height = Image.open(path).size
                    if not width or not height:
                        return False
                    actual = width / height
                    ratio_map = {
                        "16:9": 16 / 9,
                        "9:16": 9 / 16,
                        "4:3": 4 / 3,
                        "3:4": 3 / 4,
                        "1:1": 1.0,
                    }
                    expected = ratio_map.get(expected_ratio)
                    if expected is None:
                        # Parse "W:H" generically
                        parts = expected_ratio.split(":")
                        if len(parts) == 2:
                            try:
                                expected = float(parts[0]) / float(parts[1])
                            except (ValueError, ZeroDivisionError):
                                return False
                        else:
                            return False
                    return abs(actual - expected) <= tolerance
                except Exception as exc:
                    log.warning(f"⚠️ 无法校验封面比例 {path}: {exc}")
                    return False
            
            # --- Workflow Logic: Find Source Cover ---
            source_cover = None
            video_dir = None
            video_base = None
            if video_path:
                video_dir = os.path.dirname(video_path)
                video_base = os.path.splitext(os.path.basename(video_path))[0]

                orig_cover_path = metadata.get('orig_cover_path')
                if orig_cover_path and os.path.isfile(orig_cover_path):
                    source_cover = orig_cover_path
                else:
                    preferred_sources = [
                        os.path.join(video_dir, f"{video_base}_orig_cover.jpg"),
                    ]
                    preferred_sources.extend(
                        os.path.join(video_dir, video_base + ext)
                        for ext in ['.jpg', '.jpeg', '.png', '.webp']
                    )
                    for candidate in preferred_sources:
                        if os.path.isfile(candidate):
                            source_cover = candidate
                            break
            
            if not source_cover:
                # Fallback to general search in source dir
                videos_dir = CONFIG.get('video_source_dir')
                if os.path.isdir(videos_dir) and video_dir and videos_dir == video_dir:
                     pass
                elif os.path.isdir(videos_dir):
                     for f in os.listdir(videos_dir):
                        if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')) and not f.startswith('.'):
                            if video_base in f: 
                                source_cover = os.path.join(videos_dir, f)
                                break
            
            if not source_cover:
                print_t('cover_no_source')
                return True 

            print_t('cover_using_source', source_cover)
            
            # --- Workflow Logic: Init Service ---
            # Pre-warm/Check LLM before prompting user
            print_t('cover_init_client')
            try:
                LLMFactory.get_service('cover')
                print_t('cover_client_ok')
            except Exception as e:
                log.error(f"LLM Setup failed: {e}")
                return False

            # --- Workflow Logic: Check Existing Files & Prompt ---
            output_dir = video_dir or os.path.dirname(source_cover)
            source_base = video_base or os.path.splitext(os.path.basename(source_cover))[0]
            
            target_16x9_path = os.path.join(output_dir, f"{source_base}_cover_16x9.jpg")
            target_4x3_path = os.path.join(output_dir, f"{source_base}_cover_4x3.jpg")
            
            exists_16x9 = os.path.exists(target_16x9_path)
            exists_4x3 = os.path.exists(target_4x3_path)
            valid_16x9 = _has_expected_aspect(target_16x9_path, "16:9")
            valid_4x3 = _has_expected_aspect(target_4x3_path, "4:3")

            # ── 内容归属校验（防"张冠李戴"复用错封面）──
            # 2026-08-20: 即梦失败时曾把别的视频封面临时写入本视频的 target 路径，
            # 因其宽高比是16:9而被误判 valid 并复用。追加"与源封面内容相关"判定：
            # 已存在封面若与 source_cover 相关性过低(疑似错图)，即使比例对也强制重生成。
            try:
                from core.services.image_translator import cover_similarity, COVER_SIMILARITY_THRESHOLD as _SIM_TH
                # 2026-09-17 新增上界校验：与源图"几乎一致"= 只是原图（没叠加中文标题）。
                # 旧逻辑只拦"相关性过低"(错图)，导致下载目录里的残留 *_cover_16x9.jpg
                # 被当作 valid 直接复用 → B站封面没有中文（用户 09-17 投诉）。
                _SIM_HIGH = float(CONFIG.get('cover_similarity_max', 0.90))
                if source_cover and os.path.isfile(source_cover):
                    if exists_16x9 and valid_16x9:
                        _sim = cover_similarity(source_cover, target_16x9_path)
                        if _sim >= 0 and _sim < _SIM_TH():
                            log.warning(f"⚠️ 现有 16:9 封面与源封面相关性过低(sim={_sim:.2f})，疑似错封面，强制重生成: {target_16x9_path}")
                            valid_16x9 = False
                        elif _sim >= _SIM_HIGH:
                            log.warning(f"⚠️ 现有 16:9 封面与源图几乎一致(sim={_sim:.2f})，未叠加标题，强制重生成: {target_16x9_path}")
                            valid_16x9 = False
                    if exists_4x3 and valid_4x3:
                        _sim4 = cover_similarity(source_cover, target_4x3_path)
                        if _sim4 >= 0 and _sim4 < _SIM_TH():
                            log.warning(f"⚠️ 现有 4:3 封面与源封面相关性过低(sim={_sim4:.2f})，疑似错封面，强制重生成: {target_4x3_path}")
                            valid_4x3 = False
                        elif _sim4 >= _SIM_HIGH:
                            log.warning(f"⚠️ 现有 4:3 封面与源图几乎一致(sim={_sim4:.2f})，未叠加标题，强制重生成: {target_4x3_path}")
                            valid_4x3 = False
            except Exception as _cov_e:
                log.debug(f"封面归属校验失败(继续按原逻辑): {_cov_e}")

            if exists_16x9 and not valid_16x9:
                log.warning(f"⚠️ 现有 16:9 封面比例异常，准备重生成: {target_16x9_path}")
            if exists_4x3 and not valid_4x3:
                log.warning(f"⚠️ 现有 4:3 封面比例异常，准备重生成: {target_4x3_path}")
            
            generate_16x9 = True
            generate_4x3 = True
            
            if exists_16x9 or exists_4x3:
                if args.yes or args.dry_run or CONFIG.get('auto_confirm', False):
                    log.info("⚡️ [Auto] Using existing covers where available.")
                    generate_16x9 = not (exists_16x9 and valid_16x9)
                    generate_4x3 = not (exists_4x3 and valid_4x3)
                else:
                    with i18n.suspend_background_logging():
                        print("\n📂 Found existing covers:")
                        if exists_16x9: print(f"   - {os.path.basename(target_16x9_path)}")
                        if exists_4x3: print(f"   - {os.path.basename(target_4x3_path)}")

                        try:
                            res = input(i18n.get('cover_use_existing_prompt')).strip().lower()
                        except EOFError:
                            res = 'y'
                        if res != 'n':
                            generate_16x9 = not (exists_16x9 and valid_16x9)
                            generate_4x3 = not (exists_4x3 and valid_4x3)
                            if not generate_16x9 and not generate_4x3:
                                print_t('cover_using_existing')
                        else:
                            print_t('cover_regenerating')

            publishing_targets = _resolve_publish_targets(CONFIG.get("publishing", {}))

            # --- Functional Call: Generate 16:9 & 4:3 (Unified Async) ---
            async def _batch_generate_covers():
                res_16x9 = None
                res_4x3 = None
                
                # 1. Generate 16:9
                if generate_16x9:
                    res_16x9 = await CoverWorkflow.run(source_cover, target_16x9_path, "16:9", metadata=metadata)
                elif exists_16x9:
                    res_16x9 = target_16x9_path
                    print("✅ Using 16:9")

                # 2. Generate 4:3
                if generate_4x3:
                    res_4x3 = await CoverWorkflow.run(source_cover, target_4x3_path, "4:3", metadata=metadata)
                elif exists_4x3:
                    res_4x3 = target_4x3_path
                    print_t('cover_using_4_3')
                    
                return res_16x9, res_4x3

            # Reuse the shared background loop used by text translation/analysis so
            # gemini_web does not rebuild the Gemini client on every stage change.
            from core.modules.translator import run_async_task
            cover_16x9, cover_4x3 = run_async_task(_batch_generate_covers())

            def _resolve_cover_status(result_path, target_path, requested_generation):
                if result_path == target_path and result_path:
                    return "generated" if requested_generation else "reused_existing"
                if result_path and os.path.exists(result_path):
                    return "generated"
                return "failed"

            cover_generation_status = {
                "16_9": _resolve_cover_status(cover_16x9, target_16x9_path, generate_16x9),
                "4_3": _resolve_cover_status(cover_4x3, target_4x3_path, generate_4x3),
            }

            # 2026-09-16 用户裁定（重申 08-15 规矩）：禁止"虚化+压字"的本地兜底封面。
            # 在线 AI 封面全部失败 → 保持 failed → 由下方 failed_cover_ratios 阻断上传，
            # 宁可晚发/不发，也不用假封面上传。需要临时恢复兜底可置 config 开关。
            if CONFIG.get('cover_allow_local_fallback', False) and any(s == "failed" for s in cover_generation_status.values()):
                def _local_cover(src, dst, ratio):
                    import subprocess as _sp
                    import shutil as _sh
                    if not src or not os.path.exists(src):
                        return None
                    # 优先用本地合成器：虚化背景 + 居中大字标题
                    # （源图常烤英文大字，本地擦不掉 → 虚化隐藏 + 大字标题观感最好）
                    try:
                        from core.utils.local_cover import bold_cover as _bold
                        _out = _bold(src, dst, ratio, (metadata.get("title") or "").strip())
                        if _out and os.path.exists(_out):
                            return _out
                    except Exception as _e:
                        log.warning(f"⚠️ 本地 bold 封面不可用({ratio}): {_e}")
                    try:
                        from core.utils.local_cover import compose as _compose
                        _out = _compose(src, dst, ratio, (metadata.get("title") or "").strip())
                        if _out and os.path.exists(_out):
                            return _out
                    except Exception as _e:
                        log.warning(f"⚠️ 本地封面合成器不可用({ratio}): {_e}")
                    # 退化：ffmpeg 裸裁切
                    ff = _sh.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
                    size = {"16:9": "1280:720", "4:3": "1200:900"}[ratio]
                    vf = f"scale={size}:force_original_aspect_ratio=increase,crop={size}"
                    cmd = [ff, "-y", "-loglevel", "error", "-i", src, "-vf", vf,
                           "-frames:v", "1", "-q:v", "3", dst]
                    try:
                        r = _sp.run(cmd, capture_output=True, text=True, timeout=90)
                        if r.returncode == 0 and os.path.exists(dst):
                            return dst
                        log.warning(f"⚠️ 本地封面裁切失败({ratio}): {r.stderr.strip()[-160:]}")
                    except Exception as e:
                        log.warning(f"⚠️ 本地封面裁切异常({ratio}): {e}")
                    return None

                for _ratio_key, _target, _aspect in (
                    ("16_9", target_16x9_path, "16:9"),
                    ("4_3", target_4x3_path, "4:3"),
                ):
                    if cover_generation_status.get(_ratio_key) != "failed":
                        continue
                    _made = _local_cover(source_cover, _target, _aspect)
                    if _made:
                        cover_generation_status[_ratio_key] = "local_fallback"
                        if _ratio_key == "16_9":
                            cover_16x9 = _made
                        else:
                            cover_4x3 = _made
                        log.info(f"🖼️ 在线封面全失败，已用本地原图生成 {_aspect} 封面: {_made}")

            metadata['cover_path_16_9'] = cover_16x9
            metadata['cover_path_4_3'] = cover_4x3
            metadata['cover_generation_status'] = cover_generation_status

            platform_variant_specs = build_platform_cover_variant_specs(
                metadata,
                [target for target in publishing_targets if target in PLATFORM_VARIANT_TARGETS],
            )

            async def _batch_generate_platform_variants():
                prompts = CONFIG.get('prompts', {})
                prompt_template = prompts.get('platform_cover_variant', "")
                results = {}
                for platform, spec in platform_variant_specs.items():
                    existing_path = spec["output_path"]
                    should_generate = True
                    if os.path.exists(existing_path) and _has_expected_aspect(existing_path, spec["aspect_ratio"]):
                        if args.yes or args.dry_run or CONFIG.get('auto_confirm', False):
                            should_generate = False
                    if should_generate:
                        variant_metadata = dict(metadata)
                        variant_metadata["title"] = spec.get("title") or metadata.get("title", "")
                        variant_metadata["platform_name"] = platform
                        variant_metadata["cover_style"] = spec.get("cover_style", "")
                        variant_metadata["cover_text"] = spec.get("cover_text", "")
                        result_path = await CoverWorkflow.run(
                            spec["source_path"],
                            spec["output_path"],
                            spec["aspect_ratio"],
                            metadata=variant_metadata,
                            prompt_template=prompt_template,
                        )
                    else:
                        result_path = existing_path
                    results[platform] = {
                        "path": result_path or "",
                        "source_base": "16_9",
                        "cover_text": spec.get("cover_text", ""),
                        "style": spec.get("cover_style", ""),
                        "status": _resolve_cover_status(
                            result_path,
                            spec["output_path"],
                            should_generate,
                        ),
                    }
                return results

            platform_cover_assets = {}
            if platform_variant_specs:
                platform_cover_assets = run_async_task(_batch_generate_platform_variants())

            log.info(
                "🧾 Cover generation result: 16:9=%s, 4:3=%s",
                cover_generation_status["16_9"],
                cover_generation_status["4_3"],
            )

            failed_cover_ratios = [
                ratio
                for ratio, status in cover_generation_status.items()
                if status == "failed"
            ]

            # 2026-09-26: 单规格失败不再阻断整条上传。
            # 背景（用户裁定 + 实测）：B站展示的主封面是 16:9，4:3 只在部分端出现。
            # 原来双规格"任一 failed 即阻断"，实测一天内阻断 15 次（09-26），
            # 而事后补充流程 regen_ai_covers.py 本来就会在 1~2 小时内把缺口补上。
            # 宁可先发（主封面可用）+ 事后补，也不要整条视频卡住不发。
            # 但 16:9 失败 = 主封面没有 → 仍按原逻辑阻断（除非兜底开关打开）。
            _blocking = [r for r in failed_cover_ratios if r == "16_9"] \
                if CONFIG.get("cover_block_only_primary_failed", True) else failed_cover_ratios
            if failed_cover_ratios and not _blocking:
                log.warning(
                    "⚠️ 4:3 封面未生成（不阻断上传，交由事后补充流程 regen_ai_covers 补做）: %s",
                    ", ".join(failed_cover_ratios),
                )

            # Explicit Browser Closure (User Request: Close after images done)
            try:
                from core.utils.browser_gemini import close_browser
                log.info("🔌 Closing browser after cover generation batch...")
                close_browser()
            except Exception as e:
                log.warning(f"⚠️ Failed to close browser: {e}")

            metadata['cover_assets'] = {
                "base": {
                    "original": source_cover,
                    "16_9": cover_16x9,
                    "4_3": cover_4x3,
                },
                "platforms": platform_cover_assets,
            }
            metadata['platform_payloads'] = build_platform_payloads(metadata, publishing_targets)
            save_metadata(metadata, path=metadata_path)
            save_history_entry(metadata, CONFIG)

            if _blocking:
                log.error(
                    "❌ 封面生成未完成，阻断上传。失败比例: %s",
                    ", ".join(_blocking),
                )
                return False
                
            # ── 2026-10-01 shadow: 记录 Jev 对「已放行封面」的判定，不改流程 ──
            # 借鉴 Nowledge Labs《给记忆系统装上反射弧》: 新判断先 shadow 上线，
            # 只记录结果、不影响主链路，攒够样本再定阈值和是否真正接入。
            _shadow_cover_gate(target_16x9_path, metadata.get('title') or '')
            print_t('cover_done')

        except Exception as e:
            print_t('workflow_cover_fail', e) 
            import traceback
            traceback.print_exc()
            if not args.dry_run:
                if download_future:
                    print_t('workflow_wait_dl')
                    download_future.result() 
                return False
    else:
        print_t('step_2_skip')
    return True


# 超大视频上限（2026-09-20）：直播归档 VOD 常达 3h+/7GB，下载要 30+ 小时，
# 既搬不完又长期占用 ego/网络/磁盘 → 前置拦截。
# 可变参数走 config（用户原则：不硬编码），读不到时用这里的兜底默认值。
MAX_VIDEO_DURATION_SEC = int(CONFIG.get("video_max_duration_minutes", 90)) * 60
MAX_VIDEO_SIZE_BYTES = int(CONFIG.get("video_max_size_gb", 3)) * (1024 ** 3)


def wait_for_download(download_future):
    """Step 0c: Wait for Download

    2026-09-20 直播归档补强：
      直播回放 VOD 可能极大（实测 3h10m / 6.8GB），30 分钟默认超时下不完，
      超时后底层 yt-dlp 进程不会自动终止 → 僵死挂起数小时并持续占用 ego/网络。
      处理：
        - 超时时**主动取消 future 并尽力终止下载进程**，避免僵死
        - 从元数据读时长，>1h 的视频自动放大超时（每 GB 约 10 分钟，下限 1800s）
        - 若预估体积过大导致不可能在合理时间内完成 → 直接判不可搬运，不硬等
    """
    # 下载超时（秒）：默认30分钟，可用环境变量 BIYOU_DL_TIMEOUT 覆盖（大视频慢慢下）
    dl_timeout = int(os.environ.get("BIYOU_DL_TIMEOUT", "1800"))

    # 直播归档按体积自适应放大超时（除非用户显式指定了 BIYOU_DL_TIMEOUT）
    if "BIYOU_DL_TIMEOUT" not in os.environ:
        try:
            import glob as _glob
            _cands = sorted(
                _glob.glob("${HOME}/Downloads/Videos/*.info.json"),
                key=os.path.getmtime, reverse=True,
            )[:1]
            for _c in _cands:
                _d = json.load(open(_c))
                _dur = _d.get("duration") or 0
                _size = _d.get("filesize_approx") or 0
                if _dur and _dur > 3600:
                    # 大视频：每 GB 给 10 分钟，最少 30 分钟，最多 6 小时
                    _est = max(1800, min(21600, int((_size or 0) / (1024**3) * 600)))
                    dl_timeout = max(dl_timeout, _est)
                    log.warning(
                        f"⏱️ 检测到大视频(duration={_dur}s, size≈{(_size or 0)/1024**3:.1f}GB)"
                        f" → 下载超时放宽到 {dl_timeout}s"
                    )
                break
        except Exception as _e:
            log.debug(f"自适应下载超时计算失败(用默认): {_e}")

    if download_future:
        print("\n" + "="*40)
        print_t('step_0c')
        print("="*40)
        try:
            video_path = download_future.result(timeout=dl_timeout)
            if not video_path:
                 print_t('workflow_dl_wait_fail')
                 return None
            print_t('dl_confirmed', os.path.basename(video_path))
            return video_path
        except concurrent.futures.TimeoutError:
            # 关键：超时后主动取消 + 终止遗留下载进程，否则僵死挂起数小时
            log.error(f"❌ 下载超时（{dl_timeout//60}分钟）—— 主动取消并清理下载进程")
            try:
                download_future.cancel()
            except Exception:
                pass
            try:
                # 通用兜底：终止 yt-dlp 下载进程（超时后不清理会僵死挂起数小时）
                # 注意：只按进程名匹配，不硬编码视频标题
                subprocess.run(["pkill", "-f", "yt-dlp"], capture_output=True, timeout=10)
            except Exception as _e:
                log.warning(f"⚠️ 终止下载进程失败: {_e}")
            print_t('workflow_dl_error', f"下载超时（{dl_timeout//60}分钟），请检查网络后重试")
            return None
        except Exception as e:
            print_t('workflow_dl_error', e)
            return None
    return None


def step_upload(args, schedule_time, metadata_path):
    """Step 3: Upload"""
    if not args.skip_upload:
        print("\n" + "="*40)
        print_t('step_3')
        print("="*40)
        
        # --- Asset Validation ---
        from core.modules.fetcher import load_metadata
        from core.modules.uploader import validate_upload_assets, read_interactive_line
        
        metadata = load_metadata(path=metadata_path)
        if not metadata:
            log.error("❌ Failed to load metadata for validation.")
            return False
            
        validation_targets = (
            _resolve_publish_targets(CONFIG.get("publishing", {}))
            if CONFIG.get("publishing", {}).get("enabled")
            else ["bilibili"]
        )
        is_valid, missing = validate_upload_assets(metadata, targets=validation_targets)
        
        if not is_valid:
            print("\n" + "!"*40)
            print_t('up_pre_check_fail')
            for item in missing:
                print_t('up_missing_item', item)
            print("!"*40 + "\n")
            
            if args.yes:
                log.critical(i18n.get('up_auto_confirm_skip'))
                return False
            else:
                confirm = read_interactive_line(f"{i18n.get('prompt_continue')} (y/N): ", default='n').lower()
                if confirm != 'y':
                    print_t('up_cancel')
                    return False
        else:
            print_t('up_pre_check_ok')

        if CONFIG.get("publishing", {}).get("enabled"):
            success = run_multi_platform_publish(
                metadata,
                metadata_path,
                schedule_time=schedule_time,
                dry_run=args.dry_run,
            )
        else:
            success = run_upload(schedule_time=schedule_time, dry_run=args.dry_run, auto_comment=False, metadata_path=metadata_path, auto_confirm=True, season=args.season, auto_season=args.auto_season, interactive=not args.yes)
        if not success:
            if not args.dry_run:
                return False
        
        # New Step: Auto Comment (only for fitness videos in commenter.py logic)
        if not args.dry_run:
            print("\n" + "="*40)
            print_t('step_4')
            print("="*40)
            from core.modules.uploader import get_bilibili_credentials
            cookies = get_bilibili_credentials()
            post_comment_flow(dry_run=args.dry_run, metadata_path=metadata_path, cookies=cookies)
    else:
        print_t('step_3_skip')
    return True


def step_cleanup(args, metadata_path):
    """Cleanup & History Archiving"""
    if not args.dry_run and not args.skip_upload:
        print("\n" + "="*40)
        print_t('cleanup_title')
        print("="*40)
        
        target_meta_file = metadata_path if metadata_path else METADATA_FILE
        
        if target_meta_file and os.path.exists(target_meta_file):
             try:
                 with open(target_meta_file, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                 
                 video_path = meta.get('video_path')
                 if video_path and get_bilibili_publish_presence(meta):
                     save_history_entry(meta, CONFIG)
                     base_name = os.path.splitext(os.path.basename(video_path))[0]
                     print_t('cleanup_archived', f".../history/{base_name}.json")
                     
                     os.remove(target_meta_file)
                     print_t('cleanup_removed', target_meta_file)
                 else:
                     print_t('cleanup_skip')
             except Exception as e:
                 print_t('cleanup_fail', e)


_UPLOAD_SERIAL_LOCK = threading.Lock()


def step_post_process(args, schedule_time, metadata_path, current_url, signal_recorder=None):
    """
    Wrapper for post-processing steps (upload, comment, cleanup) to be run in a thread.
    Returns the URL on success, None on failure.

    In multi-URL mode, several post-process threads can run concurrently while
    earlier URLs are still uploading. The automation platforms share a single
    browser session over CDP, so two URLs racing on Douyin/Kuaishou/WeChat/Xiaohongshu
    at the same time causes page navigations to kill each other mid-flow
    ("Page.close: Connection closed", "Unable to locate fill target", etc.).
    Serialize the upload + cleanup phase so only one URL touches the shared
    browser at a time; download/cover generation for the next URL can still
    overlap because they happen before this function is called.
    """
    try:
        if signal_recorder:
            signal_recorder.set_paths(metadata_path=metadata_path)
            signal_recorder.stage_started("upload", details={"mode": "background"})
        log.info(f"🚀 [BG] 开始上传: {current_url}")
        with _UPLOAD_SERIAL_LOCK:
            if not step_upload(args, schedule_time, metadata_path):
                log.error(f"❌ [BG] 上传失败: {current_url}")
                if signal_recorder:
                    signal_recorder.run_failed(
                        stage="upload",
                        error_code="UPLOAD_FAILED",
                        message=f"Upload failed for {current_url}",
                        details={"mode": "background", "metadata_path": metadata_path},
                    )
                return None

            # Post-upload subtitle sync
            if signal_recorder:
                signal_recorder.stage_succeeded("upload", details={"mode": "background"})
            if args.post_sync_subtitles:
                log.info(f"🔄 [BG] 开始上传后字幕同步: {current_url}")
                sync_subtitles(dry_run=args.dry_run)

            # Cleanup
            step_cleanup(args, metadata_path)
        log.info(f"✅ [BG] 完成处理: {current_url}")
        if signal_recorder:
            signal_recorder.run_succeeded(
                details={"mode": "background", "metadata_path": metadata_path}
            )
        return current_url
    except Exception as e:
        import traceback
        log.error(f"❌ [BG] 后处理线程异常: {current_url}")
        log.error(traceback.format_exc())
        if signal_recorder:
            signal_recorder.run_failed(
                stage="workflow",
                error_code="POST_PROCESS_EXCEPTION",
                message=str(e),
                details={"mode": "background", "metadata_path": metadata_path},
            )
        return None


from typing import Optional, Union

def _validate_gemini_browser_session() -> tuple[bool, Optional[str]]:
    """
    Fallback validation path for Gemini:
    when cookie HTTP validation is blocked by SSL/risk checks, verify that the
    configured browser profile still has a usable Gemini session.
    Returns: (is_valid, detail)
    """
    from core.utils import browser_gemini

    async def _probe():
        async with browser_gemini.async_playwright() as p:
            browser = await browser_gemini._connect_configured_browser(p, startup_delay=2)
            context, page = await browser_gemini._get_or_create_gemini_page(
                browser,
                preferred_authuser=browser_gemini._account_index,
            )
            _ = context  # keep reference explicit; page belongs to this context.
            usable = await browser_gemini._ensure_usable_account(page)
            if not usable:
                return False, "browser session unavailable"
            state = await browser_gemini._probe_single_account(page, browser_gemini._account_index)
            status = state.get("status")
            detail = f"authuser={browser_gemini._account_index}, status={status}"
            if state.get("cooldown_until"):
                detail += f", reset={state.get('cooldown_until')}"
            return browser_gemini._account_is_available(state), detail

    try:
        return asyncio.run(_probe())
    except Exception as e:
        return False, str(e)


from typing import Optional, Union

def _validate_bilibili_browser_session() -> tuple[bool, Optional[str]]:
    """
    Best-effort fallback for environments where cookie extraction is temporarily
    unavailable. The default implementation is conservative; tests can patch
    this hook with a stronger browser-session probe.
    """
    return False, "browser session unavailable"


def pre_flight_checks(auto_confirm=False):
    """
    Check all critical services before starting workflow.
    Returns: bool (True if passed, False if failed/cancelled)
    """
    from core.utils.cookie import (
        get_bilibili_cookies, validate_bilibili_cookies,
        get_gemini_cookies, validate_gemini_cookies
    )
    from core.utils.config import CONFIG
    
    max_attempts = 5
    attempt = 0
    browser = CONFIG.get('browser', 'chrome')
    prompted_bilibili_login = False
    prompted_gemini_login = False

    while True:
        attempt += 1
        print("\n" + "="*50)
        print_t('workflow_preflight')
        print(f"Attempt: {attempt}/{max_attempts}")
        print("="*50)

        # Check 1: Bilibili Auth
        print_t('workflow_bili_check', browser)
        bili_cookies = get_bilibili_cookies(browser)
        if bili_cookies:
            is_valid, err, user = validate_bilibili_cookies(cookies_list=bili_cookies)
            if is_valid:
                print_t('workflow_bili_ok', user['uname'])
            else:
                fallback_ok, fallback_detail = _validate_bilibili_browser_session()
                if fallback_ok:
                    log.warning(
                        "⚠️ Bilibili HTTP cookie validation failed (%s), "
                        "but browser session probe succeeded: %s",
                        err,
                        fallback_detail,
                    )
                    print("      ⚠️ Bilibili HTTP 校验失败，但浏览器会话可用，继续执行。")
                    print(f"      ✅ Browser session: \033[36m{fallback_detail}\033[0m")
                else:
                    log.error(i18n.get('workflow_bili_fail', err))
                    print(f"      {i18n.get('workflow_bili_hint', browser)}")
                    if attempt >= max_attempts:
                        return False
                    if auto_confirm:
                        return False
                    if not prompted_bilibili_login:
                        print("      ❌ Bilibili Cookie Invalid. Opening browser for login...")
                        from core.utils.cookie import open_browser_for_login
                        open_browser_for_login("https://www.bilibili.com", browser)
                        prompted_bilibili_login = True
                        input(f"      {i18n.get('prompt_retry')}...")
                        continue
                    input(f"      {i18n.get('prompt_retry')}...")
                    continue
        else:
            # Browser cookie extraction failed (likely ABE encryption on macOS 15+).
            # Try fallback to cached cookies.json before giving up.
            cached_ok = False
            fallback_ok = False
            fallback_detail = None
            try:
                from core.services.bili_api import get_bilibili_credentials
                cached_creds = get_bilibili_credentials()
                if cached_creds:
                    is_valid, err, user = validate_bilibili_cookies(cookies_list=cached_creds)
                    if is_valid and user:
                        print_t('workflow_bili_ok', user['uname'])
                        print("      ℹ️  从缓存文件加载的凭证 (cookies.json)")
                        bili_cookies = cached_creds
                        cached_ok = True
            except Exception:
                pass

            if not cached_ok:
                fallback_ok, fallback_detail = _validate_bilibili_browser_session()
                if fallback_ok:
                    print("      ⚠️ Bilibili Cookie 缺失，但浏览器会话可用，继续执行。")
                    print(f"      ✅ Browser session: \033[36m{fallback_detail}\033[0m")
                else:
                    print_t('workflow_bili_no_cookie')
                    print(f"      {i18n.get('workflow_bili_hint', browser)}")
                    if attempt >= max_attempts:
                        return False
                    if auto_confirm:
                        return False
                    if not prompted_bilibili_login:
                        print("      ❌ Bilibili Cookie Missing. Opening browser for login...")
                        from core.utils.cookie import open_browser_for_login
                        open_browser_for_login("https://www.bilibili.com", browser)
                        prompted_bilibili_login = True
                        input(f"      {i18n.get('prompt_retry')}...")
                        continue
                    input(f"      {i18n.get('prompt_retry')}...")
                    continue

        # Check 2: Gemini Auth
        print_t('workflow_gemini_check', browser)
        try:
            gemini_cookies = get_gemini_cookies(browser)
            is_valid, err, email = validate_gemini_cookies(gemini_cookies)
            if is_valid:
                print_t('workflow_gemini_ok')
                if email:
                    print(f"      ✅ 登录账号: \033[36m{email}\033[0m")
            else:
                fallback_ok, fallback_detail = _validate_gemini_browser_session()
                if fallback_ok:
                    log.warning(
                        "⚠️ Gemini HTTP cookie validation failed (%s), "
                        "but browser session probe succeeded: %s",
                        err,
                        fallback_detail,
                    )
                    print("      ⚠️ Gemini HTTP 校验失败，但浏览器会话可用，继续执行。")
                    print(f"      ✅ Browser session: \033[36m{fallback_detail}\033[0m")
                else:
                    log.error(i18n.get('workflow_gemini_fail', err))
                    print(f"      {i18n.get('workflow_gemini_hint', browser)}")
                    print(f"      {i18n.get('workflow_gemini_hint_2')}")
                    print(f"      {i18n.get('workflow_gemini_hint_3')}")
                    if fallback_detail:
                        print(f"      Browser probe failed: {fallback_detail}")
                    # 2026-08-09: fail-fast — cookie 无效立即退出，不空转重试。
                    # 旧逻辑开浏览器 + input() 等手动登录，非交互模式(-y)下
                    # 卡 60s timeout 并循环 5 次，浪费大量时间且无结果。
                    print("      ❌ Gemini Cookie Invalid — 预检失败，立即退出。")
                    print("      🔧 修复方法: 打开 ego 浏览器登录 Google（或运行")
                    print("         bash ~/.hermes/scripts/refresh_gemini_cookies.sh），再重跑。")
                    return False
        except Exception as e:
            print_t('workflow_gemini_err', e)
            # 2026-08-09: fail-fast 同上 — 异常直接退出，不重试
            print("      ❌ Gemini Cookie 检查异常 — 预检失败，立即退出。")
            print(f"      异常: {e}")
            return False

        print_t('workflow_preflight_ok')
        return True


def _normalize_cli_args(raw_args):
    """
    Backward-compatible CLI normalization:
    - `-n` without value => dry-run
    - `-n <int>` => `--num <int>` (legacy behavior)
    """
    normalized = []
    used_legacy_num = False
    converted_dry_run = False

    idx = 0
    while idx < len(raw_args):
        token = raw_args[idx]
        if token.startswith('-n='):
            value = token.split('=', 1)[1].strip()
            if value:
                try:
                    int(value)
                    normalized.extend(['--num', value])
                    used_legacy_num = True
                    idx += 1
                    continue
                except ValueError:
                    pass

        if token == '-n':
            next_token = raw_args[idx + 1] if idx + 1 < len(raw_args) else None
            if next_token and not next_token.startswith('-'):
                try:
                    int(next_token)
                    normalized.extend(['--num', next_token])
                    used_legacy_num = True
                    idx += 2
                    continue
                except ValueError:
                    pass
            normalized.append('-d')
            converted_dry_run = True
            idx += 1
            continue

        normalized.append(token)
        idx += 1

    return normalized, used_legacy_num, converted_dry_run


def main():
    # Patch argparse.Namespace to support deepcopy, which is needed for multi-threaded argument passing.
    def __deepcopy__(self, memo):
        new_namespace = argparse.Namespace()
        memo[id(self)] = new_namespace
        for k, v in self.__dict__.items():
            setattr(new_namespace, k, copy.deepcopy(v, memo))
        return new_namespace
    argparse.Namespace.__deepcopy__ = __deepcopy__

    parser = argparse.ArgumentParser(description=i18n.get('cli_desc'))
    
    parser.add_argument('url', nargs='*', help=i18n.get('cli_help_url'))
    parser.add_argument('-N', '--num', type=int, default=1, help=i18n.get('cli_help_num'))
    parser.add_argument('-d', '-n', '--dry-run', action='store_true', help='Dry run (simulate upload)')
    parser.add_argument('--preview', action='store_true', help='Preview mode: fill all forms in browser but do not click publish')
    parser.add_argument('-s', '--schedule', type=str, help=i18n.get('cli_help_schedule'))
    parser.add_argument('--delete-video', action='store_true', help=i18n.get('cli_help_delete'))
    parser.add_argument('--skip-fetch', action='store_true', help='Skip metadata fetching')
    parser.add_argument('--skip-translate', action='store_true', help='Skip translation') # Left config but usually implicit
    parser.add_argument('--skip-cover', action='store_true', help='Skip cover generation')
    parser.add_argument('--skip-upload', action='store_true', help='Skip upload')
    parser.add_argument('--post-sync-subtitles', action='store_true', help='Sync subtitles after upload (default off)')
    parser.add_argument('-y', '--yes', action='store_true', help='Auto confirm all prompts')
    
    # Subtitle Sync Task
    parser.add_argument('-t', '--sync-subtitles', action='store_true', help='Sync/upload missing subtitles for uploaded videos')
    parser.add_argument('--sync-dry-run', action='store_true', help='Dry run for subtitle sync')
    parser.add_argument('--kill', '-k', '-kill', action='store_true', help='Force kill all BiYou python processes')

    # Collection Management
    parser.add_argument('--season', type=str, help='Add video to a specific collection (Name or ID) upon upload')
    parser.add_argument('--auto-season', action='store_true', help='Automatically add to AI-suggested collection upon upload')
    
    normalized_args, used_legacy_num, converted_dry_run = _normalize_cli_args(sys.argv[1:])
    args = parser.parse_args(normalized_args)

    if used_legacy_num:
        log.warning("⚠️ 检测到旧参数 `-n <num>`，已自动映射为 `--num <num>`。建议后续使用 `--num` 或 `-N`。")
    if converted_dry_run:
        log.info("ℹ️ 检测到 `-n` 无参数，已按 dry-run 处理。")

    if args.kill:
        print_t('workflow_kill_start')
        try:
            # Kill by pattern
            # Using specific pattern first
            print("Attempting to kill processes matching 'BiYou/main.py'...")
            subprocess.run(['pkill', '-f', 'BiYou/main.py'], check=False)
            
            # Fallback cleanup
            subprocess.run(['pkill', '-f', 'main.py'], check=False)
            
            print("✅ Kill signals sent.")
        except Exception as e:
            print(f"Error killing processes: {e}")
        sys.exit(0)


    # --- URL Caching Logic ---
    CACHE_FILE = os.path.join(os.path.dirname(__file__), ".last_url")
    cached_urls = load_cached_urls(CACHE_FILE)
    last_urls_display = " ".join(cached_urls)
    invalid_input_urls = []
    had_multi_input = False

    if not args.url and not args.sync_subtitles:
        # Interactive Mode
        print("\n" + "="*50)
        print("🔗 请输入视频 URL (支持多链接，用空格或逗号分隔。回车使用未上传完成的链接):")
        if cached_urls:
            print(f"👉 未上传链接: \033[36m{last_urls_display}\033[0m")
        
        user_input = input("Input URL > ").strip()
        
        if not user_input and cached_urls:
            args.url = list(cached_urls)
            print(f"✅ 使用未上传链接: {last_urls_display}")
        elif user_input:
            import re
            args.url = [u.strip() for u in re.split(r'[\s,]+', user_input) if u.strip()]
        else:
            # No input, no cache
            args.url = []

    if args.url:
        had_multi_input = len(args.url) > 1
        args.url, invalid_input_urls = split_supported_video_urls(args.url)

        if invalid_input_urls:
            if had_multi_input:
                print_t('url_invalid_multi_skip', len(invalid_input_urls))
            else:
                print_t('url_invalid_single', invalid_input_urls[0])
                print_t('url_invalid_hint')

        if not args.url:
            save_cached_urls(CACHE_FILE, [])
            if had_multi_input:
                print_t('url_no_valid')
                _print_invalid_url_summary(invalid_input_urls, include_count=True)
            return

        save_cached_urls(CACHE_FILE, args.url)
    # -------------------------

    # If multiple URLs are provided, automatically enable 'yes' mode to skip confirmations.
    if args.url and (len(args.url) > 1 or had_multi_input) and not args.yes:
        log.info("ℹ️ 检测到多个链接输入，自动启用 `--yes` 模式以跳过确认步骤。")
        args.yes = True

    # 1. First ensure browser is configured
    ensure_browser_config(auto_confirm=args.yes)

    
    # 2. Run Pre-flight checks (Auth validation)
    if not pre_flight_checks(auto_confirm=args.yes or args.sync_subtitles):
        log.critical("❌ 预检失败，程序退出。")
        sys.exit(1)
    
    # Early Exit for Subtitle Sync Task
    if args.sync_subtitles:
        print_t('workflow_start')
        sync_subtitles(dry_run=args.sync_dry_run)
        return

    # --- 2026-09-30: 单视频并发锁 -------------------------------------------------
    # 同一支 YouTube 视频绝不允许两个 BiYou 进程同时搬运（否则 B 站会出重复稿件，
    # 见 2026-09-30 07:31/07:32 的 uVAkIOy2Qwk 事故）。锁在起线程之前就抢，
    # 抢不到的直接从本次待处理列表里剔除。
    if args.url and not args.dry_run:
        locked_urls = []
        skipped_locked = []
        for _u in args.url:
            _lock_dir, _lock_ok, _lock_info = acquire_video_lock(_u)
            if _lock_ok:
                locked_urls.append(_u)
            else:
                skipped_locked.append(_u)
                log.warning(
                    f"⏭ 并发锁命中：另一 BiYou 进程正在处理同一支视频，跳过以免 B 站重复投稿: {_u} ({_lock_info})"
                )
                print(f"⏭ 跳过 — 另一个 BiYou 进程正在搬运同一支视频（防重复投稿）: {_u}")
        if not locked_urls:
            print_t('workflow_start')
            print("⏭ 所有链接都已被其他 BiYou 进程占用，本次直接退出（不会产生重复稿件）。")
            return
        args.url = locked_urls
        if skipped_locked:
            log.warning(f"⚠️ 本次因并发锁跳过 {len(skipped_locked)} 支视频，另有 {len(locked_urls)} 支正常处理。")
    # -----------------------------------------------------------------------------

    print_t('workflow_start')
    if args.dry_run:
        print_t('dry_run_enabled')

    if getattr(args, 'preview', False):
        os.environ['BIYOU_PREVIEW'] = '1'
        log.info("⏸️  预览模式已启用：将在浏览器中填写表单但不点击发布")

    # Parse schedule time
    schedule_time = 0
    if args.schedule:
        schedule_time = parse_schedule_time(args.schedule)
        if schedule_time == 0:
            print_t('workflow_invalid_schedule')
            return
        if not args.dry_run:
            min_time = datetime.now() + timedelta(hours=4)
            if schedule_time < min_time.timestamp():
                print_t('workflow_schedule_future_error', min_time.strftime('%Y-%m-%d %H:%M'))
                return

    # Executor for background download & upload
    # We need more workers for concurrent uploads
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)
    
    urls_to_process = list(args.url) if args.url else [None]
    total_urls = len([u for u in urls_to_process if u])
    is_multi_url_mode = total_urls > 1
    pending_urls = set(u for u in urls_to_process if u)
    pending_urls_lock = threading.Lock()

    # Signal Handler for Ctrl+C
    def cleanup_handler(signum, frame):
        print("\n" + "!"*50)
        log.critical("🛑 Received Stop Signal (Ctrl+C). Exiting immediately...")
        if is_multi_url_mode:
            _write_pending_cache(CACHE_FILE, pending_urls, pending_urls_lock)
        # Force shutdown executor without waiting
        executor.shutdown(wait=False, cancel_futures=True)
        release_video_locks()  # os._exit 不走 atexit，这里显式释放视频锁
        print("!"*50)
        os._exit(1) # Force exit to kill threads

    signal.signal(signal.SIGINT, cleanup_handler)
    signal.signal(signal.SIGTERM, cleanup_handler)

    if is_multi_url_mode:
        log.info(f"🚀 检测到 {total_urls} 个链接，启动流水线处理模式。")

    post_process_futures = []
    failed_urls = []

    for url_index, current_url in enumerate(urls_to_process):
        if current_url:
            print(f"\n" + "+" * 60)
            print(f"🚀 正在处理第 {url_index + 1}/{total_urls} 个链接: {current_url}")
            print("+" * 60 + "\n")
        
        # Create a deep copy of args for each thread to avoid race conditions
        thread_args = copy.deepcopy(args)
        thread_args.url = current_url
        signal_recorder = WorkflowSignalRecorder(
            url=current_url,
            dry_run=thread_args.dry_run,
            config=CONFIG,
        )
        signal_recorder.run_started(
            details={"multi_url_mode": is_multi_url_mode, "argv": sys.argv[1:]}
        )
        
        download_future = None
        phase_timings = {}
        workflow_success = False
        metadata_path = None
        video_path = None

        try:
            # Step -1: Pre-validation
            if thread_args.url:
                if not is_supported_video_url(thread_args.url):
                    log.error(f"❌ 非视频链接，已跳过: {thread_args.url}")
                    signal_recorder.run_failed(
                        stage="input",
                        error_code="INVALID_URL",
                        message=f"Unsupported video URL: {thread_args.url}",
                    )
                    failed_urls.append(current_url)
                    if is_multi_url_mode and current_url:
                        _write_pending_cache(CACHE_FILE, pending_urls, pending_urls_lock)
                    continue
            
            # Step -0.5: Dedup/resume check — skip if already published or flagged
            if thread_args.url:
                yt_id = parse_youtube_video_id(thread_args.url)
                if yt_id:
                    action, detail, dup_title = check_history_dedup(yt_id, CONFIG)
                    if action == 'skip':
                        log.info(f"⏭ Dedup skip: {yt_id} → BVID={detail}")
                        print(f"⏭ 已跳过 — 该视频已在历史归档中发布到B站 (BVID: {detail})")
                        if is_multi_url_mode and current_url:
                            with pending_urls_lock:
                                pending_urls.discard(current_url)
                            _write_pending_cache(CACHE_FILE, pending_urls, pending_urls_lock)
                        continue
                    elif action == 'flag_skip':
                        log.info(f"⏭ Flag skip: {yt_id} → {detail}")
                        print(f"⏭ 已跳过 — {dup_title or yt_id}（标记原因: {detail}）")
                        if is_multi_url_mode and current_url:
                            with pending_urls_lock:
                                pending_urls.discard(current_url)
                            _write_pending_cache(CACHE_FILE, pending_urls, pending_urls_lock)
                        continue
                    elif action == 'intent_pending':
                        log.warning(f"⏸ Effect intent pending: {yt_id} → {detail} — 上次运行可能崩溃在外部副作用中途，禁止盲目重传")
                        print(f"⏸ 检测到未结算的副作用 intent ({detail}) — 上次运行可能崩溃在提交/评论中途，已阻止重跑避免重复视频/重复评论。")
                        print(f"   处理方式：先到B站核对。若已发布 → 把 BVID 补进 history JSON（或把 effects 里对应记录改为 settled）；若未发布 → 把 effects 里 pending 记录删除或改为 failed 后重跑。")
                        if is_multi_url_mode and current_url:
                            with pending_urls_lock:
                                pending_urls.discard(current_url)
                            _write_pending_cache(CACHE_FILE, pending_urls, pending_urls_lock)
                        continue
                    # action == '' means not published or in-progress → let through
            
            # Step 0: Download
            step_t0 = time.perf_counter()
            signal_recorder.stage_started("download_prepare")
            download_future, video_path_target = step_download_video(thread_args, executor)
            phase_timings['download_prepare'] = time.perf_counter() - step_t0
            signal_recorder.stage_succeeded(
                "download_prepare",
                details={"video_path_target": video_path_target},
            )
            
            if thread_args.url and not video_path_target:
                 log.critical("❌ 获取视频信息或定位文件失败，跳过该链接。")
                 signal_recorder.run_failed(
                     stage="download_prepare",
                     error_code="VIDEO_PREPARE_FAILED",
                     message="Could not fetch video info or locate local target path",
                 )
                 failed_urls.append(current_url)
                 if is_multi_url_mode and current_url:
                     _write_pending_cache(CACHE_FILE, pending_urls, pending_urls_lock)
                 continue

            # 超大视频前置拦截（2026-09-20）：直播归档 VOD 可能是 3h+/7GB 级别，
            # 下载要 30+ 小时，既搬不完又长期占用 ego/网络/磁盘。
            # 与其下载 30 分钟后超时失败（还留下僵死进程），不如一开始就判掉。
            try:
                import glob as _g2
                _mc = sorted(_g2.glob("${HOME}/Downloads/Videos/*.info.json"),
                             key=os.path.getmtime, reverse=True)[:1]
                for _m in _mc:
                    _md = json.load(open(_m))
                    _dur = _md.get("duration") or 0
                    _sz = _md.get("filesize_approx") or 0
                    _too_long = _dur and _dur > MAX_VIDEO_DURATION_SEC
                    _too_big = _sz and _sz > MAX_VIDEO_SIZE_BYTES
                    if _too_long or _too_big:
                        _reason = (
                            f"视频过大不可搬运: 时长{_dur//60}分钟, "
                            f"预估{(_sz or 0)/1024**3:.1f}GB"
                            f"（上限 {MAX_VIDEO_DURATION_SEC//60}分钟 / "
                            f"{MAX_VIDEO_SIZE_BYTES/1024**3:.0f}GB）"
                        )
                        log.warning(f"⏭ {_reason} → 跳过 {current_url}")
                        print(f"⏭ {_reason}")
                        signal_recorder.run_failed(
                            stage="download_prepare",
                            error_code="VIDEO_TOO_LARGE",
                            message=_reason,
                        )
                        failed_urls.append(current_url)
                        break
            except Exception as _e:
                log.debug(f"超大视频前置检查跳过: {_e}")

            # Step 1: Metadata
            step_t0 = time.perf_counter()
            signal_recorder.stage_started("metadata")
            metadata_path = step_fetch_metadata(thread_args, target_path=video_path_target)
            phase_timings['metadata'] = time.perf_counter() - step_t0
            if metadata_path:
                signal_recorder.set_paths(metadata_path=metadata_path)
                signal_recorder.stage_succeeded(
                    "metadata",
                    details={"metadata_path": metadata_path},
                )

            if not metadata_path:
                log.error(f"❌ 未能处理元数据: {current_url}")
                signal_recorder.run_failed(
                    stage="metadata",
                    error_code="METADATA_PROCESS_FAILED",
                    message=f"Metadata processing failed for {current_url}",
                )
                failed_urls.append(current_url)
                if is_multi_url_mode and current_url:
                    _write_pending_cache(CACHE_FILE, pending_urls, pending_urls_lock)
                continue

            if os.path.exists(metadata_path):
                 try:
                     with open(metadata_path, 'r', encoding='utf-8') as f:
                         save_history_entry(json.load(f), CONFIG)
                 except: pass

            # Step 2: Cover
            step_t0 = time.perf_counter()
            signal_recorder.stage_started("cover")
            if not step_process_cover(thread_args, metadata_path, download_future):
                signal_recorder.run_failed(
                    stage="cover",
                    error_code="COVER_PROCESS_FAILED",
                    message=f"Cover processing failed for {current_url}",
                    details={"metadata_path": metadata_path},
                )
                failed_urls.append(current_url)
                if is_multi_url_mode and current_url:
                    _write_pending_cache(CACHE_FILE, pending_urls, pending_urls_lock)
                continue
            phase_timings['cover'] = time.perf_counter() - step_t0
            signal_recorder.stage_succeeded(
                "cover",
                details={"metadata_path": metadata_path},
            )

            # Wait for download to complete before proceeding
            if download_future:
                step_t0 = time.perf_counter()
                signal_recorder.stage_started("download_wait")
                video_path = wait_for_download(download_future)
                if not video_path:
                    signal_recorder.run_failed(
                        stage="download_wait",
                        error_code="DOWNLOAD_FINALIZE_FAILED",
                        message=f"Download completion failed for {current_url}",
                        details={"metadata_path": metadata_path},
                    )
                    failed_urls.append(current_url)
                    if is_multi_url_mode and current_url:
                        _write_pending_cache(CACHE_FILE, pending_urls, pending_urls_lock)
                    continue
                phase_timings['download_wait'] = time.perf_counter() - step_t0
                signal_recorder.set_paths(video_path=video_path)
                signal_recorder.stage_succeeded(
                    "download_wait",
                    details={"video_path": video_path},
                )

                # 下载完成后，必须把元数据里的 video_path 回写成真实落地文件。
                # 否则 yt-dlp 早期预测的 .webm/.mkv 路径会残留到上传阶段，
                # 导致上传预检去找错文件，即使真实 mp4 已经存在。
                try:
                    from core.modules.fetcher import load_metadata, save_metadata
                    metadata_after_download = load_metadata(path=metadata_path)
                    if metadata_after_download:
                        resolved_video_path = os.path.abspath(video_path)
                        if metadata_after_download.get('video_path') != resolved_video_path:
                            metadata_after_download['video_path'] = resolved_video_path
                            save_metadata(metadata_after_download, path=metadata_path)
                            log.info(f"✅ 下载完成后已回写真实视频路径: {resolved_video_path}")
                except Exception as e:
                    log.warning(f"⚠️ 下载完成后回写 video_path 失败: {e}")

            # --- Pipeline Logic ---
            if is_multi_url_mode:
                # In multi-url mode, submit post-processing to background and continue
                log.info(f"✅ 预处理完成: {current_url}。已提交到后台上传队列。")
                future = executor.submit(
                    step_post_process,
                    thread_args,
                    schedule_time,
                    metadata_path,
                    current_url,
                    signal_recorder,
                )
                if current_url:
                    def _on_post_process_done(done_future, completed_url=current_url):
                        try:
                            result_url = done_future.result()
                        except Exception:
                            result_url = None
                        if result_url:
                            with pending_urls_lock:
                                pending_urls.discard(result_url)
                            _write_pending_cache(CACHE_FILE, pending_urls, pending_urls_lock)
                    future.add_done_callback(_on_post_process_done)
                post_process_futures.append(future)
            else:
                # In single-url mode, process everything sequentially
                signal_recorder.stage_started("upload", details={"mode": "foreground"})
                if not step_upload(thread_args, schedule_time, metadata_path):
                    signal_recorder.run_failed(
                        stage="upload",
                        error_code="UPLOAD_FAILED",
                        message=f"Upload failed for {current_url}",
                        details={"mode": "foreground", "metadata_path": metadata_path},
                    )
                    failed_urls.append(current_url)
                    continue
                signal_recorder.stage_succeeded("upload", details={"mode": "foreground"})
                
                if thread_args.post_sync_subtitles:
                    sync_subtitles(dry_run=thread_args.dry_run)
                
                step_cleanup(thread_args, metadata_path)
                workflow_success = True
                signal_recorder.run_succeeded(
                    details={
                        "mode": "foreground",
                        "metadata_path": metadata_path,
                        "video_path": video_path,
                    }
                )

        except Exception as e:
            log.error(f"Workflow Critical Error for {current_url}: {e}")
            signal_recorder.run_failed(
                stage="workflow",
                error_code="WORKFLOW_EXCEPTION",
                message=str(e),
                details={"exception_type": type(e).__name__},
            )
            failed_urls.append(current_url)
            if is_multi_url_mode and current_url:
                _write_pending_cache(CACHE_FILE, pending_urls, pending_urls_lock)

        finally:
            if not is_multi_url_mode:
                if phase_timings:
                    print("\n" + "-"*50)
                    print("📊 阶段耗时摘要")
                    for phase, seconds in phase_timings.items():
                        print(f"   - {phase}: {seconds:.2f}s")
                
                if workflow_success:
                    print_t('workflow_complete')
                    save_cached_urls(CACHE_FILE, []) # Clear cache on success
                else:
                    print(f"⚠️ 处理未完成或被中断: {current_url if current_url else '本地任务'}")
                    if current_url: save_cached_urls(CACHE_FILE, [current_url])

    # --- Finalization for Multi-URL Mode ---
    if is_multi_url_mode:
        print("\n" + "="*60)
        log.info(f"✅ 所有 {total_urls} 个链接的预处理已完成。等待所有后台上传任务结束...")
        print("="*60)

        successful_urls = []
        # Wait for all post-processing tasks to complete
        for future in concurrent.futures.as_completed(post_process_futures):
            result_url = future.result()
            if result_url:
                successful_urls.append(result_url)

        # Determine final pending/failed URLs from the live pending set.
        with pending_urls_lock:
            final_failed_urls = sorted(pending_urls)

        print("\n" + "="*60)
        print("🎉 所有任务处理完毕")
        print("="*60)
        print(f"   - 成功: {len(successful_urls)}")
        print(f"   - 失败: {len(final_failed_urls)}")
        if invalid_input_urls:
            print(f"   - 跳过(非视频链接): {len(invalid_input_urls)}")
        if final_failed_urls:
            print("   - 失败的链接:")
            for u in final_failed_urls:
                print(f"     - {u}")
        if invalid_input_urls:
            _print_invalid_url_summary(invalid_input_urls)
        
        # Update cache with only the URLs that failed
        save_cached_urls(CACHE_FILE, final_failed_urls)
        if not final_failed_urls:
            print_t('workflow_complete')

    # Smart Shutdown after all URLs processed
    if executor:
        executor.shutdown(wait=True)

    if had_multi_input and invalid_input_urls and not is_multi_url_mode:
        print("\n" + "="*60)
        _print_invalid_url_summary(invalid_input_urls, include_count=True)


if __name__ == "__main__":
    main()

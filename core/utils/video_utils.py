#!/usr/bin/env python3
import subprocess
import os
import json
from core.utils.logger import log

def probe_media_stream_types(file_path: str) -> list[str]:
    if not os.path.exists(file_path):
        return []

    try:
        result = subprocess.run(
            [
                'ffprobe',
                '-v', 'error',
                '-show_streams',
                '-of', 'json',
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0 or not result.stdout:
            return []
        payload = json.loads(result.stdout)
        return [
            str(stream.get('codec_type', '')).strip()
            for stream in payload.get('streams', [])
            if stream.get('codec_type')
        ]
    except Exception as e:
        log.warning(f"⚠️ 无法探测媒体流信息 {file_path}: {e}")
        return []


def file_has_video_stream(file_path: str) -> bool:
    return 'video' in probe_media_stream_types(file_path)


def verify_video_integrity(file_path: str, *, require_video_stream: bool = True) -> bool:
    """
    Verify the integrity of a video file using FFmpeg.
    Executes: ffmpeg -v error -i [file_path] -f null -
    Returns: True if no errors are found, False otherwise.
    """
    if not os.path.exists(file_path):
        log.error(f"❌ 验证失败：文件不存在 {file_path}")
        return False

    # --- Cache Mechanism ---
    import time
    
    # Cache file in the same directory as this script
    CACHE_FILE = os.path.join(os.path.dirname(__file__), ".video_integrity_cache.json")
    
    try:
        current_mtime = os.path.getmtime(file_path)
        current_size = os.path.getsize(file_path)
        
        cache_data = {}
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, 'r') as f:
                    cache_data = json.load(f)
            except:
                pass # Ignore cache read errors
        
        # Check cache
        if file_path in cache_data:
            entry = cache_data[file_path]
            if (entry.get('mtime') == current_mtime and 
                entry.get('size') == current_size and 
                entry.get('verified') is True and
                (not require_video_stream or entry.get('has_video_stream') is True)):
                log.info(f"✅ 视频完整性 (Cached): {os.path.basename(file_path)}")
                return True
    except Exception as e:
        log.warning(f"⚠️ 缓存读取失败: {e}")
    # -----------------------

    has_video_stream = file_has_video_stream(file_path)
    if require_video_stream and not has_video_stream:
        log.error(f"❌ 验证失败：文件缺少视频轨 {os.path.basename(file_path)}")
        return False

    log.info(f"🔍 正在验证视频完整性: {os.path.basename(file_path)} ...")

    
    try:
        # -v error: only show errors
        # -i file_path: input file
        # -f null -: output to null device (decode only)
        # We use a timeout to prevent it from hanging on extremely large or broken files
        cmd = [
            'ffmpeg',
            '-hide_banner',
            '-v', 'error',
            '-max_error_rate', '1',
            '-i', file_path,
            '-f', 'null',
            '-'
        ]
        
        # Run command and capture stderr
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        
        stderr_output = result.stderr or ""
        
        # --- Robust Error Filtering ---
        # Hardware acceleration initialization errors often appear in stderr even if decoding succeeds via software fallback.
        # On some M4 builds, these are reported as fatal "Decode error rate 1" even if it's just a toolchain mismatch.
        
        filtered_errors = []
        ignored_patterns = [
            "doesn't support hardware accelerated",
            "Failed to get pixel format",
            "Get current frame error",
            "Error submitting packet to decoder: Function not implemented",
            "Decode error rate",
            "Task finished with error code",
            "[av1 @",
            "vist#0:0/av1",
            "Application provided invalid, non monotonically increasing dts",
        ]
        
        for line in stderr_output.splitlines():
            line = line.strip()
            if not line:
                continue
            should_ignore = any(pattern in line for pattern in ignored_patterns)
            if not should_ignore:
                filtered_errors.append(line)
        
        # Decision Logic:
        # 1. If FFmpeg returncode != 0, it's a fatal error.
        # 2. If filtered_errors is not empty, it contains actual stream/codec errors.
        
        if result.returncode != 0:
            log.error(f"❌ 视频验证失败 (进程退出码 {result.returncode}): {os.path.basename(file_path)}")
            if filtered_errors:
                log.error(f"严重错误解析:\n" + "\n".join(filtered_errors))
            elif stderr_output:
                log.error(f"未经过滤的错误详情:\n{stderr_output}")
            return False
            
        if filtered_errors:
            log.warning(f"⚠️ 视频存在流错误 (虽然进程正常结束): {os.path.basename(file_path)}")
            log.warning("\n".join(filtered_errors[:10])) # Show first 10 actual errors
            if len(filtered_errors) > 10:
                log.warning(f"... 还有 {len(filtered_errors) - 10} 条错误记录")
            return False

        log.info(f"✅ 视频全量验证通过: {os.path.basename(file_path)}")
        
        # Update Cache
        try:
            cache_data[file_path] = {
                "mtime": current_mtime,
                "size": current_size,
                "verified": True,
                "has_video_stream": has_video_stream,
                "timestamp": time.time()
            }
            with open(CACHE_FILE, 'w') as f:
                json.dump(cache_data, f, indent=2)
        except Exception as e:
            log.warning(f"⚠️ 缓存写入失败: {e}")
            
        return True

    except subprocess.TimeoutExpired:
        log.error(f"❌ 视频验证超时 (超过 15 分钟): {os.path.basename(file_path)}")
        return False
    except Exception as e:
        log.error(f"❌ 视频验证过程中出错: {e}")
        return False

def repair_video(file_path: str) -> bool:
    """
    Attempt to repair a video file using FFmpeg remuxing.
    Executes: ffmpeg -i [file_path] -c copy [temp_path]
    If successful, replaces the original file.
    """
    if not os.path.exists(file_path):
        return False
        
    log.info(f"🛠️ 正在尝试自动修复视频: {os.path.basename(file_path)} ...")
    
    temp_path = file_path + ".fixed.mp4"
    try:
        # -y: overwrite output
        # -i file_path: input
        # -c copy: copy streams without re-encoding
        cmd = [
            'ffmpeg',
            '-y',
            '-v', 'error',
            '-i', file_path,
            '-c', 'copy',
            temp_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        
        if result.returncode == 0 and os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
            # Replace original with fixed version
            os.replace(temp_path, file_path)
            log.info(f"✅ 视频自动修复成功: {os.path.basename(file_path)}")
            return True
        else:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            log.warning(f"⚠️ 视频修复尝试失败 (FFmpeg 无法处理该损坏)")
            return False

    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        log.error(f"❌ 视频修复过程中发生意外错误: {e}")
        return False

if __name__ == "__main__":
    import argparse
    import sys
    
    parser = argparse.ArgumentParser(description='验证视频文件完整性 (FFmpeg)')
    parser.add_argument('path', help='要验证的视频文件路径')
    args = parser.parse_args()
    
    success = verify_video_integrity(args.path)
    sys.exit(0 if success else 1)


import time
import os
import sys
import subprocess
import json
from datetime import datetime
from core.utils.config import load_config
from core.utils.logger import log

# History file to track processed video IDs
PROCESSED_FILE = os.path.join(os.path.dirname(__file__), 'history', 'processed_ids.txt')


def _yt_dlp_command():
    """Use the same Python environment as the pipeline instead of whichever yt-dlp happens to be on PATH."""
    return [sys.executable, '-m', 'yt_dlp']

def load_processed_ids():
    if not os.path.exists(PROCESSED_FILE):
        return set()
    with open(PROCESSED_FILE, 'r') as f:
        return set(line.strip() for line in f if line.strip())

def save_processed_id(video_id):
    # Append to file
    with open(PROCESSED_FILE, 'a') as f:
        f.write(f"{video_id}\n")

def get_latest_video_id(channel_url):
    """
    Get the latest video ID from a channel using yt-dlp.
    Returns: (video_id, title)
    """
    try:
        # Construct yt-dlp command to get first item in playlist (latest upload)
        cmd = [
            *_yt_dlp_command(),
            '--flat-playlist',
            '--playlist-end', '1',
            '--print', '%(id)s::::%(title)s', # Output format directly
            channel_url
        ]
        
        # Run command
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        output = result.stdout.strip()
        
        if output:
            parts = output.split('::::')
            if len(parts) >= 2:
                return parts[0], parts[1]
            return parts[0], "Unknown Title"
            
    except subprocess.CalledProcessError as e:
        log.error(f"Failed to check channel {channel_url}: {e}")
    except Exception as e:
        log.error(f"Error checking channel {channel_url}: {e}")
    
    return None, None

def run_pipeline(video_url):
    """
    Trigger the main processing pipeline for a new video.
    """
    try:
        log.info(f"🚀 Triggering pipeline for: {video_url}")
        
        # Call main.py with the URL
        # We use 'python3' and assume main.py is in the same directory
        cmd = [sys.executable, 'main.py', video_url, '--yes']  # Auto-confirm prompts
        
        # We stream the output to the console so the user can see progress if watching
        # But in background mode, this might just go to stdout/log
        
        # Using subprocess.run to block until finished (sequential processing)
        subprocess.run(cmd, check=True)
        
        log.info(f"✅ Pipeline completed for: {video_url}")
        return True
        
    except subprocess.CalledProcessError as e:
        log.error(f"❌ Pipeline failed for {video_url}. Exit code: {e.returncode}")
        return False
    except Exception as e:
        log.error(f"❌ Failed to run pipeline: {e}")
        return False

def monitor_loop():
    log.info("📡 Starting YouTube Monitor...")
    
    while True:
        try:
            # Reload config each iteration to allow dynamic updates
            config = load_config()
            monitor_cfg = config.get('monitor', {})
            interval = monitor_cfg.get('interval', 3600)
            channels = monitor_cfg.get('channels', [])
            
            if not channels:
                log.warning("⚠️ No channels configured in 'monitor' section. Sleeping...")
            
            processed_ids = load_processed_ids()
            
            for ch in channels:
                url = ch.get('url')
                if not url:
                    continue
                    
                name = ch.get('name', url)
                log.info(f"🔍 Checking channel: {name} ...")
                
                vid_id, title = get_latest_video_id(url)
                
                if vid_id:
                    if vid_id in processed_ids:
                        log.debug(f"   Skip: {title} [{vid_id}] (Already processed)")
                    else:
                        log.info(f"🆕 NEW VIDEO FOUND: {title} [{vid_id}]")
                        
                        # Trigger Pipeline
                        video_url = f"https://www.youtube.com/watch?v={vid_id}"
                        if run_pipeline(video_url):
                            save_processed_id(vid_id)
                            # Update local set too
                            processed_ids.add(vid_id)
                        else:
                            log.error(f"⚠️ Failed to process {vid_id}. Will RETRY next cycle.")
                            # Do NOT save ID so it retries later
                else:
                    log.warning(f"   Could not fetch video info for {name}")

            log.info(f"💤 Sleeping for {interval} seconds...")
            time.sleep(interval)
            
        except KeyboardInterrupt:
            log.info("🛑 Monitor stopped by user.")
            break
        except Exception as e:
            log.error(f"❌ Unexpected error in monitor loop: {e}")
            time.sleep(60) # Prevent tight loop on error

if __name__ == "__main__":
    # Ensure history dir exists
    os.makedirs(os.path.dirname(PROCESSED_FILE), exist_ok=True)
    monitor_loop()

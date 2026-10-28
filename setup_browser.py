#!/usr/bin/env python3
"""
Setup Browser Profile
Use this script to launch the isolated browser environment and log in to services.
"""
import os
import sys
import subprocess
import time
from pathlib import Path

# Add project root to path
project_root = str(Path(__file__).resolve().parent)
sys.path.insert(0, project_root)

try:
    from core.utils.browser_config import (
        get_browser_debug_port,
        get_configured_browser_profile_dir,
        prepare_browser_launch,
    )
    from core.utils.config import load_config, update_config
    from core.utils.cookie import resolve_browser_app_info
except ImportError:
    # Minimal config fallback if core not importable directly
    pass

def setup():
    print("🚀 Launching Isolated Browser for Setup...")
    print("   Close the browser window when you are done logging in.")
    
    # 1. Get Profile Path
    try:
        config = load_config()
        # Setup should always target the configured workflow-only profile so the
        # user can establish fresh login state there. Runtime fallback to legacy
        # profiles remains handled elsewhere.
        profile_path = get_configured_browser_profile_dir(config=config)
        debug_port = get_browser_debug_port(config=config)
        browser_key, browser_path, _ = resolve_browser_app_info(
            browser=config.get('browser'),
            user_data_dir=profile_path,
        )
    except:
        profile_path = os.path.expanduser("~/Library/Application Support/Citro Labs/ego lite")
        debug_port = 39222
        browser_key = 'ego'
        browser_path = "/Applications/ego lite.app/Contents/MacOS/ego lite"
        
    expanded_path = os.path.expanduser(profile_path)
    print(f"   📂 Profile Path: {expanded_path}")
    print(f"   🔌 Debug Port: {debug_port}")
    
    # 2. Browser Path
    if not browser_path or not os.path.exists(browser_path):
        print(f"❌ {browser_key.title()} not found at configured location.")
        return

    os.makedirs(expanded_path, exist_ok=True)
    launch = prepare_browser_launch(
        browser_executable=browser_path,
        profile_dir=expanded_path,
        preferred_debug_port=debug_port,
    )
    debug_port = launch['debug_port']
    if launch['port_changed']:
        update_config('browser_debug_port', debug_port)
        print(f"   ℹ️  Debug port changed to {debug_port} to avoid conflicts.")

    # 3. Launch Command (Normal Window, with debug port for later reuse)
    cmd = [
        browser_path,
        f"--user-data-dir={expanded_path}",
        f"--remote-debugging-port={debug_port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--mute-audio",
        "--autoplay-policy=user-gesture-required",
        # Open Key Pages
        "https://www.bilibili.com",
        "https://gemini.google.com"
    ]
    
    print("\n🔹 Opening Bilibili and Gemini...")
    print(f"🔹 Please LOG IN to both services in the BY {browser_key.title()} profile now.")
    
    try:
        if launch['action'] == 'reuse':
            print("\n🔁 Reused existing configured browser profile.")
            subprocess.Popen([
                browser_path,
                f"--user-data-dir={expanded_path}",
                "--mute-audio",
                "--autoplay-policy=user-gesture-required",
                "https://www.bilibili.com",
                "https://gemini.google.com",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return

        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        print("\n✅ Browser launched. Configure login in that window.")
    except KeyboardInterrupt:
        print("\n🛑 Interrupted.")

if __name__ == "__main__":
    setup()

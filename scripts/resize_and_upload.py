from PIL import Image
import os
import sys

# Append project root to path
sys.path.append(os.getcwd())

from core.services.bili_collection_api import BiliCollectionAPI, get_bilibili_credentials, create_session

TARGET_WIDTH = 1280
TARGET_HEIGHT = 720

def resize_image(path):
    if not os.path.exists(path):
        print(f"⚠️ Skipping {path}, not found.")
        return False

    try:
        img = Image.open(path)
        if img.mode != 'RGB':
            img = img.convert('RGB')
            
        current_w, current_h = img.size
        
        # Check if already correct size
        if current_w == TARGET_WIDTH and current_h == TARGET_HEIGHT:
             print(f"✅ {os.path.basename(path)} is already {TARGET_WIDTH}x{TARGET_HEIGHT}")
             return True

        # Calculate crop box for 16:9
        target_ratio = TARGET_WIDTH / TARGET_HEIGHT
        current_ratio = current_w / current_h
        
        if current_ratio > target_ratio:
            # Too wide
            new_width = int(current_h * target_ratio)
            left = (current_w - new_width) / 2
            box = (left, 0, left + new_width, current_h)
        else:
            # Too tall (square falls here)
            new_height = int(current_w / target_ratio)
            top = (current_h - new_height) / 2
            box = (0, top, current_w, top + new_height)
            
        img = img.crop(box)
        img = img.resize((TARGET_WIDTH, TARGET_HEIGHT), Image.Resampling.LANCZOS)
        
        img.save(path)
        print(f"✅ Resized {os.path.basename(path)} to {TARGET_WIDTH}x{TARGET_HEIGHT}")
        return True
    except Exception as e:
        print(f"❌ Error resizing {path}: {e}")
        return False

def main():
    covers = {
        "UE5 游戏开发教程": "assets/covers/ue5.png",
        "居家暴汗健身跟练": "assets/covers/fitness.png",
        "AI 黑科技前沿": "assets/covers/ai.png",
        "其他精选": "assets/covers/others.png"
    }

    # 1. Resize
    print("🎨 Resizing covers...")
    for name, path in covers.items():
        # Ensure path is absolute for safety
        abs_path = os.path.abspath(path)
        if resize_image(abs_path):
             pass

    # 2. Upload
    print("\n🚀 Uploading covers...")
    try:
        creds = get_bilibili_credentials()
        session = create_session(creds)
        api = BiliCollectionAPI(session)
        
        for name, path in covers.items():
            abs_path = os.path.abspath(path)
            if os.path.exists(abs_path):
                url = api.upload_cover(abs_path)
                if url:
                    print(f"✅ {name}: {url}")
                else:
                    print(f"❌ {name}: Upload failed")
            else:
                print(f"⚠️ {name}: File missing")
                
    except Exception as e:
        print(f"❌ Upload process failed: {e}")

if __name__ == "__main__":
    main()

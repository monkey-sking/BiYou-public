import sys
import os

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from biliup.plugins.bili_webup import BiliBili
    import inspect
    
    print("🔍 Inspecting BiliBili class methods:")
    for name, method in inspect.getmembers(BiliBili, predicate=inspect.isfunction):
        print(f"  - {name}")
        
except ImportError as e:
    print(f"❌ Could not import biliup: {e}")
except Exception as e:
    print(f"❌ Error inspecting: {e}")

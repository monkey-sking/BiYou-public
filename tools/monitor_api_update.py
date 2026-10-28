import subprocess
import sys
import json
import urllib.request
from packaging import version

def get_installed_version(package_name):
    try:
        result = subprocess.run([sys.executable, '-m', 'pip', 'show', package_name], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            if line.startswith('Version:'):
                return line.split(':')[1].strip()
    except Exception as e:
        return None
    return None

def get_pypi_version(package_name):
    try:
        url = f"https://pypi.org/pypi/{package_name}/json"
        with urllib.request.urlopen(url, timeout=5) as response:
            data = json.loads(response.read())
            return data['info']['version']
    except Exception as e:
        return None

def main():
    package = "gemini-webapi"
    print(f"🔍 Checking for updates to '{package}'...")
    
    current = get_installed_version(package)
    latest = get_pypi_version(package)
    
    print(f"   Current Version: {current}")
    print(f"   Latest Version:  {latest}")
    
    if current and latest and version.parse(latest) > version.parse(current):
        print(f"\n🚀 **UPDATE AVAILABLE!** Version {latest} is out.")
        print(f"   Run: pip install --upgrade {package}")
        print(f"   Changelog: https://github.com/HanaokaYuzu/Gemini-API/releases")
    else:
        print(f"\n✅ You are up to date.")
        print(f"   Monitor future updates here: https://github.com/HanaokaYuzu/Gemini-API")

if __name__ == "__main__":
    main()

#!/bin/bash
# BiYou Pre-flight Check Script
# Run this script before starting development to ensure your environment is ready.

echo "=== BiYou Environment Check ==="

# Check Python version
echo -n "[1/7] Python 3.10+: "
python3 --version 2>/dev/null | grep -qE "3\.(1[0-9]|[2-9][0-9])" && echo "✓ OK" || echo "✗ FAIL (Requires Python 3.10+)"

# Check yt-dlp version from the active Python environment
echo -n "[2/7] yt-dlp (Python env): "
python3 - <<'PY' >/dev/null 2>&1
import sys
import yt_dlp

parts = []
for chunk in yt_dlp.version.__version__.split('.'):
    digits = ''.join(ch for ch in chunk if ch.isdigit())
    parts.append(int(digits or '0'))
parts = tuple((parts + [0, 0, 0])[:3])
sys.exit(0 if parts >= (2026, 3, 17) else 1)
PY
[ $? -eq 0 ] && echo "✓ OK" || echo "✗ FAIL (Run: python3 -m pip install --break-system-packages --user -U 'yt-dlp[default]==2026.03.17')"

# Check yt-dlp-ejs
echo -n "[3/7] yt-dlp-ejs: "
python3 - <<'PY' >/dev/null 2>&1
from importlib import metadata
metadata.version("yt-dlp-ejs")
PY
[ $? -eq 0 ] && echo "✓ OK" || echo "✗ FAIL (Install with yt-dlp[default])"

# Check deno
echo -n "[4/7] deno 2.0+: "
python3 - <<'PY' >/dev/null 2>&1
import re
import shutil
import subprocess
import sys

deno = shutil.which("deno")
if not deno:
    sys.exit(1)
out = subprocess.check_output([deno, "--version"], text=True).splitlines()[0]
match = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
if not match:
    sys.exit(1)
parts = tuple(int(x) for x in match.groups())
sys.exit(0 if parts >= (2, 0, 0) else 1)
PY
[ $? -eq 0 ] && echo "✓ OK" || echo "✗ FAIL (Install Deno 2.0+ for YouTube JS challenges)"

# Check ffmpeg
echo -n "[5/7] ffmpeg: "
command -v ffmpeg >/dev/null 2>&1 && echo "✓ OK" || echo "✗ FAIL (Install ffmpeg and add to PATH)"

# Check config file
echo -n "[6/7] config/config.yaml: "
[ -f "config/config.yaml" ] && echo "✓ OK" || echo "✗ FAIL (Copy from config/config.example.yaml)"

# Check requirements installed (basic check for biliup)
echo -n "[7/7] biliup: "
python3 -c "import biliup" 2>/dev/null && echo "✓ OK" || echo "✗ FAIL (Run: pip install -r requirements.txt)"

echo "=== Check Complete ==="

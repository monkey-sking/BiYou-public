#!/bin/zsh
# Retry 2 videos with fixed jimeng cover pipeline (2026-08-29)
RUN_BY="${HOME}/.hermes/skills/openclaw-imports/biyou-uploader/scripts/run-by.sh"
export OVERRIDE_MAX_AGE=9999
URLS=(
  "https://youtu.be/Xv6H89VO2bQ"
  "https://youtu.be/ssMLuxpXHS0"
)
for url in "${URLS[@]}"; do
  echo "===== [$(date '+%F %T')] RETRY $url ====="
  "$RUN_BY" "$url" --skip-fetch 2>&1 | tail -6
  echo "===== [$(date '+%F %T')] DONE $url ====="
done
echo "RETRY_2_DONE"
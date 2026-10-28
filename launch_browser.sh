#!/bin/bash
# Launch Gemini in the ego browser (single-instance reuse).
# 2026-08-09: 根治版 — 不再启动新浏览器进程/不杀进程/不开新窗口。
# 用 ego-browser CLI 原生通道在既有 ego 实例里打开 Gemini tab，
# 避免 launch_browser.sh 旧版每次运行堆积 1 个窗口（曾堆积 23+ 窗口 → 40+ renderer 进程）。
#
# Usage: ./launch_browser.sh [reuse|login]
#   reuse (默认) — 在 agent 空间打开 Gemini tab（不打扰用户空间）
#   login — 在用户空间打开 Gemini 登录页（登录态写全局 profile，供自动化复用）
set -uo pipefail

EGO_CLI="$HOME/.local/bin/ego-browser"
GEMINI_URL="https://gemini.google.com/app"

ACTION="${1:-reuse}"

if [ ! -x "$EGO_CLI" ]; then
  echo "❌ ego-browser CLI not found: $EGO_CLI"
  exit 1
fi

case "$ACTION" in
  reuse)
    # 在专用 agent 空间打开 Gemini（空间不存在会自动创建）
    echo "🚀 Opening Gemini in ego task space (reuse)..."
    "$EGO_CLI" nodejs -e '
      await ego.helpers.useOrCreateTaskSpace("biyou-gemini");
      await ego.helpers.switchTaskSpace("biyou-gemini");
      await ego.helpers.takeOverTaskSpace();
      const t = await ego.helpers.openOrReuseTab("'"$GEMINI_URL"'", {wait: false});
      console.log("opened:", t ? t.title || "ok" : "ok");
    ' 2>&1 | tail -2
    echo "✅ Gemini opened (no new browser process/window)."
    ;;
  login)
    # 用户空间打开登录页（不新建窗口，复用现有实例）
    echo "🚀 Opening Gemini login in existing ego instance..."
    open -a "ego lite" --args "$GEMINI_URL" 2>/dev/null || true
    echo "✅ Gemini login page opened (use existing window/tab)."
    ;;
  *)
    echo "❌ Unknown action: $ACTION (use reuse|login)"
    exit 1
    ;;
esac
exit 0

#!/bin/bash
# yt-dlp wrapper — inject cookies from the CDP-extracted cookie file
# 2026-08-10: 读持久路径 ~/.hermes/biyou/yt_cookies.txt（refresh_yt_cookies.sh 的写入位置）。
# 旧版读 /tmp/yt_cookies.txt —— /tmp 重启被清 + 与刷新脚本路径不一致，导致 yt-dlp 常拿旧 cookie。
exec /opt/homebrew/bin/python3 -m yt_dlp --cookies "$HOME/.hermes/biyou/yt_cookies.txt" "$@"

#!/bin/bash

# 获取项目根目录的绝对路径
PROJECT_ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
MAIN_SCRIPT="${PROJECT_ROOT}/main.py"

echo "🚀 正在为 BiYou 配置 CLI 访问..."

# 检查 main.py 是否存在
if [ ! -f "$MAIN_SCRIPT" ]; then
    echo "❌ 错误: 未能在 $PROJECT_ROOT 找到 main.py"
    exit 1
fi

# 确保 main.py 可执行
chmod +x "$MAIN_SCRIPT"

# 确定 shell 配置文件
if [ -n "$ZSH_VERSION" ] || [ -f "$HOME/.zshrc" ]; then
    SHELL_CONFIG="$HOME/.zshrc"
elif [ -n "$BASH_VERSION" ] || [ -f "$HOME/.bash_profile" ]; then
    SHELL_CONFIG="$HOME/.bash_profile"
else
    SHELL_CONFIG="$HOME/.profile"
fi

# ==========================================
# 清理旧的配置 (biyou)
# ==========================================
if grep -q "alias biyou=" "$SHELL_CONFIG" || grep -q "biyou()" "$SHELL_CONFIG"; then
    echo "🧹 正在清理旧的 'biyou' 配置..."
    # 尝试删除简单的 alias
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' '/alias biyou=/d' "$SHELL_CONFIG"
    else
        sed -i '/alias biyou=/d' "$SHELL_CONFIG"
    fi
    # 注意：删除多行函数定义比较危险，建议用户手动检查
    echo "⚠️  提示: 如果你的配置文件中有旧的 biyou() 函数，建议手动删除。"
fi

# ==========================================
# 写入新的函数 (by)
# ==========================================

# 检查是否已存在 by()
if grep -q "by() {" "$SHELL_CONFIG"; then
    echo "ℹ️  检测到已存在 'by' 函数，跳过写入 (或者你可以手动编辑 $SHELL_CONFIG)"
else
    echo "✍️  写入 'by' 命令函数到 $SHELL_CONFIG ..."
    cat << EOF >> "$SHELL_CONFIG"

# BiYou CLI Function (Command: by)
# Usage: 
#   1. by https://youtube.com/... (Direct)
#   2. by (Interactive, no quotes needed)
by() {
    python3 "$MAIN_SCRIPT" "$@"
}
EOF
fi

# ==========================================
# Zsh 特色优化: 自动转义 URL
# ==========================================
if [ -n "$ZSH_VERSION" ] || [[ "$SHELL_CONFIG" == *".zshrc"* ]]; then
    if ! grep -q "url-quote-magic" "$SHELL_CONFIG"; then
        echo "" >> "$SHELL_CONFIG"
        echo "# BiYou: 自动转义 URL 中的特殊字符" >> "$SHELL_CONFIG"
        echo "autoload -Uz url-quote-magic" >> "$SHELL_CONFIG"
        echo "zle -N self-insert url-quote-magic" >> "$SHELL_CONFIG"
        echo "✅ 已启用 Zsh URL 自动转义优化"
    fi
fi

echo ""
echo "✅ 配置成功！"
echo "------------------------------------------------"
echo "👉 请运行以下命令使其立即生效:"
echo "   source $SHELL_CONFIG"
echo ""
echo "🎉 使用方法:"
echo "   1. 交互模式 (推荐): 输入 'by' 回车 -> 粘贴 URL"
echo "   2. 命令行模式:      by <URL>"
echo "------------------------------------------------"

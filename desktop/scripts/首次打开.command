#!/bin/bash
# whosaid 首次打开：去除 macOS Gatekeeper 隔离属性。
# 本 app 未走 Apple 签名+公证，从网上下载后直接双击会被 Gatekeeper 拦下（提示"已损坏"或
# "无法打开"），需先运行本脚本去掉隔离标记，之后即可正常双击。
set -euo pipefail

APP_PATH="/Applications/whosaid.app"

if [ ! -d "$APP_PATH" ]; then
  # 兼容用户还没拖进「应用程序」、直接在下载目录/DMG 里运行本脚本的情况
  SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
  CANDIDATE="$SCRIPT_DIR/whosaid.app"
  if [ -d "$CANDIDATE" ]; then
    APP_PATH="$CANDIDATE"
  else
    echo "未找到 whosaid.app。"
    echo "请先把 whosaid.app 拖进「应用程序」文件夹，再运行本脚本。"
    read -n 1 -s -r -p "按任意键关闭这个窗口…"
    echo
    exit 1
  fi
fi

echo "正在为 $APP_PATH 去除隔离属性…"
xattr -dr com.apple.quarantine "$APP_PATH"
echo "完成。现在可以正常双击打开 whosaid 了。"
read -n 1 -s -r -p "按任意键关闭这个窗口…"
echo

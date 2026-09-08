#!/bin/bash
set -euo pipefail

signing_identity="${APPLE_SIGNING_IDENTITY:-whosaid Local Development}"

if ! security find-identity -v -p codesigning | grep -Fq "\"${signing_identity}\""; then
  echo "未找到固定代码签名身份：${signing_identity}" >&2
  echo "拒绝退回临时签名，以免 macOS 再次清除 whosaid 的录音权限。" >&2
  exit 1
fi

export APPLE_SIGNING_IDENTITY="${signing_identity}"
src-tauri/python/bin/python3 scripts/check-private-data.py
npm run tauri build -- "$@"

signed_app="src-tauri/target/release/bundle/macos/whosaid.app"
signed_entitlements="$(codesign -d --entitlements :- "$signed_app" 2>/dev/null)"
if ! plutil -extract 'com\.apple\.security\.device\.audio-input' raw -o - - <<< "$signed_entitlements" | grep -qx true; then
  echo "签名产物缺少麦克风音频输入权限，禁止安装。" >&2
  exit 1
fi
codesign --verify --deep --strict "$signed_app"

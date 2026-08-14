#!/bin/bash
set -euo pipefail

signing_identity="${APPLE_SIGNING_IDENTITY:-whosaid Local Development}"

if ! security find-identity -v -p codesigning | grep -Fq "\"${signing_identity}\""; then
  echo "未找到固定代码签名身份：${signing_identity}" >&2
  echo "拒绝退回临时签名，以免 macOS 再次清除 whosaid 的录音权限。" >&2
  exit 1
fi

export APPLE_SIGNING_IDENTITY="${signing_identity}"
exec npm run tauri build -- "$@"

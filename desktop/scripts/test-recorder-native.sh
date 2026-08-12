#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
recorder_dir="$(cd "$script_dir/../src-tauri/native/recorder" && pwd)"
test_binary="$(mktemp "${TMPDIR:-/tmp}/whosaid-recorder-native.XXXXXX")"
trap 'rm -f "$test_binary"' EXIT

xcrun clang++ -fobjc-arc -std=c++17 -fblocks \
  "$recorder_dir/RecorderBridge.mm" \
  "$recorder_dir/tests/RecorderNativeTests.mm" \
  -framework ScreenCaptureKit \
  -framework AVFoundation \
  -framework CoreMedia \
  -framework CoreAudio \
  -framework AppKit \
  -framework Foundation \
  -o "$test_binary"

"$test_binary"

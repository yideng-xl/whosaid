#!/usr/bin/env bash
# 组装 whosaid 分发包的自包含运行时：下载 python-build-standalone（可重定位 CPython 3.13,
# arm64）到 desktop/src-tauri/python/，用它自带的 pip 全新安装 pinned 依赖（不拷 Homebrew venv，
# 避免路径/ABI 坑），瘦身后把 core/transcribe_core 源码复制进 desktop/src-tauri/core/，最后下载
# 静态 ffmpeg+ffprobe（arm64）到 desktop/src-tauri/ffmpeg/，让同事无需 brew install ffmpeg。
#
# 产物直接落在 desktop/src-tauri/{python,core,ffmpeg}/（不套 resources/ 子目录）：
# tauri.conf.json 的 bundle.resources 数组写法会原样保留 glob 里写的路径前缀，若产物放在
# resources/python/ 则打包出的会是 Contents/Resources/resources/python/...，与 Rust 侧
# resource_dir().join("python") 的路径约定对不上；直接落在 src-tauri/ 下的 python/、core/、
# ffmpeg/ 才能让 bundle.resources=["python/**/*","core/**/*","ffmpeg/**/*"] 打出
# Contents/Resources/python/... 等。
#
# 幂等：每次运行先清空 python/、core/、ffmpeg/ 再重建，保证产物可复现；
# 失败即止（set -euo pipefail），不允许某一步出错后静默继续产出半成品。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$DESKTOP_DIR/.." && pwd)"
STAGING_ROOT="$DESKTOP_DIR/src-tauri"
CORE_SRC="$REPO_ROOT/core/transcribe_core"
REQUIREMENTS_LOCK="$SCRIPT_DIR/requirements.lock"

# python-build-standalone 发行版：CPython 3.13.14, arm64 macOS, install_only_stripped
# （去调试符号，体积更小）。升级 Python 版本时同步改这两个变量即可。
PBS_TAG="20260623"
PBS_ASSET="cpython-3.13.14+${PBS_TAG}-aarch64-apple-darwin-install_only_stripped.tar.gz"
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/${PBS_ASSET}"

# 静态 ffmpeg + ffprobe（macOS arm64, FFmpeg 8.1，osxexperts.net）。audio.py/mlx_backend.py
# 调裸 ffmpeg/ffprobe（走 PATH），Task 3 把本目录前插进子进程 PATH 即可让包内版本优先。
FFMPEG_URL="https://www.osxexperts.net/ffmpeg81arm.zip"
FFPROBE_URL="https://www.osxexperts.net/ffprobe81arm.zip"

if [ ! -f "$REQUIREMENTS_LOCK" ]; then
  echo "缺 $REQUIREMENTS_LOCK：先在 core/venv 里跑" >&2
  echo "  cd core && venv/bin/pip freeze > ../desktop/scripts/requirements.lock" >&2
  exit 1
fi
if [ ! -d "$CORE_SRC" ]; then
  echo "找不到 $CORE_SRC" >&2
  exit 1
fi

echo "== 1/6 清空旧产物 =="
rm -rf "$STAGING_ROOT/python" "$STAGING_ROOT/core" "$STAGING_ROOT/ffmpeg"

echo "== 2/6 下载 python-build-standalone: $PBS_ASSET =="
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
curl -fL --progress-bar -o "$TMP_DIR/$PBS_ASSET" "$PBS_URL"
tar xzf "$TMP_DIR/$PBS_ASSET" -C "$STAGING_ROOT"
# 解压后得到 $STAGING_ROOT/python/bin/python3，与 Rust 侧打包态路径解析约定一致
PY="$STAGING_ROOT/python/bin/python3"
PIP="$STAGING_ROOT/python/bin/pip3"
if [ ! -x "$PY" ]; then
  echo "解压后未找到 $PY，python-build-standalone 产物布局可能变了，需要更新本脚本" >&2
  exit 1
fi

echo "== 3/6 全新安装 pinned 依赖 =="
"$PIP" install --no-cache-dir --upgrade pip
"$PIP" install --no-cache-dir -r "$REQUIREMENTS_LOCK"

echo "== 4/6 瘦身 =="
SITE_PACKAGES="$("$PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
find "$SITE_PACKAGES" -type d -name "__pycache__" -prune -exec rm -rf {} +
find "$SITE_PACKAGES" -maxdepth 2 -type d -name "tests" -prune -exec rm -rf {} +
find "$SITE_PACKAGES" -maxdepth 2 -type d -name "test" -prune -exec rm -rf {} +
find "$STAGING_ROOT/python" -type f -name "*.pyc" -delete
RUNTIME_SIZE="$(du -sh "$STAGING_ROOT/python" | cut -f1)"
echo "python 运行时体积：$RUNTIME_SIZE"

echo "== 5/6 复制 transcribe_core 源码 =="
mkdir -p "$STAGING_ROOT/core"
cp -R "$CORE_SRC" "$STAGING_ROOT/core/transcribe_core"

echo "== 6/6 下载静态 ffmpeg + ffprobe =="
mkdir -p "$STAGING_ROOT/ffmpeg"
curl -fL --progress-bar -o "$TMP_DIR/ffmpeg.zip" "$FFMPEG_URL"
curl -fL --progress-bar -o "$TMP_DIR/ffprobe.zip" "$FFPROBE_URL"
# 两个 zip 各自解出单个可执行文件（ffmpeg / ffprobe），直接解到 ffmpeg/ 目录
unzip -o -j "$TMP_DIR/ffmpeg.zip" -d "$STAGING_ROOT/ffmpeg"
unzip -o -j "$TMP_DIR/ffprobe.zip" -d "$STAGING_ROOT/ffmpeg"
chmod +x "$STAGING_ROOT/ffmpeg/ffmpeg" "$STAGING_ROOT/ffmpeg/ffprobe"
if [ ! -x "$STAGING_ROOT/ffmpeg/ffmpeg" ] || [ ! -x "$STAGING_ROOT/ffmpeg/ffprobe" ]; then
  echo "ffmpeg/ffprobe 解压后未就位，下载源布局可能变了，需要更新本脚本" >&2
  exit 1
fi

echo "完成。产物："
echo "  $STAGING_ROOT/python/bin/python3"
echo "  $STAGING_ROOT/core/transcribe_core"
echo "  $STAGING_ROOT/ffmpeg/ffmpeg + ffprobe"

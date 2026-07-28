# 设计：Windows x64 首版

日期：2026-07-28  
状态：已确认，实施中

## 目标

在不改动现有任务、稿件和桌面交互的前提下，提供可安装的 Windows 10/11
x64 版本。首版以兼容性为先，保证没有独立显卡的电脑也能完成本地转写和
说话人分离。

## 首版边界

- 支持 Windows 10/11 x64。
- 所有音频、模型和稿件仍保存在本机。
- 转写使用 faster-whisper CPU `int8`。
- 说话人分离继续使用 pyannote CPU。
- 提供 Tauri NSIS `.exe` 安装包。
- 模型不放进安装包，首次使用时由用户下载。
- NVIDIA CUDA 加速、Windows ARM64、代码签名不纳入首版。
- “人名统一替换”继续后置。

## 内核设计

新增 `FasterWhisperBackend`，与现有 `MlxBackend` 共同实现
`InferenceBackend`：

- macOS Apple Silicon 默认选择 `mlx`。
- Windows 默认选择 `faster-whisper`。
- `WHOSAID_BACKEND` 可显式覆盖，便于测试和排障。
- faster-whisper 首版固定 `device="cpu"`、`compute_type="int8"`。
- pyannote 复用现有实现；只有 MPS 可用时才进入 MPS，其余平台留在 CPU。

模型注册表按后端提供仓库：

- MLX 继续使用 `mlx-community/*-mlx`。
- Windows 使用 faster-whisper 已转换仓库。
- 首版 Windows 提供 Tiny、Base、Small、Medium、Large v3。
- Belle 中文微调模型暂不在 Windows 展示，待完成 CTranslate2 转换和效果
  验证后再接入。

## 桌面与运行时

Rust 外壳使用平台相关路径：

- macOS Python：`python/bin/python3`
- Windows Python：`python/python.exe`
- macOS ffmpeg：`ffmpeg/ffmpeg`
- Windows ffmpeg：`ffmpeg/ffmpeg.exe`
- 数据目录统一改用 Tauri 的应用数据目录，不再手拼 macOS 路径。
- 子进程 `PATH` 使用系统路径分隔符，避免 Windows 上写成冒号。

新增 PowerShell 运行时脚本：

1. 下载 x86_64 Windows python-build-standalone。
2. 安装 Windows 锁定依赖。
3. 拷贝 `core/transcribe_core`。
4. 下载 Windows x64 ffmpeg/ffprobe。
5. 删除缓存和测试文件，检查关键模块可导入。

## 构建与验收

新增手动触发的 GitHub Actions 工作流，运行于 `windows-latest`：

1. 安装前端和 Rust 环境。
2. 组装 Windows 自包含运行时。
3. 跑 Python 非 slow 测试、前端测试、Svelte 检查和 Rust 测试。
4. 构建 NSIS 安装包。
5. 检查安装包存在且包含运行时资源。
6. 首轮只上传 Actions Artifact；本机安装验证通过后再并入 GitHub Release。

## 风险

- pyannote 和 PyTorch 会使安装包明显变大。
- CPU 上 Large v3 较慢，默认模型应在实机测试后决定是否改为 Small。
- GitHub runner 能验证构建和服务启动，无法替代 Windows 实机上的安装、
  WebView2、长音频性能和休眠恢复测试。

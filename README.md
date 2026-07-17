# whosaid · 谁说的

> 本地、私有的中文会议转写工具——**把录音转成文字，并分清「谁在说」**。
> 音频与文字全程不出本机，只有首次下载模型时联网。

[![状态](https://img.shields.io/badge/状态-内核可用·界面WIP-orange)]()
[![平台](https://img.shields.io/badge/平台-Apple_Silicon-black)]()
[![许可](https://img.shields.io/badge/许可-MIT-blue)]()

## 这是什么

把已有录音（会议、访谈）在**本地**转成带说话人标注的文字稿：

```
说话人A：这个需求这周能上线吗？
说话人B：原型还在评审，得下周。
```

- 🔒 **本地私有**：转写与说话人分离全在本机推理，内容不上传云端
- 🗣️ **分清谁在说**：不只是转文字，还区分 2–10 位说话人
- 🇨🇳 **中文优先**：针对中文会议场景
- 🔧 **模型可换**：转写模型（whisper 各尺寸）与说话人分离模型可下载/切换

底层：[mlx-whisper](https://github.com/ml-explore/mlx-examples)（转写）+ [pyannote.audio](https://github.com/pyannote/pyannote-audio)（说话人分离），Apple Silicon 原生加速。

## 现状与路线

| 阶段 | 内容 | 状态 |
|---|---|---|
| **一期 · 内核** | 转写 + 说话人标注 + 模型管理 + 任务队列 + 本地 HTTP 服务（REST/WebSocket） | ✅ 可用 |
| **二期 · 桌面界面** | Tauri 外壳 + 前端：拖拽转写、两阶段（逐字稿/会话稿）、改说话人名、试听、导出、模型管理、深浅主题、真 macOS 磨砂 | ✅ 基本完成（Apple Silicon 本机可双击用） |
| 二期尾 · 分发打包 | 打包内含 Python 依赖 + 模型的「发给同事双击即用」版（含门控模型分发、python 解析健壮化） | ⏳ 待做 |
| 后续 · 直接录音 | app 内直接录电脑系统声/麦克风并转写，免先用别的工具录好再导入 | 🔭 计划 |
| 后续 · 跨平台 | Intel Mac / Windows（换可插拔推理后端） | 🔭 设计已预留接口 |

> 内核可用命令行起服务、用 HTTP 调用；**Apple Silicon 桌面界面已可双击使用**。仍缺的是**"发给同事零配置双击即用"的分发包**（Python 依赖 + 模型一起打包），见上表「二期尾 · 分发打包」。

## 快速开始（内核）

需要 Apple Silicon Mac + 已装 `ffmpeg`（`brew install ffmpeg`）。

```bash
cd core
python3.13 -m venv venv
venv/bin/pip install -U pip
venv/bin/pip install "numpy>=2.1" "mlx-whisper>=0.4.0" "pyannote.audio>=4.0" \
    "fastapi>=0.110" "uvicorn>=0.27"

# 起本地服务（首次会从 HuggingFace 拉模型；pyannote 为门控模型，需先在其页面同意条款并登录）
HF_ENDPOINT=https://hf-mirror.com venv/bin/python -m transcribe_core.server
# 输出 PORT=<随机端口>，随后即可 curl 调用
```

提交一个转写任务：

```bash
curl -X POST localhost:<PORT>/jobs \
  -H 'content-type: application/json' \
  -d '{"audio_path":"/abs/path/录音.m4a"}'
# 轮询 GET /jobs/<id> 到 done，再 GET /jobs/<id>/export?fmt=txt 取稿
```

更多接口与开发说明见 [`core/README.md`](core/README.md)。

## 架构

```
Tauri 外壳(二期) ──HTTP/WS──► Python 服务(transcribe_core)
                                  ├─ InferenceBackend 抽象  ← 可插拔
                                  │    └─ MlxBackend（一期唯一实现）
                                  ├─ 任务队列（单并发 + 进度推送）
                                  ├─ 模型注册表（下载/切换）
                                  └─ 转写稿模型（说话人标注 / 导出 txt·srt）
```

推理全部藏在 `InferenceBackend` 接口后，扩展 Intel/Windows 只需新增一个后端实现，上层不改。

## 许可

MIT

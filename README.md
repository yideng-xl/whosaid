# whosaid · 谁说的

> 本地、私有的中文会议转写工具——**把录音转成文字，并分清「谁在说」**。
> 音频与文字全程不出本机，只有首次下载模型时联网。

[![状态](https://img.shields.io/badge/状态-Beta·打包验收中-orange)]()
[![平台](https://img.shields.io/badge/平台-Apple_Silicon-black)]()
[![许可](https://img.shields.io/badge/许可-MIT-blue)]()

## 这是什么

把已有录音（会议、访谈）或 Mac 正在播放的系统声音在**本地**转成带说话人标注的文字稿：

```
说话人A：这个需求这周能上线吗？
说话人B：原型还在评审，得下周。
```

- 🔒 **本地私有**：转写与说话人分离全在本机推理，内容不上传云端
- 🗣️ **分清谁在说**：先做说话人分离、再按「发言块」逐块单说话人转写，每段归属**按构造正确**——不会把一段话里的多个说话人混成一个、也不会把少数说话人整段抹掉
- 📄 **三种导出**：会话稿（说话人＋内容）、字幕稿（SRT）、逐字稿（带时间戳 `[MM:SS]`、不带人名，适合快速通读/校对）
- 👤 **人名统一**：规则推荐可能的人名，人工确认后一次统一正文和说话人显示名
- 🇨🇳 **中文优先**：针对中文会议场景（转写默认 Belle 中文微调模型）
- 🔧 **模型可换**：转写模型（whisper 各尺寸 / Belle 中文微调）与说话人分离引擎可下载/切换
- 🎤 **直接录音**：在 macOS 13+ Apple Silicon 上录制全系统声音，可同时录入麦克风；停止后先试听，确认无误再开始分人转写

底层：macOS 使用 [mlx-whisper](https://github.com/ml-explore/mlx-examples)，
Windows 使用 [faster-whisper](https://github.com/SYSTRAN/faster-whisper)，
说话人分离统一使用 [pyannote.audio](https://github.com/pyannote/pyannote-audio)。

## 现状与路线

| 阶段 | 内容 | 状态 |
|---|---|---|
| **一期 · 内核** | 转写 + 说话人标注 + 模型管理 + 任务队列 + 本地 HTTP 服务（REST/WebSocket） | ✅ 可用 |
| **二期 · 桌面界面** | Tauri 外壳 + 前端：拖拽转写、两阶段（逐字稿/会话稿）、改说话人名、试听、导出、模型管理、深浅主题、真 macOS 磨砂 | ✅ 基本完成（Apple Silicon 本机可双击用） |
| **二期尾 · Apple Silicon 打包** | 自包含 Python 运行时与 ffmpeg；模型首次运行下载 | ✅ v0.1.0 已发布 |
| **三期 · Windows 版本** | faster-whisper CPU 后端 + 自包含运行时 + NSIS 安装包 | 🧪 首个候选包已生成，待实机转写验收 |
| **后续 · 人名统一替换** | 候选词提取、人工确认、一键统一替换正文和说话人显示名 | ✅ 首版完成 |
| 后续 · 直接录音 | macOS app 内直接录全系统声音和可选麦克风，试听确认后手动开始分人转写 | 🧪 自动验证与打包验收中 |

> Windows 首版支持 Windows 10/11 x64，默认使用 CPU；NVIDIA CUDA 加速放在后续阶段。

下载：[whosaid v0.1.0](https://github.com/yideng-xl/whosaid/releases/tag/v0.1.0)

## macOS 直接录音

直接录音仅在 **macOS 13+ Apple Silicon** 构建中提供。Windows、Intel Mac 和其他平台
不会显示录音入口，也不会初始化录音监听、扫描恢复会话或请求录音权限。平台能力由 Rust
按应用编译目标明确告知前端；底层保留兼容命令作为防御层，不代表这些平台开放该功能。
从左侧进入录音页后，whosaid 会录制
全系统声音，并在麦克风可用且已授权时同时录入你的发言。麦克风被腾讯会议等应用占用、
未授权或不可用时，系统声音仍会继续录制。

首次使用需要允许 macOS 的系统录音权限；如需录入自己的发言，还要允许麦克风权限。
授权后系统可能要求退出并重新打开 whosaid，按页面提示操作即可。whosaid 通过系统录音
接口采集声音，**不录制或保存屏幕画面**。

点击停止后，应用会先保存并合成 `.m4a`，并展示播放器、可编辑的录音名称和保存路径。
可以连续录制多段，再逐条试听决定是否转写。双轨合成会用系统声作为参考，降低腾讯会议
等扬声器声音再次进入麦克风形成的回音。试听确认无误后，点击“确认无误，开始转写”才会
创建分人和转写任务。若保存或合成中断，下次启动时页面会
提示恢复未完成录音；已经保存但尚未确认或提交失败的录音，也会恢复到试听确认列表。
待确认清单由应用后端与录音完成凭据一起持久化，不依赖页面是否仍然打开；只有转写任务
提交成功后才移除。升级前已经生成完成凭据的录音会自动进入清单；更早版本遗留、且没有
完成凭据的零散 `.m4a` 无法可靠判断来源，需要按普通文件拖入。
临时音轨保存在
`~/Library/Application Support/whosaid/recordings/.incomplete/`，不要在恢复完成前手动删除；
最终录音保存在其上一级 `recordings/` 目录。

安装或替换新版应用前，请先停止并保存正在进行的录音，完成或暂停已有转写任务，并确认
没有仍在排队或运行的任务；否则应延后更新。当前版本不会自动替你完成这项更新前检查。

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
# 轮询 GET /jobs/<id> 到 done，再取稿：
#   fmt=txt 会话稿（说话人＋内容）   fmt=srt 字幕稿   fmt=plain 逐字稿（时间戳，无人名）
```

更多接口与开发说明见 [`core/README.md`](core/README.md)。

## 架构

```
Tauri 外壳(二期) ──HTTP/WS──► Python 服务(transcribe_core)
                                  ├─ 转写管线：先分离 → 精炼成「发言块」→ 逐块单说话人转写
                                  │              （归属按构造正确，不再事后硬对齐）
                                  ├─ InferenceBackend 抽象  ← 可插拔（转写后端）
                                  │    └─ MlxBackend（mlx-whisper + Belle 中文微调）
                                  ├─ diarize/ 分离引擎      ← 可插拔（pyannote，预留 sherpa 等）
                                  ├─ 任务队列（单并发 + 进度推送 + 断点续跑）
                                  ├─ 模型注册表（下载/切换）
                                  └─ 转写稿模型（说话人标注 / 导出 txt·srt·逐字稿）
```

转写后端藏在 `InferenceBackend` 接口后（扩展 Intel/Windows 只需新增实现），说话人分离藏在 `diarize/` 子包后（换引擎只需实现 `DiarizeEngine`），上层均不改。

## 许可

MIT

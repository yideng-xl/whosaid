# transcribe-core

本地转写服务内核：把音频转写为带说话人标注的文字稿，通过 FastAPI 暴露
REST + WebSocket 接口。一期仅在本目录内独立运行、可用 curl/websocket
客户端测通；`create_app()` 暴露的接口即对外契约，Tauri 外壳（下一份计划）
直接消费本服务，不依赖本目录任何内部实现细节。

## 环境要求

- Python 3.13+
- Apple Silicon 使用 `mlx-whisper`，通过 Metal 加速。
- Windows 10/11 x64 使用 `faster-whisper` CPU `int8`。
- 说话人分离统一使用 `pyannote.audio`；Windows 首版在 CPU 上运行。

## 建 venv 与装依赖

在 `core/` 目录下：

```bash
python3.13 -m venv venv

# pip 走国内镜像（网络代理对 PyPI CDN 较慢，需走出沙箱执行，保证能联网装包）
venv/bin/pip install -i https://mirrors.aliyun.com/pypi/simple/ \
    fastapi "uvicorn[standard]" pydantic starlette httpx \
    mlx mlx-whisper pyannote-audio \
    huggingface_hub pytest
```

模型权重从 HuggingFace 拉取，国内直连很慢，需要设置镜像端点（后续起服务、
跑 slow 测试、手动下载模型都要带上）：

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

## 起服务

```bash
cd core
HF_ENDPOINT=https://hf-mirror.com venv/bin/python -m transcribe_core.server
```

服务绑定 `127.0.0.1` 的随机可用端口，启动后第一行标准输出为：

```
PORT=<端口号>
```

调用方（如 Tauri 外壳）读取这一行拿到实际端口。REST 接口：`POST /jobs`
提交任务、`GET /jobs` / `GET /jobs/{id}` 查询、`POST /jobs/{id}/rename`
改说话人名、`POST /jobs/{id}/replace_terms` 统一替换人名、
`GET /jobs/{id}/export?fmt=txt|srt|plain` 导出、`GET /models` /
`POST /models/{id}/download` / `POST /models/active` 管理模型；
`WS /ws/jobs/{id}` 订阅单个任务的实时进度推送。

## 跑测试

```bash
cd core

# 默认：跳过需要真实模型/音频的慢速集成测试
venv/bin/pytest -q -m "not slow"

# 慢速测试：需要真实 mlx-whisper / pyannote 模型已下载
HF_ENDPOINT=https://hf-mirror.com venv/bin/pytest -q -m slow
```

## 模块职责一览

| 模块 | 职责 |
|---|---|
| `transcript.py` | 转写稿数据模型：说话人重命名、人名候选与统一替换、导出 txt/srt/逐字稿 |
| `backend.py` | 推理后端抽象接口 `InferenceBackend` + 与推理框架无关的纯对齐/去重逻辑 |
| `mlx_backend.py` | `InferenceBackend` 的 Apple Silicon 实现：mlx-whisper 转写 + pyannote 说话人分离 |
| `faster_whisper_backend.py` | `InferenceBackend` 的 Windows/CPU 实现：faster-whisper `int8` 转写 + pyannote 说话人分离 |
| `backend_selection.py` | 按操作系统或 `WHOSAID_BACKEND` 选择并创建推理后端 |
| `models.py` | 模型注册表：内置模型清单、下载状态、当前启用模型，持久化到 `config.json` |
| `jobs.py` | 转写任务队列：串联 转写→分离→对齐→生成 Transcript，推进度，全局信号量保证单并发 |
| `server.py` | FastAPI 服务：REST + WebSocket，把上述组件装配成可被外壳调用的接口 |

## 并发策略

`jobs.py` 用模块级 `threading.Semaphore(1)`（`_infer_gate`）包住
`run_job` 的推理段，保证任意时刻至多一个任务在做 transcribe/diarize，
避免本机有限的算力/显存被多个任务同时抢占。任务仍可并发 `submit_async`
排队，只是推理段本身串行执行。

## 扩展平台

当前提供 `MlxBackend`（Apple Silicon）和 `FasterWhisperBackend`
（Windows x64 CPU）。后续 CUDA 或 Linux 后端继续实现
`InferenceBackend.transcribe` / `InferenceBackend.diarize`，并在
`backend_selection.py` 注册即可；`JobQueue` 和 API 层无需改动。

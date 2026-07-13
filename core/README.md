# transcribe-core

本地转写服务内核：把音频转写为带说话人标注的文字稿，通过 FastAPI 暴露
REST + WebSocket 接口。一期仅在本目录内独立运行、可用 curl/websocket
客户端测通；`create_app()` 暴露的接口即对外契约，Tauri 外壳（下一份计划）
直接消费本服务，不依赖本目录任何内部实现细节。

## 环境要求

- Python 3.13+
- **仅支持 Apple Silicon（M 系列）**：转写/说话人分离依赖 `mlx` /
  `mlx-whisper`，构建在 Metal 之上，Intel Mac 或其他平台无法运行推理相关
  测试与真实服务（纯逻辑模块如 `transcript.py`/`backend.py` 的对齐算法
  不受此限制）。

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
改说话人名、`GET /jobs/{id}/export?fmt=txt|srt` 导出、`GET /models` /
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
| `transcript.py` | 转写稿数据模型（`Segment`/`Transcript`）：说话人重命名、导出 txt/srt |
| `backend.py` | 推理后端抽象接口 `InferenceBackend` + 与推理框架无关的纯对齐/去重逻辑 |
| `mlx_backend.py` | `InferenceBackend` 的 Apple Silicon 实现：mlx-whisper 转写 + pyannote 说话人分离 |
| `models.py` | 模型注册表：内置模型清单、下载状态、当前启用模型，持久化到 `config.json` |
| `jobs.py` | 转写任务队列：串联 转写→分离→对齐→生成 Transcript，推进度，全局信号量保证单并发 |
| `server.py` | FastAPI 服务：REST + WebSocket，把上述组件装配成可被外壳调用的接口 |

## 并发策略

`jobs.py` 用模块级 `threading.Semaphore(1)`（`_infer_gate`）包住
`run_job` 的推理段，保证任意时刻至多一个任务在做 transcribe/diarize，
避免本机有限的算力/显存被多个任务同时抢占。任务仍可并发 `submit_async`
排队，只是推理段本身串行执行。

## 扩展平台

一期 `InferenceBackend` 只有 `MlxBackend`（Apple Silicon）一个实现。
要支持其他平台（如 Windows + CUDA、Linux 等），在 `backend.py` 中新增
一个实现 `InferenceBackend.transcribe` / `InferenceBackend.diarize` 的
子类（参考 `mlx_backend.py` 的写法），在 `server.py` 的 `main()`（或调用方
的组装代码）里按运行平台注入对应实现即可，`JobQueue`/`server.py` 的其余
逻辑无需改动。

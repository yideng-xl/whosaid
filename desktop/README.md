# whosaid Desktop（Tauri 外壳）

本地转写应用的桌面外壳：Tauri + SvelteKit + TypeScript。启动时拉起 `core/`
下的 Python 转写内核子进程，握手拿到端口后，前端直接走 `127.0.0.1` 的
REST/WS 与内核通信。本目录不含任何转写/说话人分离逻辑，纯 UI 与进程管理。

## 开发前置

- Node.js / npm
- Rust（`cargo`，本项目在 1.96 上验证）
- `core/` 下已建好 venv 并装好依赖（含 `websockets`，详见
  `core/README.md`）：

  ```bash
  cd ../core
  python3.13 -m venv venv
  venv/bin/pip install -i https://mirrors.aliyun.com/pypi/simple/ \
      fastapi "uvicorn[standard]" pydantic starlette httpx websockets \
      mlx mlx-whisper pyannote-audio \
      huggingface_hub pytest
  ```

- Apple Silicon 开发环境使用 MLX；Windows 10/11 x64 使用 faster-whisper
  CPU 后端。

完成的任务支持说话人改名，以及“候选推荐 + 人工确认”的人名统一替换；
替换会同时刷新正文、说话人显示名和所有导出结果。

## 安装依赖

```bash
cd desktop
npm install
```

## 启动开发模式

```bash
WHOSAID_PYTHON=$(pwd)/../core/venv/bin/python npm run tauri dev
```

`WHOSAID_PYTHON` 是外壳读取的 Python 解释器路径环境变量（见
`src-tauri/src/lib.rs` 的 `dev_python()`）；不设置时默认取
`../../core/venv/bin/python`（相对 `src-tauri/` 的 cwd）。若 `core/`
不在默认相对位置，还可用 `WHOSAID_CORE` 覆盖内核仓库根目录（影响
`PYTHONPATH` 与默认 `WHOSAID_PYTHON` 的推导）。

## 数据目录

内核的 `config.json` 与持久化数据落在：

- macOS：`~/Library/Application Support/whosaid`
- Windows：Tauri 返回的 `%APPDATA%` 应用数据目录

该目录在外壳启动时自动创建，同时作为子进程的 `cwd` 与
`WHOSAID_DATA_DIR` 环境变量传给内核。macOS 保留 v0.1.0 的历史目录，
避免升级后已有任务和配置丢失。

## 架构

```
┌─────────────────────────┐         spawn + 读 stdout "PORT=<n>"       ┌──────────────────────────┐
│  Tauri 外壳 (Rust)       │ ───────────────────────────────────────▶ │  Python 转写内核           │
│  src-tauri/src/          │                                          │  python -m                │
│    lib.rs   (setup/状态) │ ◀─────────────────────────────────────── │  transcribe_core.server   │
│    sidecar.rs(spawn/握手)│         握手拿到端口，存入 app 状态         │  (FastAPI, 127.0.0.1)     │
└───────────┬──────────────┘                                          └─────────────▲─────────────┘
            │ get_service_port (Tauri command)                                      │
            ▼                                                                       │
┌─────────────────────────┐                REST /jobs, /models ...                  │
│  前端 (SvelteKit)         │ ─────────────────────────────────────────────────────────┘
│  src/routes, src/lib     │                WS /ws/jobs/{id} 实时进度
└─────────────────────────┘
```

- 外壳启动（`setup` 钩子）时调用 `sidecar::spawn_service`，拉起
  `python -m transcribe_core.server`，`cwd` 为数据目录、`PYTHONPATH`
  指向 core 根（内核未 pip 安装，靠此让 `import transcribe_core` 生效）、
  同时透传 `WHOSAID_DATA_DIR` 与 `HF_ENDPOINT=https://hf-mirror.com`。
- 后台线程逐行读子进程 stdout 找 `PORT=<n>`，主线程 `recv_timeout`
  等待，约定超时 30 秒；超时或子进程提前退出（EOF）都视为启动失败，
  kill 掉子进程并返回 `Err`，避免首屏无限转圈。
- 握手拿到的端口存入 app 状态，前端通过 `get_service_port` 命令轮询
  拿到端口后，直接以 `http://127.0.0.1:<port>` 走 REST，`ws://` 走
  WebSocket 订阅任务进度，不再经过 Tauri IPC 转发业务数据。
- 退出：正常关窗（`WindowEvent::Destroyed`）与应用退出
  （`RunEvent::Exit`）都会 kill 一次子进程，重复调用无害；强杀/崩溃
  场景由 Python 内核自身的父进程看门狗兜底（`server.py` 检测父进程消失
  后自我了断），防止孤儿进程常驻。

## Apple Silicon 打包

先组装包内 Python、core 与 ffmpeg，再构建 `.app` 和 DMG：

```bash
./scripts/build-runtime.sh
npm run tauri build
```

运行时产物位于 `src-tauri/python/`、`src-tauri/core/` 和
`src-tauri/ffmpeg/`，均为本地构建产物，不进入 Git。模型权重不随安装包
分发，首次运行时由用户填写 Hugging Face 配置后下载。

当前安装包未做 Apple 签名与公证，首次打开需配合
`scripts/首次打开.command` 去除隔离属性。Apple Silicon `v0.1.0` 已通过
GitHub Release 发布；Apple 正式签名、公证与自动更新留到后续阶段。

## Windows x64 打包

Windows Runner 上执行：

```powershell
./scripts/build-runtime-windows.ps1
npm run tauri -- build --ci --bundles nsis
```

运行时使用 Python 3.13、faster-whisper CPU `int8`、pyannote CPU 和
ffmpeg。GitHub Actions 会先跑完整回归，再生成 NSIS 安装包；模型仍在
首次使用时下载，不随安装包分发。

## 已知约束

- 转写任务的分块暂停/续传（长音频分段处理、暂停后从断点续跑）为已知
  能力项，具体边界以 `core/` 内核实现与其测试为准。
- 说话人试听（按分离出的说话人播放对应音频片段辅助改名）为已知能力项。
- 孤儿进程兜底：外壳退出的正常/异常路径已尽量 kill 子进程，极端情况
  （外壳被强杀且未走到 `RunEvent::Exit`）由内核父进程看门狗自我了断
  兜底，而非外壳单侧保证。

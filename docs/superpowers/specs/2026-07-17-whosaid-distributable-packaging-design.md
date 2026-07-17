# 设计:whosaid 分发打包（Apple Silicon「发给同事」版）

日期:2026-07-17
状态:待确认

## 背景与目标

whosaid 桌面 app 的界面功能(二期)已基本完成,当前只有「本机个人版」——`whosaid.app` 靠编译期烘焙的绝对路径找到本机 `core/venv` 和模型缓存,**换台机器就失效**。

本设计要做的是**「发给同事、在别的 Apple Silicon Mac 上能装能用」的分发包**:自包含 Python 运行时 + 依赖,模型首次运行下载,不依赖收件人机器上有任何开发环境。

**明确不在本次范围**(列入后续):Intel Mac / Windows(需先做可插拔推理后端);真·代码签名+公证(本次不办 Apple 开发者账号)。

## 已确认决策(2026-07-17)

1. **目标机器**:仅 **Apple Silicon Mac**。收件人若有 Intel/Windows,本包盖不到,列后续。
2. **签名**:**不办 Apple 开发者账号、不公证**。用「首次一步」去 Gatekeeper 隔离 + 图文说明。
3. **门控模型(pyannote)**:**不由我们再分发**(规避 license 风险)。同事**自己注册 HF token**、自己在 HF 上同意 pyannote 各模型条款;我们做 **GitHub Pages 教程**引导。
4. **模型交付**:**全部首次运行下载**。app 包内只含运行时(不含模型)。
5. **Python 打包方式**:**方案 A —— 可重定位 Python(python-build-standalone)+ 全新安装依赖**(不拷 Homebrew venv,避免路径/ABI 坑)。
6. **分发载体**:**GitHub Release**(app 纯运行时 ~1.5G,在 2G/单文件上限内)。
7. **镜像**:默认官方 HF(门控 token 下载最可靠)+ app 内可选「镜像地址」(HF_ENDPOINT)设置 + 教程教 hf-mirror。

## 一、包内布局 + 路径解析改造

**包内布局**(`whosaid.app/Contents/Resources/`):
- `python/` —— 可重定位 CPython 3.13(arm64)+ 装好的依赖(mlx-whisper / pyannote.audio / torch / torchaudio 等)。
- `core/` —— `transcribe_core` 源码(纯 Python 包,作为资源打入)。

**路径解析(`desktop/src-tauri/src/lib.rs`)改成三态优先级**:
1. **运行时 env 覆盖**:`WHOSAID_PYTHON` / `WHOSAID_CORE`(dev、CI、排障用)——保留现状。
2. **打包态**:用 Tauri 资源目录 API(`app.path().resource_dir()`)定位 `Resources/python/bin/python3` 与 `Resources/core`。仅当二者存在才采用。
3. **dev 相对路径**:`current_dir()/../../core` 与其下 `venv/bin/python`——保留现状。

**移除**当前 `option_env!("WHOSAID_CORE")` 的编译期烘焙绝对路径兜底(那是仅对本机有效的个人版临时手段;分发态由「打包态」替代)。数据/配置仍落 `~/Library/Application Support/whosaid`。

**sidecar 不变**:仍 `python -m transcribe_core.server`,读 stdout `PORT=<n>` 握手,`PYTHONPATH` 指向解析出的 core 根。

## 二、Python 运行时组装(构建步骤)

新增构建脚本 `desktop/scripts/build-runtime.sh`(或等价):
1. 下载 **python-build-standalone** 的 arm64、CPython **3.13** 独立发行版,解压到 `desktop/src-tauri/resources/python/`。
2. 用该解释器按 **pinned 版本**(见下)`pip install` 依赖到其 site-packages —— **全新安装,不拷现有 Homebrew venv**。
3. 瘦身:删 `__pycache__`、`tests/`、`*.dist-info` 中的非必需项、无关的 `.a`/静态库,压缩体积。
4. 把 `core/transcribe_core`(纯源码)复制到 `desktop/src-tauri/resources/core/transcribe_core/`。
5. `tauri.conf.json` 的 `bundle.resources` 声明 `resources/python/**` 与 `resources/core/**`,使其打进 `.app/Contents/Resources/`。

**依赖版本锚定**:`core/pyproject.toml` 现声明 `mlx-whisper`、`pyannote.audio`、`torch`、`torchaudio` 等但未锁版本。构建脚本从当前本机 venv **导出 pinned 版本**(`pip freeze` 过滤)作为构建输入,保证装进包的与已验证跑通的一致。

**验证点**:python-build-standalone 3.13 的 wheel ABI 与本机 Homebrew 3.13 兼容(同 CPython 3.13 版本,manylinux/macosx arm64 wheel 通用),torch/mlx 预编译轮子应可直接加载;若个别包有平台特异性,构建脚本报错即止,不静默。

## 三、首次运行:HF token 输入 + 模型下载

**HF token 输入 UI**:模型管理页新增一个「HF 访问令牌」输入框,保存到 `~/Library/Application Support/whosaid/config.json`(新增字段 `hf_token`)。服务端下载模型时,优先用该 token(注入 `HF_TOKEN` 环境或 `Pipeline.from_pretrained(token=...)`),取代当前「靠本机 huggingface-cli 缓存 token」的隐式依赖。

**可选镜像**:同页可选填「镜像地址」,存 `config.json` 的 `hf_endpoint`,下载时设 `HF_ENDPOINT`。默认空(官方 HF)。

**首次流程**:app 启动检测「当前 active 模型是否已在缓存」(复用 server.py 现有 `try_to_load_from_cache` 判断)。未就绪 → 界面提示「填 HF token → 下载模型」。用**现有下载能力**(server.py 的 `snapshot_download` 路径 + 前端模型管理下载按钮/进度)拉:
- **Belle**(`mlx-community/belle-whisper-large-v3-zh-punct-fp16`,免 token)
- **pyannote**(diarize 用的门控模型,用同事 token)

`HF_HOME` 指到 `~/Library/Application Support/whosaid/hf-cache`(app 管理,和现有 `~/.cache/whosaid/mlx-fix` 兼容)。

**错误处理**:token 无效 / 未同意门控条款 → 后端把 HF 的 401/403 错误透传成前端可读提示(「令牌无效或未同意 pyannote 条款,见教程」+ 教程链接),不静默失败。

## 四、Gatekeeper 首次一步(不签名)

DMG 内附 **`首次打开.command`**(可双击执行的 shell 脚本):
- 对 `/Applications/whosaid.app`(或 DMG 内 app)执行 `xattr -dr com.apple.quarantine`,去掉隔离属性。
- 提示用户先把 app 拖进「应用程序」,再运行本脚本,之后即可正常双击。
- 脚本自身也可能被 Gatekeeper 拦 → 教程里给「右键→打开」运行 `.command` 的一次性说明,或直接给等价的一行终端命令兜底。

配图文说明(并入 GitHub Pages 教程)。

## 五、GitHub Pages 教程

一个 docs 站(GitHub Pages,源可放 whosaid 仓库 `/docs` 或 `gh-pages` 分支),内容:
1. **下载安装**:从 GitHub Release 下 DMG → 拖 app 进「应用程序」。
2. **首次打开**:运行 `首次打开.command` 去隔离(截图步骤)。
3. **注册 HF token**:注册 huggingface.co 账号 → 建 Read token → **逐个打开 pyannote 门控模型页点「Agree」同意条款**(附直链:segmentation-3.0 / speaker-diarization-community-1 等 diarize 实际用到的)。
4. **填 token + 下载模型**:把 token 粘进 app 模型管理页 → 点下载 → 等 Belle+pyannote 下完(~3G,国内同事可填 hf-mirror 镜像地址加速)。
5. **开始用**:拖音频进窗口转写。

## 六、分发载体

- **GitHub Release**:上传 `whosaid_<ver>_aarch64.dmg`(纯运行时,~1.5G,在 2G 上限内)+ `首次打开.command`(或并入 DMG)。
- Release 说明里链到 GitHub Pages 教程。

## 七、范围与约束(YAGNI)

- **仅 Apple Silicon**。Intel/Windows 不在本次。
- **不签名/不公证**;不追求「双击零提示」,接受首次一步。
- **不再分发门控模型**;同事自带 token。
- 不引入新的转写/分人业务逻辑;仅动:打包组装、路径解析、HF token 输入 UI + 下载接线、脚本、文档。
- 复用现有模型下载 / 缓存判断 / 进度机制,不重写。

## 八、验证

- **自包含性**(核心):临时把 dev 的 `core/`、`core/venv`、`~/.cache/huggingface`(或 HF_HOME)改名/隐藏,运行打包出的 `.app`,确认它**只用包内 python + core、模型能从零下载**——证明脱离本机开发环境仍可跑。理想再在一台干净的 Apple Silicon Mac(或新用户账户)上实测一遍。
- **首次流程**:清空 app 数据目录 + 模型缓存 → 走一遍填 token → 下载 → 转写。
- **构建可复现**:`build-runtime.sh` 从零跑通,产物体积在预期内。
- 现有 `npm run check` 0/0、后端 pytest、前端 vitest 保持绿(HF token UI 若加前端逻辑需覆盖)。

## 影响文件(预估)

- 新增:`desktop/scripts/build-runtime.sh`(运行时组装)、`desktop/scripts/首次打开.command`(去隔离)、GitHub Pages 教程(`docs/site/` 或 `gh-pages`)。
- 改:`desktop/src-tauri/src/lib.rs`(三态路径解析,移除烘焙兜底)、`desktop/src-tauri/tauri.conf.json`(`bundle.resources`)、`core/transcribe_core/server.py`(HF token/endpoint 注入下载、错误透传)、`core/transcribe_core/mlx_backend.py`(token 来源改为显式)、`desktop/src/lib/ModelManager.svelte`(HF token/镜像输入框)、`desktop/src/lib/api.ts`(存取 token 接口)、`core/pyproject.toml`(锁定 pinned 版本,供构建输入)。
- 文档:`README.md` 路线图「二期尾·分发打包」状态推进。

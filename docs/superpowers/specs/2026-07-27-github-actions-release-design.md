# 设计：GitHub Actions 自动构建并发布 macOS 安装包

日期：2026-07-27  
状态：已确认，待实现

## 背景

`whosaid v0.1.0` 已在本机完成 Apple Silicon `.app` 与 DMG 构建，并通过
测试、挂载和完整性校验。本机向 GitHub Release 上传约 434 MB 的 DMG
时，上行速度长期只有约 40 KB/s，直接上传需要数小时。

仓库为公开仓库，可以使用 GitHub 标准 Apple Silicon macOS runner。把
Release 构建移到 GitHub 侧，可以避开本机大文件上行，同时形成后续
Windows 自动打包的基础。

## 目标

1. 提供一套手动触发的 GitHub Actions 工作流。
2. 在 Apple Silicon macOS runner 上从仓库源码重新构建自包含 DMG。
3. 将 DMG、首次打开辅助脚本和 SHA-256 校验文件上传到指定 Release。
4. 首次用于完成现有 `v0.1.0` 草稿 Release。
5. 构建失败时保持 Release 为草稿，不公开残缺附件。

## 非目标

- 本次不做 Windows 打包；只给后续 Windows 工作流预留相同的版本输入和
  Release 上传边界。
- 不做 Apple 开发者签名、公证和自动更新。
- 不在 push 或 pull request 时自动发布。
- 不把 Hugging Face 模型权重放进安装包。
- 不实现人名统一替换。

## 方案

新增 `.github/workflows/release-macos.yml`，只允许
`workflow_dispatch` 手动触发。输入项：

- `tag`：Release Tag，例如 `v0.1.0`。
- `publish`：是否在附件上传并校验后把草稿转为公开 Release。

工作流运行在 `macos-latest`。公开仓库的该标签对应标准 Apple Silicon
runner，不使用收费的大型 runner。

### 构建流程

1. 检出触发时所选分支或 Tag 的源码。
2. 安装 Node.js 与 Rust，执行 `desktop/npm ci`。
3. 执行 `desktop/scripts/build-runtime.sh`，组装包内 Python、core、
   ffmpeg 和 ffprobe。
4. 运行后端非 slow 测试、前端测试、Svelte 检查和 Rust 测试。
5. 执行 Tauri CI 模式构建，生成 Apple Silicon `.app` 与 DMG。
6. 将 `desktop/scripts/首次打开.command` 压缩为
   `whosaid_<version>_first-open.zip`。
7. 生成 `SHA256SUMS.txt`。

### Release 流程

工作流使用 GitHub 自动提供的 `GITHUB_TOKEN`，权限限定为
`contents: write`：

1. 查找输入 Tag 对应的 Release。
2. Release 不存在时创建草稿；存在时复用。
3. 以覆盖模式上传 DMG、首次打开压缩包和校验文件。
4. 重新查询远端资产，核对三个文件均存在且大小大于零。
5. 仅当 `publish=true` 且所有检查通过时公开 Release，并标记为最新版。

第一次运行复用当前已有的 `v0.1.0` 草稿及发布说明。

## 失败处理

- 依赖下载、测试、构建或上传任一步失败，工作流立即失败。
- 失败时不执行公开步骤，Release 保持草稿。
- 上传使用覆盖模式，重跑不会产生同名重复附件。
- 不自动删除已有可用附件；只有同名文件在本次上传成功后被替换。
- 工作流日志输出构建产物路径、文件大小和 SHA-256，不输出 Token。

## 安全边界

- 工作流不接收任意 shell 命令或外部下载地址作为输入。
- Tag 只用于查找 Release，不拼接到执行命令中。
- 发布权限只授予当前单个 Job。
- 不引入个人访问令牌或新的仓库 Secret。
- `GITHUB_TOKEN` 由 GitHub 托管，只用于当前仓库 Release。

## 验收

1. 工作流可从 GitHub Actions 页面手动触发。
2. Runner 架构为 `arm64`。
3. 后端、前端和 Rust 测试全部通过。
4. Tauri 生成 `whosaid_0.1.0_aarch64.dmg`。
5. Release 中存在：
   - `whosaid_0.1.0_aarch64.dmg`
   - `whosaid_0.1.0_first-open.zip`
   - `SHA256SUMS.txt`
6. 远端 DMG 大小大于 400 MB，完整性校验值与工作流日志一致。
7. `publish=true` 时，`v0.1.0` 从草稿变成公开最新版。
8. Release Tag 指向触发构建的源码提交。

## 后续 Windows 复用

Windows 阶段新增独立的 `release-windows.yml`，复用相同的 `tag`、
`publish` 输入和 Release 资产校验规则。Windows 推理后端、Python
运行时和 Tauri 安装包在独立工作流中构建，不把两个平台塞进同一个 Job。

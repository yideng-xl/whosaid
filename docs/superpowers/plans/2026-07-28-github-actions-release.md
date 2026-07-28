# GitHub Actions 自动发布 macOS 安装包 Implementation Plan

日期：2026-07-28  
对应设计：`docs/superpowers/specs/2026-07-27-github-actions-release-design.md`

## 目标

新增一套只允许手动触发的 macOS Release 工作流，在 GitHub 标准
Apple Silicon runner 上完成运行时组装、测试、DMG 构建、附件上传与
Release 公开，并用它发布 `v0.1.0`。

## Task 1：工作流入口与权限

新增 `.github/workflows/release-macos.yml`：

- 触发方式仅为 `workflow_dispatch`。
- 输入 `tag`，默认 `v0.1.0`。
- 输入 `publish`，布尔值，默认 `false`。
- Job 使用 `macos-latest`，超时 90 分钟。
- 权限仅为 `contents: write`。
- 增加并发组，避免同一仓库同时跑两次 macOS 发布。
- 首步打印 `uname -m`，非 `arm64` 立即失败。

## Task 2：组装运行时并跑回归

依次执行：

1. `actions/checkout@v4`
2. `actions/setup-node@v4`，Node 22，启用 npm 缓存
3. `dtolnay/rust-toolchain@stable`
4. `desktop/npm ci`
5. `desktop/scripts/build-runtime.sh`
6. 用包内 Python 跑后端非 slow 测试
7. `npm test`
8. `npm run check`
9. `cargo test`

测试失败时 Job 立即退出，不进入打包和发布步骤。

## Task 3：构建并准备 Release 资产

1. Rust 测试后执行 `cargo clean`，减少 14 GB runner 磁盘压力。
2. 用 Tauri CI 模式构建 `.app` 与 DMG。
3. 检查唯一 DMG 存在且大小大于 400 MB。
4. 把 `首次打开.command` 压缩为
   `whosaid_0.1.0_first-open.zip`，不携带上级目录。
5. 为 DMG 和辅助脚本生成 `SHA256SUMS.txt`。
6. 输出三个资产的路径、大小和校验值。

## Task 4：上传并公开 Release

1. 用 `gh release view` 查找输入 Tag。
2. Release 不存在时，基于当前 `GITHUB_SHA` 创建草稿。
3. 用 `gh release upload --clobber` 上传三个资产。
4. 用 GitHub API 重新读取资产列表，逐项校验：
   - 文件名准确；
   - 状态为 `uploaded`；
   - DMG 大于 400 MB；
   - 另外两个文件大于零。
5. `publish=true` 时执行
   `gh release edit <tag> --draft=false --latest --target <sha>`。

## Task 5：本地与远端验收

实现后：

1. 本地检查 YAML 基本结构、shell 语法和 `git diff --check`。
2. 提交并推送设计、计划和工作流。
3. 手动触发：

   ```bash
   gh workflow run release-macos.yml \
     --repo yideng-xl/whosaid \
     --ref main \
     -f tag=v0.1.0 \
     -f publish=true
   ```

4. 监控到工作流结束；失败则根据日志修复并重跑。
5. 查询公开 Release，确认 Tag、提交、附件数量、文件大小和 Latest 状态。
6. 下载 `SHA256SUMS.txt`，与 Release 资产列表相互核对。

## 完成标准

- GitHub Actions 工作流成功。
- `v0.1.0` 为公开 Latest Release。
- Release 有 DMG、首次打开压缩包、SHA256SUMS 三个附件。
- DMG 由本次 GitHub 工作流从当前源码构建。
- 本地已有的三份未跟踪设计/计划文档未被误提交。

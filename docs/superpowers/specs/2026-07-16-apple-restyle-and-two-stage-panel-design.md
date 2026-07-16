# 设计:Apple/macOS 风格重塑 + 详情面板两阶段(转文字/分人)结果视图

日期:2026-07-16
状态:待确认

## 背景与目标

whosaid 是 macOS 桌面 app(Tauri + Svelte)。用户要求两件事,合为一次详情面板重设计 + 全局视觉重塑:

1. **两阶段可视化**:一条记录本质是两阶段——**① 语音转文字**(transcribe,出纯文字)→ **② 分离说话人**(diarize+align,出分人稿)。要把它做成"进度 + 结果切换二合一":进行中显示阶段进度;① 完成即可看纯文字;完成的任务可点击分别查看 **①纯文字稿** 与 **②分人稿**。
2. **Apple/macOS 原生视觉**:参考 awesome-design-md 的 Apple 规范 + macOS HIG,做成系统原生质感。

数据现成:同一份 `job.transcript`,①= `plain_text()`(按时间顺读、无说话人),②= `to_txt()`(说话人分组)。无需重跑,只是两种呈现。

## 一、两阶段面板(核心交互)

详情面板顶部放一个 **macOS 分段控件(Segmented Control)**:`[ ① 转文字 | ② 分人 ]`,兼作"阶段状态"与"结果切换"。

**进行中状态:**
- 转写阶段(running & progress<0.85):① 高亮为"进行中" + 转写进度(块 x/y),② 置灰"待分离";主区流式显示纯文字(随块出现)。
- 分离阶段(running & progress≥0.85):① 打勾"完成"(**可点,看纯文字**),② 高亮为"分离中"(流动动画 + 提示「长录音可能几分钟、不可中断」);主区默认显示纯文字。
- 已暂停/失败:分段控件按当前 phase 呈现,不崩。

**已完成(done):**
- ① 与 ② 均打勾可点。**默认停在 ②(分人稿)**——即现有的说话人分组视图 + 改名 + 试听。
- 点 **①** → 显示纯文字稿(无说话人、时间顺序段落);点 **②** → 回到分人稿。
- 导出按钮(文字稿/字幕稿)保持不变。

**后端**:`GET /jobs/{id}` 已返回 `txt`(done 时是 `to_txt()`,否则 `plain_text()`)。新增返回 `plain_txt`(始终 `plain_text()`,供①视图),`txt` 语义不变。前端据当前分段选择渲染 `plain_txt`(①)或说话人视图(②)。

## 二、Apple/macOS 视觉规范(全局 token 化)

散落各组件的硬编码色统一成一套 CSS 变量(`:root` + `data-theme` 双主题),取值参考 awesome-design-md Apple + macOS App 实用尺寸:

**颜色**
| token | 浅色 | 深色 |
|---|---|---|
| `--accent`(Action Blue) | `#0066cc` | `#0a84ff` |
| `--focus`(焦点环) | `#0071e3` | `#0a84ff` |
| `--bg`(窗口/主区) | `#ffffff` | `#1e1e1e` |
| `--sidebar-bg`(parchment) | `#f5f5f7` | `#252527` |
| `--card` | `#ffffff` | `#2a2a2c` |
| `--fg`(近黑墨) | `#1d1d1f` | `#f5f5f7` |
| `--muted`(次要文字) | `#7a7a7a` | `#98989d` |
| `--hairline`(发丝分隔) | `#e0e0e0` | `#3a3a3c` |
| `--danger` | `#ff3b30` | `#ff453a` |
| 选中行填充 | `--accent` @ 12% 或实心 accent(见下) | 同 |

**字体**:`-apple-system, "SF Pro Text", "SF Pro Display", system-ui, sans-serif`。App 实用尺寸(非营销页 17px):正文 13px、次要 12px、小标题 15/600、大标题 20/600;字距近 0。

**圆角**:按钮 `6px`、卡片/面板 `10px`、弹窗 `12px`、胶囊/分段控件 `7px`(macOS 风)、圆形控件 `pill`。

**间距**:8px 基:4/8/12/16/24/32。

**深度**:主界面用发丝线分隔而非重阴影;仅浮层(弹窗/气泡)用柔和阴影 `0 8px 30px rgba(0,0,0,.16)`;弹窗遮罩 40–50% 黑。

**动效**:150–250ms;按钮/卡片按压 `transform: scale(0.97)`;选中/hover 平滑过渡;尊重 `prefers-reduced-motion`。

**控件**
- 主按钮:实心 accent、白字、圆角 6、按压缩放。次按钮:描边 accent 透明底。危险:danger 色。
- **侧栏选中行**:Finder 式实心 accent 圆角高亮(白字)。
- **分段控件**:macOS 风——浅灰底容器 + 选中段白色凸起(浅)/深灰凸起(深)+ 细阴影。
- **图标**:去 emoji(⚙️🌙☀️✕▶⏸),换 SF Symbols 风的**内联线性 SVG**(stroke 1.5,统一尺寸),放 `src/lib/icons.ts` 或内联组件。

**可访问性**:文字对比≥4.5:1(深浅各自验);焦点环可见(`--focus`);图标按钮加 `aria-label`;色彩不作唯一信息载体。

## 三、毛玻璃侧栏(vibrancy)—— 真 macOS vibrancy(用户已选)

用 Rust `window-vibrancy` crate 做真正的 NSVisualEffectView 磨砂:
- `Cargo.toml` 加 `window-vibrancy` 依赖;Tauri setup(lib.rs/main.rs)里 `apply_vibrancy(&window, NSVisualEffectMaterial::Sidebar, None, None)`。
- `tauri.conf.json` 窗口 `"transparent": true`(macOS 需要 `macOSPrivateApi: true`)。
- CSS:`html,body` 背景透明;**主区(content)给不透明底**、**侧栏底透明**(让 vibrancy 透出)。发丝分隔线保留。
- 需 `npm run tauri dev` 重新编译 + 重启一次。深浅两主题各自选合适 material(Sidebar 材质本身随系统外观自适应)。

## 四、范围与约束(YAGNI)

- **只动展示层**,不碰转写/暂停/续跑/重新分人/试听/删除等业务逻辑。
- 双主题都要过 `svelte-check` 0/0 + 现有 vitest 11 绿。
- 不引入运行时新依赖(图标用内联 SVG,不加图标库);字体用系统栈,不引 webfont(离线/CSP 友好)。
- 分段控件的"结果切换"纯前端;后端仅加 `plain_txt` 一个只读字段。

## 影响文件(预估)
- 新增:`desktop/src/lib/tokens.css`(或 `app.css` 全局 token)、`desktop/src/lib/icons.ts`(内联 SVG)。
- 新增/改:`desktop/src/lib/StageSwitcher.svelte`(分段控件)。
- 改:`TranscriptView.svelte`(两阶段面板 + ①/②视图切换 + 复用流动动画)、`Sidebar.svelte`(选中行 + 徽标 + token)、`+page.svelte`(全局 token/字体/背景)、`ModelManager.svelte`、`SpeakerRename.svelte`(token + 控件 + 图标)。
- 后端:`server.py`(get_job 加 `plain_txt`)、`api.ts`(JobDetail 加 `plain_txt`)。

## 已确认决策(2026-07-16)
1. 侧栏毛玻璃 = **真 macOS vibrancy**(Rust window-vibrancy + 重编译)。
2. 完成任务默认视图 = **② 分人稿**(点 ① 看纯文字稿)。

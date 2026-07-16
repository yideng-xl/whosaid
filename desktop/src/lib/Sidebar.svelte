<script lang="ts">
  import type { JobSummary } from "./api";
  import Icon from "./Icon.svelte";

  // 呈现型组件：任务列表由 +page 统一持有并传入，本组件只负责渲染与派发点击。
  let {
    jobs = [],
    selectedJobId = null,
    dragging = false,
    onSelect,
    onOpenModels,
    onDelete,
    currentTheme = "light",
    onToggleTheme,
  }: {
    jobs: JobSummary[];
    selectedJobId: string | null;
    dragging: boolean;
    onSelect: (id: string) => void;
    onOpenModels: () => void;
    onDelete: (id: string) => void;
    // 当前主题与切换回调：由 +page 统一持有并下发
    currentTheme?: "light" | "dark";
    onToggleTheme?: () => void;
  } = $props();

  function basename(p: string): string {
    const parts = p.split(/[\\/]/);
    return parts[parts.length - 1] || p;
  }

  const STATUS: Record<string, { label: string; cls: string }> = {
    queued: { label: "排队中", cls: "queued" },
    running: { label: "转写中", cls: "running" },
    done: { label: "完成", cls: "done" },
    failed: { label: "失败", cls: "failed" },
    paused: { label: "已暂停", cls: "paused" },
  };

  function statusOf(s: string) {
    return STATUS[s] ?? { label: s, cls: "queued" };
  }

  function badgeFor(job: JobSummary) {
    // 分人阶段（running 且进度进入 0.85+）显示「分人中」，避免与听写混淆
    if (job.status === "running" && job.progress >= 0.85)
      return { label: "分人中", cls: "running" };
    return statusOf(job.status);
  }

  // 按「拖入时间」把任务分组：今天/昨天/具体日期，组内与组间都倒序（最新在上）
  function dayLabel(ts: number): string {
    const d = new Date(ts * 1000);
    const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const diff = Math.round((startOf(new Date()) - startOf(d)) / 86400000);
    if (diff <= 0) return "今天";
    if (diff === 1) return "昨天";
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    return `${d.getFullYear()}-${mm}-${dd}`;
  }

  const grouped = $derived.by(() => {
    const sorted = [...jobs].sort((a, b) => (b.created_at ?? 0) - (a.created_at ?? 0));
    const groups: { label: string; items: JobSummary[] }[] = [];
    for (const j of sorted) {
      const label = dayLabel(j.created_at ?? 0);
      const last = groups[groups.length - 1];
      if (!last || last.label !== label) groups.push({ label, items: [j] });
      else last.items.push(j);
    }
    return groups;
  });
</script>

<aside class="sidebar">
  <div class="drop-hint" class:active={dragging}>
    把音频文件拖进窗口开始转写
  </div>

  <div class="job-list">
    {#if jobs.length === 0}
      <div class="empty">还没有任务</div>
    {/if}
    {#each grouped as group (group.label)}
      <div class="group-label">{group.label}</div>
      {#each group.items as job (job.id)}
        <!-- 用 div+role 而非 button：删除键是嵌套 button，button 内不可嵌 button（非法 HTML，
             Chromium 会重排导致 ✕ 错位/点击失效）。role+tabindex+键盘处理保留可访问性。 -->
        <div
          class="job"
          role="button"
          tabindex="0"
          class:selected={job.id === selectedJobId}
          onclick={() => onSelect(job.id)}
          onkeydown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(job.id); } }}
        >
          <div class="job-top">
            <span class="name" title={job.audio_path}>{basename(job.audio_path)}</span>
            <span class="badge {badgeFor(job).cls}">{badgeFor(job).label}</span>
            {#if job.status !== "running" && job.status !== "queued"}
              <button
                class="del"
                aria-label="删除任务"
                title="删除任务"
                onclick={(e) => { e.stopPropagation(); onDelete(job.id); }}
              >
                <Icon name="close" size={12} />
              </button>
            {/if}
          </div>
          {#if job.status === "running" || job.status === "queued"}
            {#if job.status === "running" && job.progress >= 0.85}
              <!-- 分人阶段无细粒度进度、可能耗时数分钟：用不确定态流动条，
                   避免进度条死停在 85% 让用户误以为卡死 -->
              <div class="bar indeterminate"><div class="stripe"></div></div>
            {:else}
              <div class="bar"><div class="fill" style="width:{Math.round(job.progress * 100)}%"></div></div>
            {/if}
          {/if}
          {#if job.status === "failed" && job.error}
            <div class="err">{job.error}</div>
          {/if}
        </div>
      {/each}
    {/each}
  </div>

  <div class="footer">
    <button class="models-entry" aria-label="模型管理" onclick={onOpenModels}>
      <Icon name="gear" size={14} />
      <span>模型管理</span>
    </button>
    <button
      class="theme-toggle"
      aria-label={currentTheme === "dark" ? "切换到浅色模式" : "切换到深色模式"}
      title="切换深色/浅色"
      onclick={onToggleTheme}
    >
      <Icon name={currentTheme === "dark" ? "sun" : "moon"} size={16} />
    </button>
  </div>
</aside>

<style>
  /* 侧栏容器：背景/分隔线全部走全局 token，深浅主题由 tokens.css 的 data-theme 统一驱动，
     组件内不再重复定义任何深色覆盖。 */
  .sidebar {
    width: 260px;
    min-width: 260px;
    height: 100vh;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    border-right: 1px solid var(--hairline);
    background: var(--sidebar-bg);
    padding: var(--space-3);
    gap: var(--space-2);
  }
  .drop-hint {
    border: 1.5px dashed var(--hairline);
    border-radius: var(--radius-card);
    padding: 14px var(--space-2);
    text-align: center;
    font-size: 12px;
    color: var(--muted);
    transition: all 0.15s;
  }
  .drop-hint.active {
    border-color: var(--accent);
    background: color-mix(in srgb, var(--accent) 10%, transparent);
    color: var(--accent);
  }
  .job-list {
    flex: 1;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }
  .empty {
    color: var(--muted);
    font-size: 12px;
    text-align: center;
    padding: 20px 0;
  }
  .group-label {
    font-size: 11px;
    color: var(--muted);
    padding: var(--space-2) 2px 2px;
    font-weight: 600;
  }
  .group-label:first-child { padding-top: 0; }

  /* Finder 式列表行：默认透明、无边框，hover 出现浅灰底；选中态见 .job.selected。
     hover 底色用 currentColor(即 --fg)按 8% 混合透明，浅色主题下 --fg 近黑得到浅灰，
     深色主题下 --fg 近白得到浅亮灰——一份公式两个主题都成立，无需额外深色覆盖。 */
  .job {
    text-align: left;
    background: transparent;
    border: none;
    border-radius: var(--radius-card);
    padding: var(--space-2) var(--space-3);
    cursor: pointer;
    font: inherit;
    color: var(--fg);
    transition: background 0.15s, color 0.15s;
  }
  .job:hover:not(.selected) {
    background: color-mix(in srgb, var(--fg) 8%, transparent);
  }
  .job.selected {
    background: var(--accent);
    color: #fff;
  }
  .job-top { display: flex; align-items: center; gap: var(--space-2); }
  .name {
    flex: 1;
    font-size: 13px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* 徽标：Apple 风小胶囊，低饱和底 + 同色系文字。除「失败」复用全局 --danger 外，
     其余状态色未在全局 token 中定义，用 color-mix 生成透明底以自动适配两套主题背景。 */
  .badge {
    font-size: 11px;
    padding: 1px 7px;
    border-radius: 999px;
    white-space: nowrap;
  }
  .badge.queued {
    background: color-mix(in srgb, var(--muted) 18%, transparent);
    color: var(--muted);
  }
  .badge.running {
    background: color-mix(in srgb, var(--accent) 16%, transparent);
    color: var(--accent);
  }
  .badge.done {
    background: color-mix(in srgb, #2c8a4b 16%, transparent);
    color: #2c8a4b;
  }
  .badge.failed {
    background: color-mix(in srgb, var(--danger) 16%, transparent);
    color: var(--danger);
  }
  .badge.paused {
    background: color-mix(in srgb, #b9711a 18%, transparent);
    color: #b9711a;
  }
  /* 选中行反白：名字继承父级白字，徽标改半透明白底 + 白字保持可读对比 */
  .job.selected .badge {
    background: rgba(255, 255, 255, 0.25);
    color: #fff;
  }
  .del {
    flex-shrink: 0;
    display: inline-flex;
    align-items: center;
    background: transparent;
    border: none;
    padding: 2px;
    line-height: 1;
    color: var(--muted);
    cursor: pointer;
    opacity: 0;
    transition: opacity 0.12s, color 0.12s;
  }
  .job:hover .del { opacity: 1; }
  .del:hover { color: var(--danger); }
  .job.selected .del { color: rgba(255, 255, 255, 0.8); }
  .job.selected .del:hover { color: #fff; }
  .bar {
    margin-top: 6px;
    height: 4px;
    border-radius: 3px;
    background: var(--hairline);
    overflow: hidden;
  }
  .fill {
    height: 100%;
    background: var(--accent);
    transition: width 0.25s;
  }
  /* 分人阶段不确定态进度条：一条高亮色块来回滑动，替代停在 85% 的固定宽度 */
  .bar.indeterminate { position: relative; overflow: hidden; }
  .bar.indeterminate .stripe {
    position: absolute;
    top: 0;
    left: 0;
    height: 100%;
    width: 40%;
    background: var(--accent);
    border-radius: 3px;
    animation: indeterminate 1.15s ease-in-out infinite;
  }
  @keyframes indeterminate {
    0% { transform: translateX(-110%); }
    100% { transform: translateX(360%); }
  }
  /* 选中行内的进度条改用半透明白，避免同色系 accent 与卡片底色糊在一起 */
  .job.selected .bar { background: rgba(255, 255, 255, 0.3); }
  .job.selected .fill,
  .job.selected .bar.indeterminate .stripe { background: #fff; }
  .err {
    margin-top: 4px;
    font-size: 11px;
    color: var(--danger);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .job.selected .err { color: #fff; }
  .footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-top: 1px solid var(--hairline);
    padding-top: var(--space-2);
  }
  .models-entry {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    text-align: left;
    background: transparent;
    border: none;
    padding: 4px 4px 2px;
    cursor: pointer;
    font: inherit;
    font-size: 13px;
    color: var(--muted);
  }
  .models-entry:hover { color: var(--accent); }
  .theme-toggle {
    flex-shrink: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: transparent;
    border: none;
    padding: var(--space-1) 6px;
    cursor: pointer;
    color: var(--fg);
    border-radius: var(--radius-btn);
    transition: background 0.12s;
  }
  .theme-toggle:hover { background: color-mix(in srgb, var(--accent) 12%, transparent); }
</style>

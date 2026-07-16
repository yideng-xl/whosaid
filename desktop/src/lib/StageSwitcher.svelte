<script lang="ts">
  // macOS 风两段分段控件：兼作「①转文字/②分人」阶段状态指示 + 结果切换二合一。
  // 呈现型组件：状态与选中项均由父组件（T6 详情面板）持有并下发，本组件只负责渲染与派发点击。
  import Icon from "./Icon.svelte";

  let { stage1State, stage2State, active, onSelect }: {
    stage1State: "active" | "done" | "pending";
    stage2State: "active" | "done" | "pending";
    active: 1 | 2;
    onSelect: (n: 1 | 2) => void;
  } = $props();

  const SEGMENTS: { n: 1 | 2; label: string }[] = [
    { n: 1, label: "① 转文字" },
    { n: 2, label: "② 分人" },
  ];

  function stateOf(n: 1 | 2): "active" | "done" | "pending" {
    return n === 1 ? stage1State : stage2State;
  }

  // aria-label 按状态拼出可读描述，供屏幕阅读器区分「已完成/进行中/待处理」，
  // 不依赖颜色这一单一信息载体。
  function ariaLabelOf(n: 1 | 2, label: string, state: "active" | "done" | "pending"): string {
    if (state === "done") return `${label}，已完成，点击查看`;
    if (state === "active") return n === 2 ? `${label}，分离中` : `${label}，进行中`;
    return `${label}，待处理`;
  }

  // 仅 done 段可点：active 段（进行中）尚无产出，点击会切到还没内容的视图，
  // 其"选中显示"完全由父组件下发的 active prop 控制，不需要点击触发；pending 段同样拦截。
  function handleClick(n: 1 | 2, state: "active" | "done" | "pending") {
    if (state !== "done") return;
    onSelect(n);
  }
</script>

<div class="stage-switcher">
  {#each SEGMENTS as seg (seg.n)}
    {@const state = stateOf(seg.n)}
    <button
      type="button"
      class="seg"
      class:selected={active === seg.n}
      class:is-active={state === "active"}
      class:is-pending={state === "pending"}
      disabled={state === "pending"}
      aria-disabled={state === "pending"}
      aria-label={ariaLabelOf(seg.n, seg.label, state)}
      onclick={() => handleClick(seg.n, state)}
    >
      <span class="label">{seg.label}</span>
      {#if state === "done"}
        <Icon name="check" size={14} />
      {:else if state === "active" && seg.n === 2}
        <!-- ②分离中：纯 CSS 旋转小圆环，表示后台正在分离说话人 -->
        <span class="spinner" aria-hidden="true"></span>
      {/if}
    </button>
  {/each}
</div>

<style>
  /* 外层浅灰容器：直接用 --hairline 做底色，浅色下是浅灰、深色下是深灰，
     两套主题各自都落在 macOS 分段控件"未选中轨道"的观感区间内，无需再叠加透明度。 */
  .stage-switcher {
    display: flex;
    gap: 2px;
    padding: 2px;
    background: var(--hairline);
    border-radius: var(--radius-seg);
  }

  .seg {
    flex: 1;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 4px;
    border: none;
    background: transparent;
    color: var(--fg);
    font: inherit;
    font-size: 12px;
    white-space: nowrap;
    padding: 5px 10px;
    border-radius: calc(var(--radius-seg) - 2px);
    cursor: pointer;
    transition: background 0.18s ease, color 0.18s ease, box-shadow 0.18s ease, transform 0.12s ease;
  }

  /* 可点段（仅 done）hover 时轻微提亮，与 Sidebar 的 hover 公式保持一致；
     is-active 段不可点，排除在外，避免出现"可点"的视觉误导 */
  .seg:not(:disabled):not(.selected):not(.is-active):hover {
    background: color-mix(in srgb, var(--fg) 8%, transparent);
  }

  /* 按压反馈：scale(0.97)，pending 段因 disabled 天然不会触发 active 态 */
  .seg:not(:disabled):active {
    transform: scale(0.97);
  }

  /* 选中段（active prop 命中）：白色凸起（深色下用 --card 深灰凸起）+ 细阴影 */
  .seg.selected {
    background: var(--card);
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.12);
  }

  /* active 状态（进行中）：文字高亮为 accent 色，独立于是否被选中显示；
     不可点击（选中显示由父组件 active prop 控制），视觉上与 is-pending 一致地
     去掉手型光标，避免"看起来能点、点了却没反应"的视觉欺骗 */
  .seg.is-active {
    color: var(--accent);
    font-weight: 600;
    cursor: default;
  }

  /* pending 段：置灰、默认光标、不可交互 */
  .seg.is-pending {
    color: var(--muted);
    cursor: default;
  }
  .seg:disabled {
    cursor: default;
  }

  /* 分离中小旋转指示：描边用 accent 色，淡色圆环 + 高亮弧线旋转 */
  .spinner {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    border: 1.5px solid color-mix(in srgb, var(--accent) 30%, transparent);
    border-top-color: var(--accent);
    animation: seg-spin 0.8s linear infinite;
  }
  @keyframes seg-spin {
    to {
      transform: rotate(360deg);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .spinner {
      animation: none;
    }
  }
</style>

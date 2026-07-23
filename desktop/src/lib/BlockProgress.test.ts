// BlockProgress 组件测试：验证块状进度条的百分比渲染与「分离中」态文案。
import { render } from '@testing-library/svelte';
import { expect, test } from 'vitest';
import BlockProgress from './BlockProgress.svelte';

test('渲染已完成/总数对应的填充块与百分比', () => {
  const { getByText } = render(BlockProgress, { total: 10, done: 4, phase: 'transcribing' });
  // 4/10 → 40%
  expect(getByText(/40%/)).toBeTruthy();
});

test('分离阶段显示分离中而非块数', () => {
  const { getByText } = render(BlockProgress, { total: 0, done: 0, phase: 'diarizing' });
  expect(getByText(/分离中/)).toBeTruthy();
});

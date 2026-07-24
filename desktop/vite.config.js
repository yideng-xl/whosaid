import { defineConfig } from "vite";
import { sveltekit } from "@sveltejs/kit/vite";

// @ts-expect-error process is a nodejs global
const host = process.env.TAURI_DEV_HOST;
// @ts-expect-error process is a nodejs global
const isVitest = !!process.env.VITEST;

// https://vite.dev/config/
export default defineConfig(async () => ({
  plugins: [sveltekit()],

  // Vite options tailored for Tauri development and only applied in `tauri dev` or `tauri build`
  //
  // 1. prevent Vite from obscuring rust errors
  clearScreen: false,
  // 2. tauri expects a fixed port, fail if that port is not available
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: 1421,
        }
      : undefined,
    watch: {
      // 3. tell Vite to ignore watching `src-tauri`
      ignored: ["**/src-tauri/**"],
    },
  },

  // vitest 下渲染 Svelte 组件需要走浏览器构建产物（含 mount 生命周期），
  // 否则会解析到 SSR 版本导致 `mount(...) is not available on the server`
  resolve: isVitest
    ? {
        conditions: ["browser"],
      }
    : undefined,

  test: {
    // 组件测试需要 DOM 环境（document/window）
    environment: "jsdom",
    // 开启全局 beforeEach/afterEach，@testing-library/svelte 借此在每个用例后
    // 自动 cleanup()，避免多个组件测试之间 DOM 残留互相污染
    globals: true,
  },
}));

/// <reference types="vitest/config" />
// vite 配置：dev 时把 /api 与 /files 代理到本地 server（127.0.0.1:47820，见 server/app.py）；
// build 产物落 web/dist，由 server 的静态路由直接服务。
// TODO(contract): @types/react / @types/react-dom 不在 BUILD-CONTRACT §0.4 dev 白名单里，
// 但 typescript 对 JSX 做类型检查必需（零运行时代码）——按"typescript 附属类型包"处理，待契约确认。
// 不 import node:* ——@types/node 不在白名单；root/outDir 走 vite 默认的相对配置文件解析。
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  base: "./",
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    // UI 对齐判例（src/parity.test.tsx，CONTRACT §62）要 import 仓库根下 ui/parity/ 的清单、
    // 账本（?raw）与 fixture——vite 默认只放行 web/ 内的文件，这里把上一级（仓库根）加进白名单。
    fs: { allow: [".."] },
    proxy: {
      "/api": "http://127.0.0.1:47820",
      "/files": "http://127.0.0.1:47820",
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    // vitest 默认把 CSS 换成空串跳过处理——连 `?raw` 一起空掉，而
    // styles/tokens.test.ts 要读 tokens.css 原文钉暗色双写。打开后 CSS 走
    // 与 vite build 相同的处理链。
    css: true,
  },
});

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
    proxy: {
      "/api": "http://127.0.0.1:47820",
      "/files": "http://127.0.0.1:47820",
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});

// Playwright 视觉基线（CONTRACT §62.4）。e2e/visual.spec.ts 自己种 demo 数据、起 server（随机
// 空闲端口）——这里只定浏览器、视口与 golden 的落点。
//   · 视口 1440×900（owner 机器的常用窗口尺寸；原生 app 默认窗 900×640 + 侧栏，放大后看得清列）
//   · golden 路径不带平台/浏览器后缀：web/e2e/__screenshots__/<spec>/<name>.png——
//     基线在 macOS 上生成（字体 = SF Pro / PingFang，与 CI 的 macos-latest 一致）
//   · toHaveScreenshot 允许 2% 像素差（时间戳/相对时间等小字漂移不算回归；masked 的除外）
//   · 更新基线是显式动作：npm run visual:update（见 CONTRIBUTING「视觉基线」）
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  snapshotPathTemplate: "{testDir}/__screenshots__/{testFileName}/{arg}{ext}",
  expect: {
    toHaveScreenshot: {
      maxDiffPixelRatio: 0.02,
      animations: "disabled",
      caret: "hide",
    },
  },
  use: {
    ...devices["Desktop Chrome"],
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    locale: "zh-CN",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});

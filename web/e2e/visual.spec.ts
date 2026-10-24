// 视觉基线（CONTRACT §66.4）：每个 web 页面 × light / dark 各一张 1440×900 截图，与
// web/e2e/__screenshots__/visual.spec.ts/*.png 的 golden 比对（容差 truth = web/playwright.config.ts 的 maxDiffPixelRatio / threshold）。
// 页面 = 会议纪要（缺省页）+ 设置（v2.0.0：看板 / 回收站随 Act 管线退役，它们的 golden 一并删除）。
// 数据 = scripts/demo_seed.py 的 initial 场景（全虚构），server 起在随机端口（demoServer.ts）。
// 改 UI 的 PR 必须显式更新 golden（npm run visual:update）并在 PR 里说明——CI job
// "Web visual (playwright)" 出生时 informational，不挡合并。
import { expect, test, type Page } from "@playwright/test";
import { startDemoServer, type DemoServer } from "./demoServer";

const PAGES = [
  { name: "recaps", query: "" },
  { name: "settings", query: "?page=settings" },
] as const;
const THEMES = ["light", "dark"] as const;

let server: DemoServer;

test.beforeAll(async () => {
  // 起 server 的等待（demoServer.ts 最多 90 s）要能在 hook 超时之前把诊断信息抛出来
  test.setTimeout(150_000);
  server = await startDemoServer("initial");
});

test.afterAll(() => {
  server?.stop();
});

async function openPage(page: Page, query: string, theme: (typeof THEMES)[number]) {
  // 首帧前写偏好：index.html 读 localStorage zai.theme / zai.lang（与真实用户同一路径）
  await page.addInitScript((t) => {
    window.localStorage.setItem("zai.theme", t);
    window.localStorage.setItem("zai.lang", "zh");
  }, theme);
  await page.goto(`${server.baseURL}/${query}`);
  await page.getByRole("heading", { level: 1 }).waitFor();
  await page.locator(".shell-main").waitFor();
  await page.waitForLoadState("networkidle");
}

for (const { name, query } of PAGES) {
  for (const theme of THEMES) {
    test(`${name} · ${theme}`, async ({ page }) => {
      await openPage(page, query, theme);
      await expect(page).toHaveScreenshot(`${name}-${theme}.png`, {
        // 相对时间（「上次检查：N 分钟前」之类）每秒漂移，不参与比对
        mask: [page.locator(".card-meta-text")],
      });
    });
  }
}

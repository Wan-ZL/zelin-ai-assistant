// 视觉基线（CONTRACT §64.4）：每个 web 页面 × light / dark 各一张 1440×900 截图，与
// web/e2e/__screenshots__/visual.spec.ts/*.png 的 golden 比对（2% 像素差以内算同一张）。
// 数据 = scripts/demo_seed.py 的 initial 场景（全虚构），server 起在随机端口（demoServer.ts）。
// 改 UI 的 PR 必须显式更新 golden（npm run visual:update）并在 PR 里说明——CI job
// "Web visual (playwright)" 出生时 informational，不挡合并。
// 遮罩：新鲜度 / 部署标签是时间文案（每秒漂移）；设置页里 ~/.claude/settings.json 的路径随
// 临时 HOME 每次不同——三者不参与比对。
import { expect, test, type Page } from "@playwright/test";
import { startDemoServer, type DemoServer } from "./demoServer";

const PAGES = [
  { name: "board", query: "" },
  { name: "trash", query: "?page=trash" },
  { name: "settings", query: "?page=settings" },
] as const;
const THEMES = ["light", "dark"] as const;

let server: DemoServer;

test.beforeAll(async () => {
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
  // 看板数据到位（AppShell 只在有快照时渲染页面）
  await page.locator(".shell-main").waitFor();
  await page.waitForLoadState("networkidle");
}

for (const { name, query } of PAGES) {
  for (const theme of THEMES) {
    test(`${name} · ${theme}`, async ({ page }) => {
      await openPage(page, query, theme);
      await expect(page).toHaveScreenshot(`${name}-${theme}.png`, {
        mask: [
          page.locator(".shell-freshness"),
          page.locator(".shell-deploy"),
          page.locator(".settings-global-path"),
        ],
      });
    });
  }
}

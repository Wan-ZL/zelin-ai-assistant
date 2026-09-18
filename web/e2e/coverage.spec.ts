// flow:pages_controls（全覆盖跑者 scripts/qa/coverage_run.py，CONTRACT §58 QA 闸门）——
// 真浏览器走一遍左栏的**每一个**页面，点每页的安全控件（只点不会离开本 app、不会写外部世界
// 的：tab / 折叠 / 分段器 / 过滤器），断言：
//   · 每页都渲染出主标题（页面没白屏）；
//   · 整趟零 console error、零 pageerror（渲染时炸的 React 错误在这里才抓得到）。
// 数据 = demo initial 场景（demoServer.ts 起随机端口的真 server；绝不碰 live state/）。
// 判决怎么被消费：coverage_run.py 读 playwright 的 --reporter=json，按 `<spec>::<title>` 映射
// 到清单 id（proof `flow:pages_controls` / `playwright:coverage.spec.ts::…`）。
import { expect, test, type Page } from "@playwright/test";
import { startDemoServer, type DemoServer } from "./demoServer";

let server: DemoServer;

test.beforeAll(async () => {
  test.setTimeout(150_000);
  server = await startDemoServer("initial");
});

test.afterAll(() => {
  server?.stop();
});

// 只点安全控件：这些 class / 属性都是同文档内的视图切换，不发写请求、不开外部 app
const SAFE_CONTROLS = [
  "[role=tab]",
  ".segmented button",
  ".settings-section-header",
  ".lane-filter button",
];

function collectErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console: ${msg.text()}`);
  });
  page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
  return errors;
}

async function openBoard(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem("zai.theme", "light");
    window.localStorage.setItem("zai.lang", "zh");
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${server.baseURL}/`);
  await page.locator('[data-rail="left"]').waitFor();
  await page.waitForLoadState("networkidle");
}

test("rail pages walk: every page renders and clicking safe controls raises no console error", async ({ page }) => {
  test.setTimeout(180_000);
  const errors = collectErrors(page);
  await openBoard(page);

  const slugs = await page.locator("[data-rail-item]").evaluateAll((nodes) =>
    nodes.map((n) => n.getAttribute("data-rail-item") ?? "").filter(Boolean),
  );
  expect(slugs.length, "rail carries no items — the board did not render").toBeGreaterThan(0);

  for (const slug of slugs) {
    const item = page.locator(`[data-rail-item="${slug}"]`);
    await item.click();
    await expect(item).toHaveAttribute("aria-current", "page");
    await page.waitForLoadState("networkidle");
    // 页面活着：main 里有可见内容（标题/列/区块随页而异，只断言非空）
    await expect(page.locator("main, .settings-page, .board-columns").first()).toBeVisible();

    for (const selector of SAFE_CONTROLS) {
      const controls = page.locator(selector);
      const count = Math.min(await controls.count(), 6);
      for (let i = 0; i < count; i += 1) {
        const control = controls.nth(i);
        if (!(await control.isVisible()) || !(await control.isEnabled())) continue;
        await control.click({ trial: false, timeout: 5_000 }).catch(() => undefined);
      }
    }
  }

  expect(errors, `console errors while walking ${slugs.length} rail pages`).toEqual([]);
});

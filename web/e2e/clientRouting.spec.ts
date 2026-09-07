// 客户端路由（CONTRACT §49 / §54.4 2026-09-06 追记，D40）——真浏览器判例：
//   · rail「设置」→ 设置页、URL ?page=settings、document.title 跟页，**文档没有重载**（window 上放的标记还在；整页导航会把它清掉）；
//   · 「← 返回看板」→ 看板列直接在（快照还在 store 里），不闪「正在加载看板…」，标记仍在；
//   · 浏览器后退 / 前进（popstate）→ 页跟 URL 走、标记仍在；
//   · ⌘点 rail 项不拦（浏览器手势归浏览器：这里只验 href 仍是完整深链、能在新文档里直达）。
// 数据 = demo initial 场景（demoServer.ts 起随机端口的真 server）。
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

const MARKER = "__zaiSameDocument";

async function openBoard(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem("zai.theme", "light");
    window.localStorage.setItem("zai.lang", "zh");
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${server.baseURL}/`);
  await page.locator(".board-column").first().waitFor();
  await page.waitForLoadState("networkidle");
  // 同文档标记：整页重载会把它清掉
  await page.evaluate((key) => { (window as unknown as Record<string, unknown>)[key] = "alive"; }, MARKER);
}

const marker = (page: Page) => page.evaluate((key) => (window as unknown as Record<string, unknown>)[key], MARKER);

test("rail → 设置 → 「← 返回看板」：URL / 标题跟页，文档不重载，看板不闪加载态", async ({ page }) => {
  await openBoard(page);
  await expect(page).toHaveTitle(/— 任务台$/);

  await page.locator('[data-rail-item="settings"]').click();
  await expect(page).toHaveURL(/[?&]page=settings(&|$)/);
  await expect(page.locator(".settings-page-title")).toHaveText("设置");
  await expect(page).toHaveTitle(/— 设置$/);
  await expect(page.locator('[data-rail-item="settings"]')).toHaveAttribute("aria-current", "page");
  expect(await marker(page)).toBe("alive");

  await page.locator(".settings-page .trash-back-link").click();
  await expect(page).not.toHaveURL(/[?&]page=/);
  await expect(page.locator(".board-column").first()).toBeVisible();
  expect(await page.getByText("正在加载看板…").count()).toBe(0);
  await expect(page).toHaveTitle(/— 任务台$/);
  await expect(page.locator('[data-rail-item="dashboard"]')).toHaveAttribute("aria-current", "page");
  expect(await marker(page)).toBe("alive");
});

test("浏览器后退 / 前进走 popstate：页跟 URL，文档仍不重载", async ({ page }) => {
  await openBoard(page);
  await page.locator('[data-rail-item="trash"]').click();
  await expect(page).toHaveURL(/[?&]page=trash(&|$)/);
  await expect(page).toHaveTitle(/— 回收站$/);
  await page.locator('[data-rail-item="about"]').click();
  await expect(page).toHaveURL(/[?&]page=about(&|$)/);
  await expect(page).toHaveTitle(/— 关于$/);

  await page.goBack();
  await expect(page).toHaveURL(/[?&]page=trash(&|$)/);
  await expect(page).toHaveTitle(/— 回收站$/);
  expect(await marker(page)).toBe("alive");

  await page.goBack();
  await expect(page).not.toHaveURL(/[?&]page=/);
  await expect(page.locator(".board-column").first()).toBeVisible();
  await expect(page).toHaveTitle(/— 任务台$/);
  expect(await marker(page)).toBe("alive");

  await page.goForward();
  await expect(page).toHaveURL(/[?&]page=trash(&|$)/);
  await expect(page).toHaveTitle(/— 回收站$/);
  expect(await marker(page)).toBe("alive");
});

test("深链仍是 API：rail 项的 href 是完整的 ?page= 深链，直接打开 / 刷新还原同一页", async ({ page }) => {
  await openBoard(page);
  const href = await page.locator('[data-rail-item="archive"]').getAttribute("href");
  expect(href).toBeTruthy();
  expect(new URL(href!, server.baseURL).searchParams.get("page")).toBe("archive");
  await page.goto(new URL(href!, server.baseURL).toString());
  await expect(page).toHaveTitle(/— 永久性完成$/);
  await page.reload();
  await expect(page).toHaveTitle(/— 永久性完成$/);
});

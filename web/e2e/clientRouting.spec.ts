// 客户端路由（CONTRACT §49 / §54.4 2026-09-06 追记，D40）——真浏览器判例：
//   · rail「设置」→ 设置页、URL ?page=settings、document.title 跟页，**文档没有重载**（window 上放的标记还在；整页导航会把它清掉）；
//   · rail「会议纪要」→ 回到缺省页（不带 ?page=），标记仍在；
//   · 浏览器后退 / 前进（popstate）→ 页跟 URL 走、标记仍在；
//   · ⌘点 rail 项不拦（浏览器手势归浏览器：这里只验 href 仍是完整深链、能在新文档里直达）；
//   · 退役页的旧深链（?page=board / trash）回落会议纪要，不白屏。
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

async function openRecaps(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem("zai.theme", "light");
    window.localStorage.setItem("zai.lang", "zh");
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${server.baseURL}/`);
  await page.locator(".recaps-page").waitFor();
  await page.waitForLoadState("networkidle");
  // 同文档标记：整页重载会把它清掉
  await page.evaluate((key) => { (window as unknown as Record<string, unknown>)[key] = "alive"; }, MARKER);
}

const marker = (page: Page) => page.evaluate((key) => (window as unknown as Record<string, unknown>)[key], MARKER);

test("rail → 设置 → rail 会议纪要：URL / 标题跟页，文档不重载", async ({ page }) => {
  await openRecaps(page);
  await expect(page).toHaveTitle(/— 会议纪要$/);

  await page.locator('[data-rail-item="settings"]').click();
  await expect(page).toHaveURL(/[?&]page=settings(&|$)/);
  await expect(page.locator(".settings-page-title")).toHaveText("设置");
  await expect(page).toHaveTitle(/— 设置$/);
  await expect(page.locator('[data-rail-item="settings"]')).toHaveAttribute("aria-current", "page");
  expect(await marker(page)).toBe("alive");

  await page.locator('[data-rail-item="recaps"]').click();
  await expect(page).not.toHaveURL(/[?&]page=/);
  await expect(page.locator(".recaps-page")).toBeVisible();
  await expect(page).toHaveTitle(/— 会议纪要$/);
  await expect(page.locator('[data-rail-item="recaps"]')).toHaveAttribute("aria-current", "page");
  expect(await marker(page)).toBe("alive");
});

test("浏览器后退 / 前进走 popstate：页跟 URL，文档仍不重载", async ({ page }) => {
  await openRecaps(page);
  await page.locator('[data-rail-item="ingest"]').click();
  await expect(page).toHaveURL(/[?&]page=ingest(&|$)/);
  await expect(page).toHaveTitle(/— 录制与导入$/);
  await page.locator('[data-rail-item="about"]').click();
  await expect(page).toHaveURL(/[?&]page=about(&|$)/);
  await expect(page).toHaveTitle(/— 关于$/);

  await page.goBack();
  await expect(page).toHaveURL(/[?&]page=ingest(&|$)/);
  await expect(page).toHaveTitle(/— 录制与导入$/);
  expect(await marker(page)).toBe("alive");

  await page.goBack();
  await expect(page).not.toHaveURL(/[?&]page=/);
  await expect(page.locator(".recaps-page")).toBeVisible();
  await expect(page).toHaveTitle(/— 会议纪要$/);
  expect(await marker(page)).toBe("alive");

  await page.goForward();
  await expect(page).toHaveURL(/[?&]page=ingest(&|$)/);
  await expect(page).toHaveTitle(/— 录制与导入$/);
  expect(await marker(page)).toBe("alive");
});

test("深链仍是 API：rail 项的 href 是完整的 ?page= 深链，直接打开 / 刷新还原同一页；退役页的旧深链回落会议纪要", async ({ page }) => {
  await openRecaps(page);
  const href = await page.locator('[data-rail-item="about"]').getAttribute("href");
  expect(href).toBeTruthy();
  expect(new URL(href!, server.baseURL).searchParams.get("page")).toBe("about");
  await page.goto(new URL(href!, server.baseURL).toString());
  await expect(page).toHaveTitle(/— 关于$/);
  await page.reload();
  await expect(page).toHaveTitle(/— 关于$/);
  for (const retired of ["board", "trash", "skills"]) {
    await page.goto(`${server.baseURL}/?page=${retired}`);
    await expect(page).toHaveTitle(/— 会议纪要$/);
    await expect(page.locator(".recaps-page")).toBeVisible();
  }
});

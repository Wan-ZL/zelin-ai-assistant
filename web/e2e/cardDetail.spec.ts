// 卡片详情只有一面（CONTRACT §49 追记 2026-09-04，D34 / issue #217）——真浏览器判例：
//   · 点卡上的「展开详情 ▸」→ 右侧详情侧栏（role=dialog）出现、抬头是这张卡的标题、URL 带 ?card=<主键>（可刷新还原 / 可分享）；
//     卡片自身高度不变（不就地撑开，泳道不跳）；⎋ 关侧栏、?card= 清掉；
//   · 键盘：焦点在卡上按 Enter → 同一个侧栏（a11y，「展开详情 ▸」的键盘等价物）；关侧栏把焦点还给打开它的控件；
//   · 双击卡片不开侧栏（语义留给 #216 终端接管）；
//   · 侧栏开着时 ⎋ 只关侧栏：⌘F 搜索词 / URL ?q= 不动（FilterBar 的两段 ⎋ 那一下不插手）。
// 数据 = demo initial 场景（demoServer.ts 起随机端口的真 server）；用「执行中」列第一张卡——卡面标题与详情抬头都是 name。
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

async function openBoard(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem("zai.theme", "light");
    window.localStorage.setItem("zai.lang", "zh");
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${server.baseURL}/`);
  await page.getByRole("heading", { level: 1 }).waitFor();
  await page.locator(".shell-main").waitFor();
  await page.waitForLoadState("networkidle");
}

/** 「执行中」列第一张非排队卡（有 name 的 TaskRow：卡面标题 = 详情抬头） */
function firstRunningCard(page: Page) {
  return page.locator("article.task-card").filter({ has: page.locator(".card-dot.is-running") }).first();
}

test("点「展开详情 ▸」→ 右侧详情侧栏带这张卡的标题 + ?card= 深链；卡片不撑高；⎋ 关闭", async ({ page }) => {
  await openBoard(page);
  const card = firstRunningCard(page);
  await expect(card).toBeVisible();
  const title = (await card.locator(".card-title").innerText()).trim();
  const id = (await card.locator(".card-id").innerText()).trim();
  const heightBefore = (await card.boundingBox())!.height;
  expect(await card.getByRole("button", { name: "收起 ▾" }).count()).toBe(0);

  await card.getByRole("button", { name: "展开详情 ▸" }).click();
  const drawer = page.getByRole("dialog", { name: /^卡片详情 / });
  await expect(drawer).toBeVisible();
  await expect(drawer.locator(".zai-drawer-heading h2")).toHaveText(title);
  await expect(drawer.locator(".zai-drawer-id").first()).toHaveText(id);
  expect(new URL(page.url()).searchParams.get("card")).toBeTruthy();
  // 详情积木住侧栏：📋 要做什么 在侧栏里、不在卡上
  await expect(drawer.getByRole("heading", { name: "📋 要做什么" })).toBeVisible();
  expect(await card.getByText("📋 要做什么").count()).toBe(0);
  // 卡片自身一像素没长（就地展开退役 → 泳道不跳）
  expect((await card.boundingBox())!.height).toBe(heightBefore);

  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(new URL(page.url()).searchParams.get("card")).toBeNull();
  // 关侧栏：焦点回到打开它的「展开详情 ▸」（键盘用户不必从页顶重新 Tab）
  await expect(card.getByRole("button", { name: "展开详情 ▸" })).toBeFocused();
});

test("键盘：焦点在卡上按 Enter → 同一个详情侧栏；双击卡片不开", async ({ page }) => {
  await openBoard(page);
  const card = firstRunningCard(page);
  const title = (await card.locator(".card-title").innerText()).trim();

  await card.dblclick({ position: { x: 20, y: 8 } });
  await page.waitForTimeout(200);
  expect(await page.getByRole("dialog").count()).toBe(0);

  await card.focus();
  await expect(card).toBeFocused();
  await page.keyboard.press("Enter");
  const drawer = page.getByRole("dialog", { name: /^卡片详情 / });
  await expect(drawer).toBeVisible();
  await expect(drawer.locator(".zai-drawer-heading h2")).toHaveText(title);
  await expect(drawer).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(card).toBeFocused(); // 还给按 Enter 的那张卡
});

test("侧栏开着时 ⎋ 只关侧栏：搜索词与 ?q= 留着，看板还是筛过的那几张", async ({ page }) => {
  await openBoard(page);
  const search = page.getByRole("searchbox", { name: "搜索卡片" });
  const total = await page.locator("article.task-card").count();
  await search.fill("example");
  await expect(page).toHaveURL(/[?&]q=example/);
  const narrowed = await page.locator("article.task-card").count();
  expect(narrowed).toBeGreaterThan(0);
  expect(narrowed).toBeLessThan(total);

  // 筛出来的第一张带「展开详情 ▸」的卡（占位 / 待办卡没有这个入口）
  const details = page.getByRole("button", { name: "展开详情 ▸" }).first();
  await details.click();
  await expect(page.getByRole("dialog", { name: /^卡片详情 / })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(search).toHaveValue("example");
  expect(new URL(page.url()).searchParams.get("q")).toBe("example");
  expect(await page.locator("article.task-card").count()).toBe(narrowed);
});

test("?card= 深链刷新还原：直接打开带 ?card= 的 URL 就是那张卡的侧栏", async ({ page }) => {
  await openBoard(page);
  const card = firstRunningCard(page);
  await card.getByRole("button", { name: "展开详情 ▸" }).click();
  await expect(page.getByRole("dialog", { name: /^卡片详情 / })).toBeVisible();
  const deepLink = page.url();
  await page.goto(deepLink);
  await page.locator(".shell-main").waitFor();
  await expect(page.getByRole("dialog", { name: /^卡片详情 / })).toBeVisible();
});

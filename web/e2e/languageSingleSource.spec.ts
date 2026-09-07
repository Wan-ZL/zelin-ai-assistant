// 语言只有一把开关（CONTRACT §15 追记 2026-09-06，D37；§49 / §61.1 追记）——真浏览器判例，对着 demo 的真 server：
//   · 首次运行（server 的 general.language source=default）：首帧显示的语言被写进 settings_overrides.json 一次；设置页「界面语言」
//     的 help 说的是「同一把开关」、来源章是「覆盖」（写在目录 GET 之前 / 之后都一样——store 等在途目录落地再补拉）；
//   · 顶栏切换：override 跟着改、<html lang> 立刻换；
//   · 新文档带着**过期的** localStorage 缓存进来 → 水合后仍是 server 的值（缓存只是首帧提示）。
// 数据 = demo initial 场景（demoServer.ts 起随机端口的真 server，临时 home——读的 overrides 文件也在那里）。
import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { startDemoServer, type DemoServer } from "./demoServer";

let server: DemoServer;

test.beforeAll(async () => {
  test.setTimeout(150_000);
  server = await startDemoServer("initial");
});

test.afterAll(() => {
  server?.stop();
});

const overrides = (): Record<string, unknown> =>
  JSON.parse(readFileSync(path.join(server.home, "state", "settings_overrides.json"), "utf-8"));

const htmlLang = (page: Page) => page.evaluate(() => document.documentElement.lang);

async function open(page: Page, query: string, hint: "zh" | "en") {
  await page.addInitScript((lang) => {
    window.localStorage.setItem("zai.theme", "light");
    window.localStorage.setItem("zai.lang", lang);
  }, hint);
  await page.goto(`${server.baseURL}/${query}`);
  await page.getByRole("heading", { level: 1 }).waitFor();
  await page.waitForLoadState("networkidle");
}

test("首次运行：首帧显示的语言被持久化一次；设置页的 help 与来源章说的是同一把开关", async ({ page }) => {
  await open(page, "?page=settings", "zh");
  expect(overrides().language).toBe("zh");
  await expect(page.getByText("看板、系统通知与修法句共用这一把开关", { exact: false })).toBeVisible();
  const chip = page.locator("section[aria-labelledby='settings-general-title'] .settings-source-chip").first();
  await expect(chip).toHaveAttribute("data-source", "override");
  // 幂等：再开一次不再写（文件内容不变）
  const before = JSON.stringify(overrides());
  await page.reload();
  await page.getByRole("heading", { level: 1 }).waitFor();
  await page.waitForLoadState("networkidle");
  expect(JSON.stringify(overrides())).toBe(before);
});

test("顶栏切换写 override 且 UI 立刻换；过期的 localStorage 缓存压不过 server 的值", async ({ page }) => {
  await open(page, "", "zh");
  const startedIn = overrides().language as string;
  await page.getByRole("button", { name: /切换到英文|Switch to Chinese/ }).click();
  const target = startedIn === "en" ? "zh" : "en";
  await expect.poll(() => htmlLang(page)).toBe(target === "en" ? "en" : "zh-CN");
  await expect.poll(() => overrides().language).toBe(target);
  // 另一个文档、带着相反的缓存进来（另一台浏览器 / 旧标签）：首帧按缓存猜，水合后回到 server 的值
  await page.evaluate((stale) => window.localStorage.setItem("zai.lang", stale), target === "en" ? "zh" : "en");
  await page.reload();
  await page.getByRole("heading", { level: 1 }).waitFor();
  await page.waitForLoadState("networkidle");
  await expect.poll(() => htmlLang(page)).toBe(target === "en" ? "en" : "zh-CN");
  expect(overrides().language).toBe(target);
});

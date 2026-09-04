// 顶栏单行的几何判例（CONTRACT §49 追记 2026-09-04，D31）——真浏览器量布局，jsdom 量不出：
//   · 左翼最长（陈旧新鲜度 + 已回滚部署 + 设备标签，zh / en）+ 壳桥在场（右侧多两颗开关）时，
//     三档各取一个视口：槽位里的每个控件都落在槽位矩形内（不被裁）、顶栏仍 52px 一行、标题完整；
//     tight 再把搜索框展开量一次（basis 0 吃余量，不许把标题挤掉）；
//   · 回收站页复用 .chrome-search 但容器竖排——flex-basis 不许变成高度；
//   · tight 搜索框展开着点「筛选」/「提建议」：第一下就开（pointerdown 不抢焦点，见 FilterBar.tsx）。
// 数据 = demo initial 场景，/api/board 经 page.route 改写 generated_at / device_label / deploy_state。
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

// 壳桥快照：录制 / 实时字幕两颗开关都在场（shellBridge.normalizeShellState 补齐其余字段）
const SHELL_SNAPSHOT = {
  recording: { available: true, on: false, mode: "off", engine_running: false },
  captions: { available: true, on: false, engine: "auto" },
};

interface OpenOptions {
  lang: "zh" | "en";
  width: number;
  shell?: boolean;
  /** 左翼拉到最长：数据陈旧 125 分钟 + 设备标签 + 已回滚的部署状态 */
  longLeft?: boolean;
  query?: string;
}

async function open(page: Page, { lang, width, shell = false, longLeft = false, query = "" }: OpenOptions) {
  await page.addInitScript(({ lang, shell, snapshot }) => {
    window.localStorage.setItem("zai.theme", "light");
    window.localStorage.setItem("zai.lang", lang);
    if (shell) {
      window.webkit = { messageHandlers: { zaiShell: { postMessage: async () => snapshot } } };
    }
  }, { lang, shell, snapshot: SHELL_SNAPSHOT });
  if (longLeft) {
    await page.route("**/api/board", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.generated_at = new Date(Date.now() - 125 * 60_000).toISOString();
      body.device_label = "MacBook Pro · zelin";
      body.deploy_state = {
        status: "rolled_back",
        version: "1.0.9",
        last_deployed: new Date(Date.now() - 3 * 3_600_000).toISOString(),
        failed_sha: "abc1234",
        detail: "doctor FAIL: server heartbeat missing",
      };
      await route.fulfill({ response, json: body });
    });
  }
  await page.setViewportSize({ width, height: 900 });
  await page.goto(`${server.baseURL}/${query}`);
  await page.getByRole("heading", { level: 1 }).waitFor();
  await page.locator(".shell-main").waitFor();
  await page.waitForLoadState("networkidle");
}

interface HeaderGeometry {
  density: string | undefined;
  headerHeight: number;
  titleInsideLeft: boolean;
  /** 槽位矩形外（左右任一侧超出 0.5px 以上）的 .chrome-filterbar 直接子元素 */
  outside: string[];
}

function measureHeader(page: Page): Promise<HeaderGeometry> {
  return page.evaluate(() => {
    const header = document.querySelector<HTMLElement>(".shell-header")!;
    const slot = document.querySelector<HTMLElement>(".shell-search-slot")!.getBoundingClientRect();
    const left = document.querySelector<HTMLElement>(".shell-header-left")!.getBoundingClientRect();
    const title = document.querySelector<HTMLElement>(".shell-title")!.getBoundingClientRect();
    const outside: string[] = [];
    for (const child of Array.from(document.querySelector<HTMLElement>(".chrome-filterbar")!.children)) {
      const rect = child.getBoundingClientRect();
      if (rect.width === 0) continue;
      if (rect.left < slot.left - 0.5 || rect.right > slot.right + 0.5) {
        outside.push(`${child.className} [${Math.round(rect.left)}..${Math.round(rect.right)}] vs slot [${Math.round(slot.left)}..${Math.round(slot.right)}]`);
      }
    }
    return {
      density: header.dataset.density,
      headerHeight: header.getBoundingClientRect().height,
      titleInsideLeft: title.right <= left.right + 0.5 && title.left >= left.left - 0.5,
      outside,
    };
  });
}

async function expectOneRowNoClipping(page: Page) {
  const geometry = await measureHeader(page);
  expect(geometry.outside).toEqual([]);
  expect(geometry.headerHeight).toBe(52);
  expect(geometry.titleInsideLeft).toBe(true);
  return geometry;
}

const SEARCH_NAME = { zh: "搜索卡片", en: "Search cards" } as const;

for (const lang of ["zh", "en"] as const) {
  // 1640 → zh full / en compact；1200 → zh compact / en tight；720 → tight
  for (const width of [1640, 1200, 720]) {
    test(`左翼最长 + 壳 · ${lang} @${width}：槽位控件不裁、顶栏一行、标题完整`, async ({ page }) => {
      await open(page, { lang, width, shell: true, longLeft: true });
      const geometry = await expectOneRowNoClipping(page);
      if (geometry.density === "tight") {
        await page.getByRole("button", { name: SEARCH_NAME[lang] }).click();
        await expect(page.getByRole("searchbox", { name: SEARCH_NAME[lang] })).toBeFocused();
        await expectOneRowNoClipping(page);
      }
    });
  }
}

test("回收站页的搜索框：竖排容器里仍是单行输入框高度（flex-basis 只在顶栏的条里生效）", async ({ page }) => {
  await open(page, { lang: "zh", width: 1440, query: "?page=trash" });
  const height = await page.locator(".trash-page .chrome-search").evaluate((el) => el.getBoundingClientRect().height);
  expect(height).toBeLessThan(40);
});

test("tight：搜索框展开着，点「筛选」/「提建议」一下就开", async ({ page }) => {
  await open(page, { lang: "en", width: 900 });
  expect((await measureHeader(page)).density).toBe("tight");

  await page.getByRole("button", { name: "Search cards" }).click();
  await expect(page.getByRole("searchbox", { name: "Search cards" })).toBeFocused();
  await page.getByRole("button", { name: "Filters" }).click();
  await expect(page.getByRole("dialog", { name: "Filters" })).toBeVisible();
  // 面板开的同一次渲染里搜索框已收起——面板量到的锚点就是最终位置
  await expect(page.getByRole("searchbox")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);

  await page.getByRole("button", { name: "Search cards" }).click();
  await expect(page.getByRole("searchbox", { name: "Search cards" })).toBeFocused();
  await page.getByRole("button", { name: "Send feedback" }).click();
  await expect(page.getByRole("dialog").filter({ hasText: "Send feedback (overall)" })).toBeVisible();
});

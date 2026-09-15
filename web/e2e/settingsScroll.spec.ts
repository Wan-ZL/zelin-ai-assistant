// 窗口从不整体滚动、页面只在 .shell-main 里滚（owner 决策 D42，CONTRACT §54.4 2026-09-06 追记 +
// 2026-09-15 追记；issue #359：设置页滚到最后一个区「每日整理」之后还能继续往下滚、内容下方一大片空白，
// 左侧导航栏那片灰底只到窗口约 1/3 处）——真浏览器判例：
//   · 设置页（全部区展开，1440×900）：.shell-main 的 scrollHeight 紧贴内容——不超过「最后一区底边 +
//     一个 padding 单位（页面的 48px 下内边距）」；
//   · 导航栏灰底贯穿视口：滚到 .shell-main 的底、再用滚轮往下推，.rail 仍是 top 0 / 高 = 视口高；
//   · 文档不滚：documentElement.scrollHeight = 视口高，滚轮与程序滚动都把 window.scrollY 留在 0；
//   · **横幅摞高过一屏也不许把文档变成第二个滚动容器**（#359 的成因：横幅是压不扁的 flex 项，
//     溢出壳之后文档滚起来、壳随之滚出窗口——灰底只剩上面一截、内容下方露白）；
//   · 其它页（看板 / 会议纪要 / 回收站 / 永久性完成）同样：文档不滚、灰底贯穿视口。
// 数据 = demo initial 场景。
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

/** 设置页页面容器的下内边距（settings.css `.settings-page { padding: 20px 24px 48px }`）= 一个 padding 单位 */
const PAGE_PADDING_BOTTOM = 48;

async function open(page: Page, url: string): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem("zai.theme", "light");
    window.localStorage.setItem("zai.lang", "zh");
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${server.baseURL}/${url}`);
  await page.getByRole("heading", { level: 1 }).waitFor();
  await page.locator(".shell-main").waitFor();
  await page.waitForLoadState("networkidle");
}

/** 设置页全区展开（区 id 从 DOM 读——SETTINGS_TOC 增删不用改这里），再重开一次页 */
async function openSettingsExpanded(page: Page): Promise<void> {
  await open(page, "?page=settings");
  const ids = await page.evaluate(() =>
    Array.from(document.querySelectorAll<HTMLElement>(".settings-page > .settings-fold"))
      .map((fold) => fold.dataset.section)
      .filter((id): id is string => !!id));
  expect(ids.length).toBeGreaterThan(10);
  await page.evaluate((all) => {
    window.localStorage.setItem("settings.expandedSections", JSON.stringify(all));
  }, ids);
  await open(page, "?page=settings");
}

interface WindowGeometry {
  innerHeight: number;
  docScrollHeight: number;
  windowScrollY: number;
  railTop: number;
  railHeight: number;
}

function windowGeometry(page: Page): Promise<WindowGeometry> {
  return page.evaluate(() => {
    const rail = document.querySelector<HTMLElement>(".rail")!.getBoundingClientRect();
    return {
      innerHeight: window.innerHeight,
      docScrollHeight: document.documentElement.scrollHeight,
      windowScrollY: Math.round(window.scrollY),
      railTop: Math.round(rail.top),
      railHeight: Math.round(rail.height),
    };
  });
}

/** 滚轮往下推一大截（用户手势）+ 程序滚动，都不该让窗口动 */
async function pushWindowDown(page: Page): Promise<void> {
  await page.mouse.move(720, 450);
  await page.mouse.wheel(0, 4000);
  await page.evaluate(() => { window.scrollTo(0, 10 ** 7); });
  await page.waitForTimeout(150);
}

function expectRailSpansViewport(geometry: WindowGeometry): void {
  expect(geometry.railTop).toBe(0);
  expect(geometry.railHeight).toBe(geometry.innerHeight);
  expect(geometry.windowScrollY).toBe(0);
  expect(geometry.docScrollHeight).toBe(geometry.innerHeight);
}

test("设置页：.shell-main 的滚动高度紧贴最后一区，滚到底后窗口不再多滚一截", async ({ page }) => {
  test.setTimeout(150_000);
  await openSettingsExpanded(page);
  await page.locator(".shell-main").evaluate((el) => { el.scrollTop = 10 ** 7; });
  await page.waitForTimeout(200);

  const measured = await page.evaluate(() => {
    const main = document.querySelector<HTMLElement>(".shell-main")!;
    const mainTop = main.getBoundingClientRect().top;
    const sections = Array.from(document.querySelectorAll<HTMLElement>(".settings-page > .settings-fold"));
    const last = sections[sections.length - 1];
    return {
      scrollHeight: main.scrollHeight,
      clientHeight: main.clientHeight,
      // 最后一区底边在滚动内容坐标系里的位置
      lastSectionBottom: last.getBoundingClientRect().bottom - mainTop + main.scrollTop,
      lastSectionId: last.id,
      atBottom: Math.round(main.scrollTop + main.clientHeight),
    };
  });

  // 全展开的设置页确实滚得起来（不然这条断言是空的）
  expect(measured.scrollHeight).toBeGreaterThan(measured.clientHeight * 2);
  expect(measured.lastSectionId).toBe("settings-daily_loop");
  // 内容下方最多一个 padding 单位（48px 的页面下内边距），不许多出一屏空白
  expect(measured.scrollHeight - measured.lastSectionBottom).toBeLessThanOrEqual(PAGE_PADDING_BOTTOM + 1);
  // 滚到底 = 内容的底：容器没有比内容更高
  expect(Math.abs(measured.atBottom - measured.scrollHeight)).toBeLessThanOrEqual(1);

  await pushWindowDown(page);
  expectRailSpansViewport(await windowGeometry(page));
});

test("横幅摞高过一屏：溢出在壳里夹掉，文档不滚、导航栏灰底仍贯穿视口", async ({ page }) => {
  test.setTimeout(150_000);
  await openSettingsExpanded(page);
  // 真实横幅（离线 / 管线 / 自我改进 / 每日整理 / 诊断条）的极端摞高：压不扁的 flex 项，高过一屏
  await page.evaluate(() => {
    const body = document.querySelector<HTMLElement>(".shell-body")!;
    const tall = document.createElement("div");
    tall.dataset.testBanner = "tall";
    tall.style.height = "1400px";
    tall.style.flex = "none";
    body.insertBefore(tall, body.querySelector(".shell-main"));
  });
  await pushWindowDown(page);
  expectRailSpansViewport(await windowGeometry(page));
});

for (const [label, url] of [["看板", ""], ["会议纪要", "?page=recaps"], ["回收站", "?page=trash"], ["永久性完成", "?page=done"]] as const) {
  test(`${label}页：文档不滚、导航栏灰底贯穿视口`, async ({ page }) => {
    test.setTimeout(150_000);
    await open(page, url);
    await pushWindowDown(page);
    expectRailSpansViewport(await windowGeometry(page));
    // 页面内容仍然只在 .shell-main（或看板自己的列）里滚——容器不比内容高
    const tight = await page.evaluate(() => {
      const main = document.querySelector<HTMLElement>(".shell-main")!;
      const mainTop = main.getBoundingClientRect().top;
      let contentBottom = 0;
      for (const child of Array.from(main.children)) {
        if (getComputedStyle(child as HTMLElement).position === "fixed") continue;
        contentBottom = Math.max(contentBottom, child.getBoundingClientRect().bottom - mainTop + main.scrollTop);
      }
      return { scrollHeight: main.scrollHeight, clientHeight: main.clientHeight, contentBottom };
    });
    expect(tight.scrollHeight).toBeLessThanOrEqual(Math.max(tight.clientHeight, tight.contentBottom + PAGE_PADDING_BOTTOM) + 1);
  });
}

// 看板列各自滚动（owner 决策 D42，CONTRACT §54.4 2026-09-06 追记；原生 Kanban.swift：横向 ScrollView 里每列
// 各一个纵向 ScrollView，窗口从不整体滚）——真浏览器判例：
//   · 在提案列上滚滚轮 → 只有这一列的卡片列表动了：这一列的列头与列顶输入框一像素不动、其余列的列头与首卡不动、
//     文档与 .shell-main 都没滚（document 不再是滚动容器）；
//   · 键盘：把焦点落到这一列视口外的卡上 → 浏览器把这一列滚过去（卡进入列表的可视框），列头照旧钉着；
//   · 首卡的焦点环（2px outline，在卡的 border box 之外）落在滚动容器的 padding box 里——滚动容器只画 padding box，
//     顶上不留 2px 就把环的上边切掉（#274 审查抓到）；卡自己的位置一像素不动；
//   · 多选态的操作条横贯看板底部、整条在视口里（此前它是横排里的一个 flex 项，被排到最右列之后、视口之外）；
//   · 永久性完成书立条展开后：搜索框是滚动容器的兄弟、钉在条顶，滚行列表时它不动（与列顶输入框同款；#274 审查抓到）。
// 数据 = demo initial 场景（提案列四张卡 + 一张占位；600px 高的视口下列表必然溢出）；书立条那条用 page.route
// 往 /api/board 里注 20 条 archived 行（demo seed 的 archived 是空的）。
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

/** 短视口：列表必然比列高，滚得起来 */
async function openBoard(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem("zai.theme", "light");
    window.localStorage.setItem("zai.lang", "zh");
  });
  await page.setViewportSize({ width: 1440, height: 600 });
  await page.goto(`${server.baseURL}/`);
  await page.getByRole("heading", { level: 1 }).waitFor();
  await page.locator(".shell-main").waitFor();
  await page.locator(".board-column").first().waitFor();
  await page.waitForLoadState("networkidle");
}

interface LaneGeometry {
  /** 每列列头的 top */
  headerTops: number[];
  /** 每列列表第一个子节点的 top（卡 / 占位 / 空态） */
  firstItemTops: number[];
  /** 每列列表的 scrollTop */
  listScrollTops: number[];
  /** 提案列输入框的 top */
  composerTop: number;
  docScrollY: number;
  mainScrollTop: number;
  docScrollHeight: number;
  innerHeight: number;
}

function measure(page: Page): Promise<LaneGeometry> {
  return page.evaluate(() => {
    const columns = Array.from(document.querySelectorAll<HTMLElement>(".board-column"));
    const lists = columns.map((c) => c.querySelector<HTMLElement>(".column-list")!);
    return {
      headerTops: columns.map((c) => c.querySelector(".column-header")!.getBoundingClientRect().top),
      firstItemTops: lists.map((l) => l.firstElementChild!.getBoundingClientRect().top),
      listScrollTops: lists.map((l) => l.scrollTop),
      composerTop: document.querySelector(".board-column .lane-composer")!.getBoundingClientRect().top,
      docScrollY: window.scrollY,
      mainScrollTop: document.querySelector<HTMLElement>(".shell-main")!.scrollTop,
      docScrollHeight: document.documentElement.scrollHeight,
      innerHeight: window.innerHeight,
    };
  });
}

test("滚提案列 → 只有这一列的卡动；它的列头 / 输入框与其余列一像素不动；文档不滚", async ({ page }) => {
  await openBoard(page);
  const list = page.locator(".board-column .column-list").first();
  const before = await measure(page);
  // 文档本身已不是滚动容器：整页恰好一屏高
  expect(before.docScrollHeight).toBe(before.innerHeight);
  expect(before.listScrollTops.every((t) => t === 0)).toBe(true);
  // 看板层只横向滚：没有任何东西（含两根收起的书立条）把 .board-main 撑出纵向溢出
  const boardOverflow = await page.locator(".board-main").evaluate((el) => ({
    vertical: el.scrollHeight - el.clientHeight,
    horizontal: el.scrollWidth - el.clientWidth,
  }));
  expect(boardOverflow.vertical).toBe(0);
  expect(boardOverflow.horizontal).toBeGreaterThan(0);
  // 提案列确实溢出（不然这个判例什么都证明不了）
  expect(await list.evaluate((el) => el.scrollHeight - el.clientHeight)).toBeGreaterThan(100);

  await list.hover();
  await page.mouse.wheel(0, 240);
  await expect.poll(() => list.evaluate((el) => el.scrollTop)).toBeGreaterThan(100);

  const after = await measure(page);
  // 这一列的列头与输入框没动，它的首卡上移了
  expect(after.headerTops[0]).toBe(before.headerTops[0]);
  expect(after.composerTop).toBe(before.composerTop);
  expect(after.firstItemTops[0]).toBeLessThan(before.firstItemTops[0] - 100);
  // 其余列：列头、首卡、scrollTop 都没动
  for (let i = 1; i < before.headerTops.length; i += 1) {
    expect(after.headerTops[i]).toBe(before.headerTops[i]);
    expect(after.firstItemTops[i]).toBe(before.firstItemTops[i]);
    expect(after.listScrollTops[i]).toBe(0);
  }
  // 文档与 .shell-main 都没滚
  expect(after.docScrollY).toBe(0);
  expect(after.mainScrollTop).toBe(0);
  // 输入框仍在视口里（列顶钉住的意义所在）
  const composer = page.locator(".board-column .lane-composer textarea").first();
  await expect(composer).toBeInViewport();
});

test("键盘：焦点落到列视口外的卡上 → 这一列自己滚过去，列头仍钉着", async ({ page }) => {
  await openBoard(page);
  const column = page.locator(".board-column").first();
  const list = column.locator(".column-list");
  const lastCard = list.locator("article.task-card").last();
  const before = await measure(page);
  // 最后一张卡在列表可视框之下
  const listBox = (await list.boundingBox())!;
  const cardBoxBefore = (await lastCard.boundingBox())!;
  expect(cardBoxBefore.y + cardBoxBefore.height).toBeGreaterThan(listBox.y + listBox.height);

  await lastCard.focus();
  await expect(lastCard).toBeFocused();
  await expect.poll(() => list.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
  const cardBoxAfter = (await lastCard.boundingBox())!;
  expect(cardBoxAfter.y).toBeGreaterThanOrEqual(listBox.y - 1);
  expect(cardBoxAfter.y + cardBoxAfter.height).toBeLessThanOrEqual(listBox.y + listBox.height + 1);

  const after = await measure(page);
  expect(after.headerTops[0]).toBe(before.headerTops[0]);
  expect(after.composerTop).toBe(before.composerTop);
  expect(after.docScrollY).toBe(0);
  expect(after.mainScrollTop).toBe(0);
});

test("首卡的焦点环不被滚动容器切掉：每列 scrollTop 0 时列表的 padding box 比首卡的 border box 至少高出 2px", async ({ page }) => {
  await openBoard(page);
  const columns = page.locator(".board-column");
  const n = await columns.count();
  for (let i = 0; i < n; i += 1) {
    const column = columns.nth(i);
    const list = column.locator(".column-list");
    const card = list.locator("article.task-card").first();
    if ((await card.count()) === 0) continue;
    await card.focus();
    await expect(card).toBeFocused();
    // 聚焦首卡不引发列表滚动（它已在可视框里）
    expect(await list.evaluate((el) => el.scrollTop)).toBe(0);
    const listBox = (await list.boundingBox())!;
    const cardBox = (await card.boundingBox())!;
    // 环画在 border box 之外 2px：滚动容器的可视框（padding box）顶边必须在卡顶之上 ≥ 2px，否则环的上边被裁
    expect(cardBox.y - listBox.y).toBeGreaterThanOrEqual(2);
    // 左右两边同理（负外边距 + 同宽内边距把滚动容器撑到列的全宽）
    expect(cardBox.x - listBox.x).toBeGreaterThanOrEqual(2);
    expect(listBox.x + listBox.width - (cardBox.x + cardBox.width)).toBeGreaterThanOrEqual(2);
  }
});

test("永久性完成书立条：展开后搜索框钉在条顶，滚行列表时不动", async ({ page }) => {
  // demo seed 的 archived 是空的：注 20 条进 /api/board，600px 高的条必然溢出
  await page.route("**/api/board", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.archived = Array.from({ length: 20 }, (_, i) => ({
      id: `R-9${String(i).padStart(2, "0")}`, title: `archived demo card ${i}`, summary: "demo", kind: "debt",
      archived_at: "2026-09-01T10:00:00Z", archive_reason: "user", prev_status: "detected",
    }));
    body.counts = { ...(body.counts ?? {}), archived: 20 };
    await route.fulfill({ response, json: body });
  });
  await openBoard(page);
  await page.locator(".backlog-strip.is-archive .backlog-strip-toggle").click();
  const strip = page.locator(".backlog-strip.is-archive");
  const list = strip.locator(".backlog-strip-list");
  const search = strip.getByRole("searchbox");
  await expect(search).toBeVisible();
  // 搜索框是滚动容器的兄弟（条的直接子项），不在列表里
  expect(await search.evaluate((el) => el.parentElement!.classList.contains("backlog-strip"))).toBe(true);
  expect(await list.evaluate((el) => el.querySelector("input"))).toBeNull();
  expect(await list.evaluate((el) => el.scrollHeight - el.clientHeight)).toBeGreaterThan(100);
  const searchBefore = (await search.boundingBox())!;
  const firstRowBefore = (await list.locator("article.task-card").first().boundingBox())!;

  await list.hover();
  await page.mouse.wheel(0, 300);
  await expect.poll(() => list.evaluate((el) => el.scrollTop)).toBeGreaterThan(100);

  const searchAfter = (await search.boundingBox())!;
  const firstRowAfter = (await list.locator("article.task-card").first().boundingBox())!;
  expect(searchAfter.y).toBe(searchBefore.y);
  expect(firstRowAfter.y).toBeLessThan(firstRowBefore.y - 100);
  await expect(search).toBeInViewport();
  // 看板层仍无纵向溢出（展开的条没把 .board-main 撑高）
  expect(await page.locator(".board-main").evaluate((el) => el.scrollHeight - el.clientHeight)).toBe(0);
});

test("多选态：操作条横贯看板底部、整条在视口里", async ({ page }) => {
  await openBoard(page);
  await page.getByRole("button", { name: "选择", exact: true }).click();
  const bar = page.getByRole("toolbar", { name: "多选操作" });
  await expect(bar).toBeVisible();
  await expect(bar).toBeInViewport({ ratio: 1 });
  const barBox = (await bar.boundingBox())!;
  const pageBox = (await page.locator(".board-page").boundingBox())!;
  const viewport = page.viewportSize()!;
  // 与看板同宽、贴在看板底
  expect(Math.abs(barBox.x - pageBox.x)).toBeLessThan(1);
  expect(Math.abs(barBox.width - pageBox.width)).toBeLessThan(1);
  expect(Math.abs(barBox.y + barBox.height - viewport.height)).toBeLessThan(1);
  // 列区在它上方，不被盖住
  const firstColumn = (await page.locator(".board-column").first().boundingBox())!;
  expect(firstColumn.y + firstColumn.height).toBeLessThanOrEqual(barBox.y + 0.5);
});

// 顶栏单行的几何判例（CONTRACT §49 追记 2026-09-04，D31）——真浏览器量布局，jsdom 量不出：
//   · 左翼最长（陈旧新鲜度 + 已回滚部署 + 设备标签，zh / en）+ 壳桥在场（右侧多两颗开关）时，
//     三档各取一个视口：槽位里的每个控件都落在槽位矩形内（不被裁）、顶栏仍 52px 一行、标题完整；
//     tight 再把搜索框展开量一次（basis 0 吃余量，不许把标题挤掉）；
//   · 回收站页复用 .chrome-search 但容器竖排——flex-basis 不许变成高度；
//   · tight 搜索框展开着点「筛选」/「提建议」：第一下就开（pointerdown 不抢焦点，见 FilterBar.tsx）；
//   · 提建议回执（成功句 / server 原文错误句，长度不可预算）显示的 4 s 里顶栏不横向溢出、标题不裁、右翼在视口内、
//     整页无横向滚动条——回执是 portal 到 body 的 fixed 小条，不是槽位 max-content 下限来源 .chrome-filterbar 的子元素。
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

// —— 提建议回执不撑顶栏（#206 review）——
// 修复前回执是 nowrap 条的直接子元素、条又是槽位 max-content 下限的来源：en + 壳 @720 成功句就把标题裁 36px；
// 下面这句 ≈1300px 的 server 原文（describeActionError 照登、长度不可预算）让顶栏横向溢出 1012–1377px（三档四个视口）、
// 标题裁 130–159px、右翼被推出视口同样的像素数、整页出横向滚动条——持续 4 s。
const LONG_SERVER_ERROR = "inbox_writer rejected the action: state/inbox is not writable — "
  + "check the permissions of /Users/owner/Library/Application Support/zelin-ai-assistant/state/inbox/ "
  + "and retry; details: [Errno 13] Permission denied while creating feedback-2026-09-04T21-32-50Z.md "
  + "(this is the verbatim server message, passed through without truncation)";

const FEEDBACK = {
  zh: { button: "提建议", placeholder: "建议内容…", send: "发送", ok: "已记录建议，感谢" },
  en: { button: "Send feedback", placeholder: "Your feedback…", send: "Send", ok: "Feedback recorded" },
} as const;

interface HeaderOverflow {
  density: string | undefined;
  headerHeight: number;
  /** .shell-header 横向溢出量（scrollWidth − clientWidth） */
  headerOverflow: number;
  /** 标题被左翼裁掉的像素（标题右沿超出左翼右沿） */
  titleClip: number;
  titleInsideLeft: boolean;
  /** 右翼（连接点 / 语言 / 主题 / 录制 / 字幕）右沿超出视口的像素 */
  rightOverflow: number;
  /** 整页横向溢出量（documentElement.scrollWidth − innerWidth） */
  pageOverflow: number;
  noteInBar: boolean;
  noteInsideViewport: boolean;
}

function measureOverflow(page: Page): Promise<HeaderOverflow> {
  return page.evaluate(() => {
    const header = document.querySelector<HTMLElement>(".shell-header")!;
    const left = document.querySelector<HTMLElement>(".shell-header-left")!.getBoundingClientRect();
    const title = document.querySelector<HTMLElement>(".shell-title")!.getBoundingClientRect();
    const right = document.querySelector<HTMLElement>(".shell-header-right")!.getBoundingClientRect();
    const note = document.querySelector<HTMLElement>(".chrome-feedback-note")!;
    const noteRect = note.getBoundingClientRect();
    return {
      density: header.dataset.density,
      headerHeight: header.getBoundingClientRect().height,
      headerOverflow: header.scrollWidth - header.clientWidth,
      titleClip: Math.max(0, Math.round(title.right - left.right)),
      titleInsideLeft: title.right <= left.right + 0.5 && title.left >= left.left - 0.5,
      rightOverflow: Math.max(0, Math.round(right.right - window.innerWidth)),
      pageOverflow: document.documentElement.scrollWidth - window.innerWidth,
      noteInBar: note.closest(".chrome-filterbar") !== null,
      noteInsideViewport: noteRect.left >= 0 && noteRect.right <= window.innerWidth && noteRect.width > 0,
    };
  });
}

async function sendFeedback(page: Page, lang: "zh" | "en", status: 200 | 500) {
  await page.route("**/api/actions*", (route) => route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(status === 200 ? { ok: true } : { error: { code: "INBOX_WRITE_FAILED", message: LONG_SERVER_ERROR } }),
  }));
  await page.getByRole("button", { name: FEEDBACK[lang].button, exact: true }).click();
  await page.getByPlaceholder(FEEDBACK[lang].placeholder).fill("layout probe");
  await page.getByRole("button", { name: FEEDBACK[lang].send, exact: true }).click();
  const note = page.locator(".chrome-feedback-note");
  await expect(note).toBeVisible();
  await expect(note).toHaveText(status === 200 ? FEEDBACK[lang].ok : LONG_SERVER_ERROR);
}

// 一次比对整组几何：失败时把 overflow / clip 的像素数一并打出来
async function expectHeaderUnmovedByNote(page: Page) {
  const geometry = await measureOverflow(page);
  expect(geometry).toMatchObject({
    noteInBar: false,
    headerOverflow: 0,
    titleClip: 0,
    titleInsideLeft: true,
    rightOverflow: 0,
    pageOverflow: 0,
    headerHeight: 52,
    noteInsideViewport: true,
  });
  return geometry;
}

// zh @1440 无壳 = full（golden 条件）；zh / en + 壳 @1440 = compact；en + 壳 @720 = tight
for (const scene of [
  { lang: "zh", width: 1440, shell: false, density: "full" },
  { lang: "zh", width: 1440, shell: true, density: "compact" },
  { lang: "en", width: 1440, shell: true, density: "compact" },
  { lang: "en", width: 720, shell: true, density: "tight" },
] as const) {
  test(`提建议被 server 拒绝（原文 ≈1300px）· ${scene.lang}${scene.shell ? " + 壳" : ""} @${scene.width}：回执不撑顶栏、标题完整、右翼在视口内、整页不横滚`, async ({ page }) => {
    await open(page, { lang: scene.lang, width: scene.width, shell: scene.shell });
    expect((await measureHeader(page)).density).toBe(scene.density);
    await sendFeedback(page, scene.lang, 500);
    await expectHeaderUnmovedByNote(page);
  });
}

test("提建议成功句 · en + 壳 @720（tight）：回执显示时标题裁 0px、顶栏不溢出", async ({ page }) => {
  await open(page, { lang: "en", width: 720, shell: true });
  await sendFeedback(page, "en", 200);
  const geometry = await expectHeaderUnmovedByNote(page);
  expect(geometry.density).toBe("tight");
});

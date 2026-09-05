// 每日整理横幅展开态的几何判例（CONTRACT §70.7，D33）——真浏览器量布局，jsdom 量不出：
//   · 「系统自检 N 条」点开后，每条的「首见 <日期>」是一行（span 只占一个 client rect）——修复前
//     .shell-banner-list li 是 flex 行、日期 span 没有 flex:none / nowrap，1280 宽就把「2026-09-」与「01」拆成两行；
//   · 横幅本体不横向溢出（flex-wrap 只在 is-open 时开，列表 flex-basis 100% 换到下一行）；
//   · 三计数全零 + 只有 advisories：横幅仍渲染，文案是「看板无变动」，没有回收站链接（没有可撤销的动作）。
// 数据 = demo initial 场景，/api/board 经 page.route 注入 maintenance（demo seed 没有这个键）；
// last_run_at 取请求当刻——横幅只认「本地今天」的运行。
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

// 五条真实形状的 advisory：文本长到 820 宽必然要挤日期列
const ADVISORIES = [
  { kind: "stuck_dispatch", ref: "claude_blind", fingerprint: "stuck_dispatch:claude_blind", first_seen: "2026-08-30",
    text: "派发卡死：3 张已批卡发不出去（claude_blind） — approved 卡 dispatch_attempts ≥ 3 或 dispatch_halted，根因 claude_blind：launchd 起的 claude 没有完全磁盘访问" },
  { kind: "doctor_fail", ref: "claude_blind", fingerprint: "doctor_fail:launchd claude", first_seen: "2026-08-30",
    text: "doctor 红灯：launchd claude — TCC：Operation not permitted，PermissionError: [Errno 1] 读 ~/Library/Application Support 失败" },
  { kind: "launchd_fault", ref: "", fingerprint: "launchd_fault:tcc_eperm", first_seen: "2026-09-01",
    text: "launchd 环境故障：tcc_eperm — ~/Library/Logs/zelin-ai-assistant/actd.log 末 200 行命中 Operation not permitted 的已知环境故障正则" },
  { kind: "log_loop", ref: "", fingerprint: "log_loop:a1b2c3d4e5", first_seen: "2026-09-02",
    text: "日志刷屏：同形报错 312 次 — state/actd.log 末 2000 行去时间戳后 PermissionError: [Errno 1] Operation not permitted 出现 312 次" },
  { kind: "event_anomaly", ref: "", fingerprint: "event_anomaly:dispatch_failed", first_seen: "2026-09-02",
    text: "事件风暴：dispatch_failed 今天 96 次 — 前七日中位数 4，超 5 倍；analytics/events.jsonl 近 8 天按日计数" },
];

async function open(page: Page, lang: "zh" | "en", width: number) {
  await page.addInitScript((lang) => {
    window.localStorage.setItem("zai.theme", "light");
    window.localStorage.setItem("zai.lang", lang);
  }, lang);
  await page.route("**/api/board", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    const nowS = Math.floor(Date.now() / 1000);
    body.maintenance = {
      phase: "idle",
      started_at: nowS - 600,
      last_run_at: nowS - 60,
      next_run_at: nowS + 80_000,
      last_result: { merged: 0, trashed: 0, proposals: 0, summaries: 0, errors: 0, advisories: ADVISORIES },
    };
    await route.fulfill({ response, json: body });
  });
  await page.setViewportSize({ width, height: 900 });
  await page.goto(`${server.baseURL}/`);
  await page.getByRole("heading", { level: 1 }).waitFor();
  await page.locator(".shell-main").waitFor();
  await page.waitForLoadState("networkidle");
}

const TOGGLE = { zh: /系统自检 5 条/, en: /5 self-check notes/ } as const;
const QUIET = { zh: "看板无变动", en: "nothing to change" } as const;

interface ListGeometry {
  /** 每条「首见」span 占的行数（高度 / 行高，四舍五入；flex 项是块盒，getClientRects 数不出行） */
  noteLines: number[];
  /** 每条的日期列是否落在该行 li 的右沿内 */
  noteInsideRow: boolean[];
  bannerOverflow: number;
  items: number;
}

function measureList(page: Page): Promise<ListGeometry> {
  return page.evaluate(() => {
    const banner = document.querySelector<HTMLElement>(".shell-banner.is-info")!;
    const rows = Array.from(document.querySelectorAll<HTMLElement>(".shell-banner-list li"));
    const notes = rows.map((li) => li.querySelector<HTMLElement>(".shell-banner-note")!);
    return {
      noteLines: notes.map((n) => Math.round(n.getBoundingClientRect().height / parseFloat(getComputedStyle(n).lineHeight))),
      noteInsideRow: rows.map((li, i) => notes[i].getBoundingClientRect().right <= li.getBoundingClientRect().right + 0.5),
      bannerOverflow: banner.scrollWidth - banner.clientWidth,
      items: rows.length,
    };
  });
}

for (const lang of ["zh", "en"] as const) {
  for (const width of [1280, 820]) {
    test(`advisories 展开 · ${lang} @${width}：首见日期一行、横幅不溢出、全零文案无回收站链接`, async ({ page }) => {
      await open(page, lang, width);
      const banner = page.getByRole("status").filter({ has: page.getByRole("button", { name: TOGGLE[lang] }) });
      await expect(banner).toHaveAttribute("data-kind", "summary");
      await expect(banner).toContainText(QUIET[lang]);
      await expect(banner.getByRole("link")).toHaveCount(0);

      const toggle = banner.getByRole("button", { name: TOGGLE[lang] });
      await toggle.click();
      await expect(toggle).toHaveAttribute("aria-expanded", "true");
      await expect(banner.getByRole("listitem")).toHaveCount(ADVISORIES.length);

      const geometry = await measureList(page);
      expect(geometry.items).toBe(ADVISORIES.length);
      expect(geometry.noteLines).toEqual(ADVISORIES.map(() => 1));
      expect(geometry.noteInsideRow).toEqual(ADVISORIES.map(() => true));
      expect(geometry.bannerOverflow).toBe(0);
    });
  }
}

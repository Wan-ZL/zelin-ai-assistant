// README 截图的后端（CONTRACT §77.5：README 引用的 docs/images/*.png 必须是真渲染，不是占位）。
//
// 与 scripts/qa/readme_render.mjs、web/e2e/demoServer.ts 同一条链路：scripts/demo_seed.py 种临时
// AIASSISTANT_HOME（HOME 也指向它）→ 随机端口起 `python3 -m server` → Playwright 打开每一页拍一张。
// 数据全是虚构的会议纪要；时钟用 --now 与浏览器的固定时间钉住，重拍出来的相对时间不随运行日漂。
// 截图里机器相关的路径（本 checkout、临时 home、运行者的 ~）在拍之前改写成一台普通安装上的样子
// （~/Projects/zelin-ai-assistant、~）：README 不该带上拍图那台机器的用户名与临时目录，界面本身不动。
// 「关于」页的 /api/about 在浏览器侧改写版本与更新缓存（DEMO_ABOUT）：真答案是拍图 checkout 的
// `git describe`（开发分支上是「v… 领先 N 个提交，未发版」），而 README 要展示一台已装正式版、
// 检查到新版本的机器——「新版本 v… 可用 — 立即升级」那一行只在这个状态下出现。server 与界面都不动。
// **绝不碰生产 state/**。前置：web/dist 已构建（cd web && npm run build），chromium 已装
// （cd web && npx playwright install chromium）。
//
// 用法：
//   node scripts/qa/readme_shots.mjs            # 写到 docs/images/
//   node scripts/qa/readme_shots.mjs <out-dir>
import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import net from "node:net";
import { homedir, tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const PYTHON = process.env.PYTHON ?? "python3";
const OUT_DIR = path.resolve(process.argv[2] ?? path.join(REPO_ROOT, "docs", "images"));
// 固定时钟：demo_seed 以它为「现在」种数据，浏览器也停在同一刻（页面上的「x 分钟前」逐次一致）。
const NOW = "2026-10-21T16:30:00Z";

// 「关于」页的演示状态：装的是一个已发版的 tag，更新检查两小时前找到下一个补丁版（§26 / §79 的 payload 形状）。
const DEMO_RELEASES_URL = "https://github.com/Wan-ZL/zelin-ai-assistant/releases";
const DEMO_ABOUT = {
  version: "2.0.0",
  update_check: { checked_at: "2026-10-21T14:30:00Z", latest: "2.0.1", url: DEMO_RELEASES_URL },
  update_available: { current: "2.0.0", latest: "2.0.1", url: DEMO_RELEASES_URL,
    pkg_asset_url: null, manual_upgrade: false },
  check_enabled: true,
};

// 文件名 = README 引用的名字；page 为空 = 缺省页（会议纪要）。语言走 ?lang=（store.ts：本次会话的一次性覆写，
// 截图 / 演示用，不水合也不持久化）。pick = 会议纪要列表里点开第几条（第 0 条是「进行中」，没有正文可看）。
const SHOTS = [
  { file: "recaps-en-light.png", lang: "en", page: "", pick: 1 },
  { file: "recaps-zh-light.png", lang: "zh", page: "", pick: 1 },
  { file: "settings-en-light.png", lang: "en", page: "settings" },
  { file: "about-en-light.png", lang: "en", page: "about", about: DEMO_ABOUT },
  { file: "permissions-en-light.png", lang: "en", page: "permissions" },
];

function freePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.unref();
    probe.on("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const address = probe.address();
      probe.close(() => resolve(typeof address === "object" && address ? address.port : 0));
    });
  });
}

async function waitForServer(baseURL, child, timeoutMs = 90_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`server exited with code ${child.exitCode}`);
    try {
      if ((await fetch(`${baseURL}/api/health`)).ok) return;
    } catch {
      // 还没起来，接着等
    }
    await new Promise((r) => setTimeout(r, 120));
  }
  throw new Error(`server never answered on ${baseURL}/api/health`);
}

function seedHome() {
  const home = mkdtempSync(path.join(tmpdir(), "zai-readme-shots-"));
  const seed = spawnSync(PYTHON, [path.join(REPO_ROOT, "scripts", "demo_seed.py"), home,
    "--scene", "initial", "--now", NOW], { encoding: "utf-8" });
  if (seed.status !== 0) throw new Error(`demo_seed.py failed: ${seed.stderr || seed.stdout}`);
  // 首次运行向导会整页顶掉别的页（§68.5）——与 e2e 同一个「向导已完成」标记。
  mkdirSync(path.join(home, "state"), { recursive: true });
  writeFileSync(path.join(home, "state", "setup_done.json"),
    JSON.stringify({ completed_at: "2026-09-02T12:00:00Z" }));
  return home;
}

const INSTALL_PATH = "~/Projects/zelin-ai-assistant";

/** 拍图机器上的路径 → 普通安装上的路径（长的先换：临时 home 与 checkout 都可能在 ~ 底下）。 */
function pathRewrites(home) {
  const pairs = [];
  for (const dir of [REPO_ROOT, home]) {
    for (const variant of new Set([dir, realpathSync(dir)])) pairs.push([variant, INSTALL_PATH]);
  }
  pairs.push([homedir(), "~"]);
  return pairs.sort((a, b) => b[0].length - a[0].length);
}

function rewriteText(pairs) {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    let text = node.nodeValue;
    for (const [from, to] of pairs) text = text.split(from).join(to);
    if (text !== node.nodeValue) node.nodeValue = text;
  }
}

async function capture(browser, baseURL, shot, rewrites) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 },
    deviceScaleFactor: 2, colorScheme: "light" });
  const page = await context.newPage();
  await page.clock.setFixedTime(new Date(NOW));
  if (shot.about) {
    // 真 server 先答（home / repo 等字段照旧，随后被 rewriteText 改写），只覆盖版本与更新缓存。
    await page.route("**/api/about", async (route) => {
      const response = await route.fetch();
      await route.fulfill({ response, json: { ...(await response.json()), ...shot.about } });
    });
  }
  const query = new URLSearchParams({ lang: shot.lang });
  if (shot.page) query.set("page", shot.page);
  await page.goto(`${baseURL}/?${query}`);
  await page.locator(".shell-main").waitFor();
  await page.waitForLoadState("networkidle");
  if (shot.pick !== undefined) await page.locator(".recap-row").nth(shot.pick).click();
  await page.waitForTimeout(400);
  await page.evaluate(rewriteText, rewrites);
  await page.screenshot({ path: path.join(OUT_DIR, shot.file) });
  await context.close();
  process.stdout.write(`${shot.file}\n`);
}

async function main() {
  const { chromium } = await import(path.join(REPO_ROOT, "web", "node_modules", "playwright", "index.mjs"));
  mkdirSync(OUT_DIR, { recursive: true });
  const home = seedHome();
  const port = await freePort();
  const child = spawn(PYTHON, ["-m", "server"], {
    cwd: REPO_ROOT,
    env: { ...process.env, HOME: home, AIASSISTANT_HOME: home, ZAI_PORT: String(port), PYTHONPATH: REPO_ROOT },
    stdio: ["ignore", "ignore", "ignore"],
  });
  const baseURL = `http://127.0.0.1:${port}`;
  let browser;
  try {
    await waitForServer(baseURL, child);
    browser = await chromium.launch();
    const rewrites = pathRewrites(home);
    for (const shot of SHOTS) await capture(browser, baseURL, shot, rewrites);
  } finally {
    if (browser) await browser.close().catch(() => {});
    child.kill("SIGTERM");
    rmSync(home, { recursive: true, force: true });
  }
}

main().catch((error) => {
  process.stderr.write(`${error?.stack || error}\n`);
  process.exit(1);
});

// readme_audit.py 的 `--render` 后端（CONTRACT §58 / §66）：把看板的每一页真渲染一遍，
// 把可见文本打到 stdout，让 README 里引号括起来的 UI 文案有一条「屏幕上真有这几个字」的判据。
//
// 与 web/e2e/demoServer.ts 同一条链路（scripts/demo_seed.py 种临时 AIASSISTANT_HOME →
// 随机端口起 `python3 -m server` → Playwright 打开），只是不进 Playwright test runner：
// 这支脚本被 python 侧 subprocess 调用，退出码 0 = stdout 可信。**绝不碰生产 state/**。
//
// 用法（页面用逗号分隔的 query 串，空串 = 看板首页）：
//   node scripts/qa/readme_render.mjs "" "?page=settings,?page=skills"
import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import net from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const PYTHON = process.env.PYTHON ?? "python3";
const PAGES = (process.argv[2] ?? "").split(",");

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

async function waitForBoard(baseURL, child, timeoutMs = 90_000) {
  const deadline = Date.now() + timeoutMs;
  let last = "no response yet";
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`server exited with code ${child.exitCode}`);
    try {
      const res = await fetch(`${baseURL}/api/board`);
      if (res.ok) return;
      last = `HTTP ${res.status}`;
    } catch (error) {
      last = String(error);
    }
    await new Promise((r) => setTimeout(r, 120));
  }
  throw new Error(`server never answered on ${baseURL}/api/board (last: ${last})`);
}

async function main() {
  const { chromium } = await import(path.join(REPO_ROOT, "web", "node_modules", "playwright", "index.mjs"));
  const home = mkdtempSync(path.join(tmpdir(), "zaa-readme-render-"));
  const seed = spawnSync(PYTHON, [path.join(REPO_ROOT, "scripts", "demo_seed.py"), home, "--scene", "initial"],
    { encoding: "utf-8" });
  if (seed.status !== 0) throw new Error(`demo_seed.py failed: ${seed.stderr || seed.stdout}`);
  // 首次运行向导会整页顶掉看板（§68.5）——与 e2e 同一个「向导已完成」标记。
  mkdirSync(path.join(home, "state"), { recursive: true });
  writeFileSync(path.join(home, "state", "setup_done.json"),
    JSON.stringify({ completed_at: "2026-09-02T12:00:00Z" }));
  const port = await freePort();
  const child = spawn(PYTHON, ["-m", "server"], {
    cwd: REPO_ROOT,
    env: { ...process.env, HOME: home, AIASSISTANT_HOME: home, ZAI_PORT: String(port), PYTHONPATH: REPO_ROOT },
    stdio: ["ignore", "ignore", "ignore"],
  });
  const baseURL = `http://127.0.0.1:${port}`;
  let browser;
  try {
    await waitForBoard(baseURL, child);
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    // 两种语言都渲一遍：README 的 UI 文案可能引任一侧（§49 语言单源）。
    for (const lang of ["en", "zh"]) {
      for (const query of PAGES) {
        await page.addInitScript((l) => window.localStorage.setItem("zai.lang", l), lang);
        await page.goto(`${baseURL}/${query}`);
        await page.locator(".shell-main").waitFor();
        await page.waitForLoadState("networkidle");
        process.stdout.write(await page.locator("body").innerText());
        process.stdout.write("\n");
      }
    }
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

// 演示视频的录屏机（CONTRACT §77 拟；QA 面 §58）。
// 像素只来自真 app：Playwright（chromium，视口 1440×900，录像开）驱动 `python3 -m server`，
// 数据 = scripts/demo_seed.py 种进临时 HOME 的虚构 demo 数据——与 web/e2e/demoServer.ts 同一条链路
// （这里用 node 原样复刻它的三步：种数据 → 写 setup_done.json → 随机端口起 server），
// 一个镜头一个 context / 一个 .webm，落到 M/raw/<shot id>.webm。
// 用法：node scripts/media/record.mjs --out <M>/raw [--only 01-dashboard,04-approve]
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..", "..");
const PYTHON = process.env.PYTHON ?? "python3";
// HOME 换成临时目录会把 **user site-packages**（PyYAML 就住在那儿）一起藏掉——server 于是
// 在技能页上报「PyYAML is required to read skills/index.yaml」，录出来的是个假报错。
// 把真 HOME 下的 user site 目录显式塞进 PYTHONPATH：录屏里的 app 与用户机上的 app 同一个依赖面。
const USER_SITE = spawnSync(PYTHON, ["-c", "import site; print(site.getusersitepackages())"], { encoding: "utf-8" }).stdout?.trim() ?? "";
const { chromium } = await import(path.join(REPO_ROOT, "web", "node_modules", "@playwright", "test", "index.mjs"));

function arg(name, fallback = null) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const OUT = path.resolve(arg("out", path.join(REPO_ROOT, "media-raw")));
const ONLY = (arg("only", "") || "").split(",").filter(Boolean);
const TAIL_MS = 2000; // 每段多录 2 s：剪辑时按计划时长裁掉尾巴，绝不出现黑帧不足

function freePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.unref();
    probe.on("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const address = probe.address();
      const port = typeof address === "object" && address ? address.port : 0;
      probe.close(() => resolve(port));
    });
  });
}

async function waitForBoard(baseURL, child, timeoutMs = 90_000) {
  const deadline = Date.now() + timeoutMs;
  let last = "no response yet";
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`server exited early with code ${child.exitCode}`);
    try {
      const res = await fetch(`${baseURL}/api/board`);
      if (res.ok) return;
      last = `HTTP ${res.status}`;
    } catch (error) {
      last = String(error);
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error(`server silent on ${baseURL}/api/board (${last})`);
}

/** 种 demo 数据 + 起 server（临时 HOME 只在 /tmp，收尾删除）。 */
async function startDemoServer(scene) {
  const home = mkdtempSync("/tmp/zaa-media-");
  const seed = spawnSync(PYTHON, [path.join(REPO_ROOT, "scripts", "demo_seed.py"), home, "--scene", scene], { encoding: "utf-8" });
  if (seed.status !== 0) throw new Error(`demo_seed.py failed: ${seed.stderr || seed.stdout}`);
  mkdirSync(path.join(home, "state"), { recursive: true });
  writeFileSync(path.join(home, "state", "setup_done.json"), JSON.stringify({ completed_at: "2026-09-02T12:00:00Z" }));
  // skill 商店的真源是 checkout 里的 skills/（act/lib/skills.py 从 AIASSISTANT_HOME 找它）：临时 home 里
  // 软链到本 worktree 的那一份，技能页上就是真实清单，而不是一条「读不到 index.yaml」的假报错。
  symlinkSync(path.join(REPO_ROOT, "skills"), path.join(home, "skills"));
  const port = await freePort();
  const child = spawn(PYTHON, ["-m", "server"], {
    cwd: REPO_ROOT,
    env: {
      ...process.env, HOME: home, AIASSISTANT_HOME: home, ZAI_PORT: String(port), PYTHONUNBUFFERED: "1",
      PYTHONPATH: [REPO_ROOT, USER_SITE].filter(Boolean).join(path.delimiter),
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  const baseURL = `http://127.0.0.1:${port}`;
  try {
    await waitForBoard(baseURL, child);
  } catch (error) {
    child.kill();
    rmSync(home, { recursive: true, force: true });
    throw error;
  }
  return { baseURL, home, stop() { child.kill(); rmSync(home, { recursive: true, force: true }); } };
}

/** 一步步演；找不到目标不崩整支片子——记一行 warn，镜头照录（诚实优先于好看）。 */
async function runStep(page, step, warns) {
  const locator = step.selector ? page.locator(step.selector).first()
    : step.placeholder ? page.getByPlaceholder(step.placeholder).first()
    : step.role ? page.getByRole(step.role, { name: step.name }).first()
    : step.text ? page.getByText(step.text).first() : null;
  try {
    switch (step.do) {
      case "wait": await page.waitForTimeout(step.ms ?? 1000); return;
      case "into": await locator.scrollIntoViewIfNeeded({ timeout: 5000 }); return;
      case "hover": await locator.hover({ timeout: 5000 }); return;
      case "click": await locator.click({ timeout: 5000 }); return;
      case "dblclick": await locator.dblclick({ timeout: 5000 }); return;
      case "type": await locator.pressSequentially(step.text, { delay: step.delay ?? 60, timeout: 15000 }); return;
      case "scroll":
        await page.evaluate(([sel, by]) => {
          const el = sel ? document.querySelector(sel) : null;
          (el ?? document.scrollingElement).scrollBy({ top: by, behavior: "smooth" });
        }, [step.selector ?? null, step.by ?? 200]);
        return;
      default: throw new Error(`unknown step ${step.do}`);
    }
  } catch (error) {
    warns.push(`${step.do}:${step.selector ?? step.placeholder ?? step.name ?? ""} → ${String(error).split("\n")[0]}`);
  }
}

async function recordShot(browser, server, shot) {
  const warns = [];
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    locale: "zh-CN",
    // 双击接管的降级路要写剪贴板（壳没在跑 → 复制指令）：不给权限时 copyText 抛错，卡上那行回执就不出现
    permissions: ["clipboard-read", "clipboard-write"],
    recordVideo: { dir: path.join(OUT, ".tmp"), size: { width: 1440, height: 900 } },
  });
  await context.addInitScript(() => {
    window.localStorage.setItem("zai.theme", "light");
    window.localStorage.setItem("zai.lang", "zh");
  });
  const page = await context.newPage();
  const started = Date.now();
  await page.goto(`${server.baseURL}${shot.url ?? "/"}`, { waitUntil: "domcontentloaded" });
  for (const step of shot.steps ?? []) await runStep(page, step, warns);
  const remaining = shot.seconds * 1000 + TAIL_MS - (Date.now() - started);
  if (remaining > 0) await page.waitForTimeout(remaining);
  const video = page.video();
  await page.close();
  await context.close();
  const raw = await video.path();
  const dest = path.join(OUT, `${shot.id}.webm`);
  renameSync(raw, dest);
  console.log(`shot ${shot.id}: ${((Date.now() - started) / 1000).toFixed(1)}s → ${dest}${warns.length ? ` (warn: ${warns.join(" | ")})` : ""}`);
  return { id: shot.id, warns };
}

const data = JSON.parse(readFileSync(path.join(HERE, "shots.json"), "utf-8"));
const shots = data.shots.filter((s) => !ONLY.length || ONLY.includes(s.id));
if (!existsSync(path.join(REPO_ROOT, "web", "dist", "index.html"))) {
  throw new Error("web/dist not built — run `npm run build` in web/ first (the server serves web/dist)");
}
mkdirSync(path.join(OUT, ".tmp"), { recursive: true });

const byScene = new Map();
for (const shot of shots) {
  const scene = shot.scene ?? "initial";
  if (!byScene.has(scene)) byScene.set(scene, []);
  byScene.get(scene).push(shot);
}

const browser = await chromium.launch();
const report = [];
try {
  for (const [scene, sceneShots] of byScene) {
    const server = await startDemoServer(scene);
    try {
      for (const shot of sceneShots) report.push(await recordShot(browser, server, shot));
    } finally {
      server.stop();
    }
  }
} finally {
  await browser.close();
  rmSync(path.join(OUT, ".tmp"), { recursive: true, force: true });
}
const warned = report.filter((r) => r.warns.length);
console.log(`RECORD shots=${report.length} warned=${warned.length} out=${OUT}`);

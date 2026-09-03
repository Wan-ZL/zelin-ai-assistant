// 视觉基线的后端：种 demo 数据到临时 AIASSISTANT_HOME（绝不碰生产 state/，ground rule 3），
// 在随机空闲端口起 `python3 -m server`，等 /api/board 通了再把 baseURL 交给 spec。
// 与 scripts/dev-preview.sh 同一条链路，只是端口随机、目录临时、结束即杀进程。
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import net from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const PYTHON = process.env.PYTHON ?? "python3";

export interface DemoServer {
  baseURL: string;
  home: string;
  stop(): void;
}

function freePort(): Promise<number> {
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

async function waitForBoard(baseURL: string, child: ChildProcess, timeoutMs = 15_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`server exited early with code ${child.exitCode}`);
    try {
      const res = await fetch(`${baseURL}/api/board`);
      if (res.ok) return;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error(`server did not answer on ${baseURL}/api/board within ${timeoutMs} ms`);
}

/** 种 `scene`（默认 initial）→ 起 server → 返回可用的 baseURL。调用方负责 stop()。 */
export async function startDemoServer(scene = "initial"): Promise<DemoServer> {
  const home = mkdtempSync(path.join(tmpdir(), "zai-visual-"));
  const seed = spawnSync(PYTHON, [path.join(REPO_ROOT, "scripts", "demo_seed.py"), home, "--scene", scene], {
    encoding: "utf-8",
  });
  if (seed.status !== 0) throw new Error(`demo_seed.py failed: ${seed.stderr || seed.stdout}`);
  const port = await freePort();
  const child = spawn(PYTHON, ["-m", "server"], {
    cwd: REPO_ROOT,
    env: { ...process.env, AIASSISTANT_HOME: home, ZAI_PORT: String(port), PYTHONPATH: REPO_ROOT },
    stdio: ["ignore", "ignore", "pipe"],
  });
  let stderr = "";
  child.stderr?.on("data", (chunk) => { stderr += String(chunk); });
  const baseURL = `http://127.0.0.1:${port}`;
  try {
    await waitForBoard(baseURL, child);
  } catch (error) {
    child.kill();
    throw new Error(`${(error as Error).message}\n${stderr}`);
  }
  return {
    baseURL,
    home,
    stop() {
      child.kill();
      rmSync(home, { recursive: true, force: true });
    },
  };
}

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

// 90 s：macOS CI runner 上 http.server 的 server_bind 会对 127.0.0.1 做 socket.getfqdn()
// 反查，runner 的 DNS 反查可以卡到 resolver 超时（数十秒）才放行——本机瞬时，CI 首跑实测
// 25 s 内零输出。等它，不改 server（那是 live 机器共享的代码路径，另案）。
async function waitForBoard(baseURL: string, child: ChildProcess, timeoutMs = 90_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let last = "no response yet";
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`server exited early with code ${child.exitCode}`);
    try {
      const res = await fetch(`${baseURL}/api/board`);
      if (res.ok) return;
      last = `HTTP ${res.status}: ${(await res.text()).slice(0, 300)}`;
    } catch (error) {
      last = String(error);
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error(`server did not answer OK on ${baseURL}/api/board within ${timeoutMs} ms (last: ${last})`);
}

/** 种 `scene`（默认 initial）→ 起 server → 返回可用的 baseURL。调用方负责 stop()。 */
export async function startDemoServer(scene = "initial"): Promise<DemoServer> {
  const home = mkdtempSync(path.join(tmpdir(), "zai-visual-"));
  const seed = spawnSync(PYTHON, [path.join(REPO_ROOT, "scripts", "demo_seed.py"), home, "--scene", scene], {
    encoding: "utf-8",
  });
  if (seed.status !== 0) throw new Error(`demo_seed.py failed: ${seed.stderr || seed.stdout}`);
  const port = await freePort();
  // HOME 也指向临时目录：设置页读 ~/.claude/settings.json（§59 全局默认）——golden 不许带上
  // 开发者机器的真实路径 / 模型名，CI runner 上也没有这个文件，两边一致 = 「文件不存在」态。
  const child = spawn(PYTHON, ["-m", "server"], {
    // stdout/stderr 都收：server 的横幅与访问日志走 stdout，起不来时一并进错误信息
    cwd: REPO_ROOT,
    env: {
      ...process.env, HOME: home, AIASSISTANT_HOME: home, ZAI_PORT: String(port), PYTHONPATH: REPO_ROOT,
      PYTHONUNBUFFERED: "1",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  child.stdout?.on("data", (chunk) => { output += String(chunk); });
  child.stderr?.on("data", (chunk) => { output += String(chunk); });
  const baseURL = `http://127.0.0.1:${port}`;
  try {
    await waitForBoard(baseURL, child);
  } catch (error) {
    child.kill();
    const version = spawnSync(PYTHON, ["--version"], { encoding: "utf-8" });
    throw new Error(`${(error as Error).message}\n[${PYTHON} = ${(version.stdout || version.stderr).trim()}]\n${output}`);
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

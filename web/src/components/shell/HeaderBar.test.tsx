// 顶栏行为测试（G7）：新鲜度阈值（镜像 Freshness.swift 的 90s 语义）、
// 主题切换（dataset + localStorage）、语言切换（store.setLanguage + zai.lang 持久化）、
// §56 部署状态小字（deploy_state 缺失自隐藏 / healthy 次级色 / 回滚警告色）。
// 页面入口（回收站 / 设置 …）已搬到左侧导航栏——判例在 NavRail.test.tsx（§54.4）。
// 三档密度（§49 追记 2026-09-04）：data-density 反映档位；compact 收设备标签；tight 把新鲜度 / 部署小字折进
// 连接点 tooltip、壳开关只留图标（title 说全「录制：状态词」）。jsdom 无 ResizeObserver → 不给 prop 时恒 full。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchBoard } from "../../api";
import { LanguageContext } from "../../i18n";
import { resetShellBridgeForTests, type ShellState } from "../../shellBridge";
import { getState, refreshBoard, resetStoreForTests } from "../../store";
import type { Board, DeployState } from "../../types";
import { HeaderBar, type HeaderBarProps } from "./HeaderBar";

vi.mock("../../api", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../../api")>();
  return { ...mod, fetchBoard: vi.fn(), fetchCard: vi.fn() };
});

const fetchBoardMock = vi.mocked(fetchBoard);

function makeBoard(generatedAt: string, deployState?: DeployState): Board {
  return {
    generated_at: generatedAt,
    counts: {},
    needs_approval: [],
    running: [],
    needs_input: [],
    review: [],
    completed: [],
    debt: [],
    trash: [],
    ...(deployState ? { deploy_state: deployState } : {}),
  };
}

async function seedBoard(ageSeconds: number, deployState?: DeployState) {
  const generatedAt = new Date(Date.now() - ageSeconds * 1000).toISOString();
  fetchBoardMock.mockResolvedValue(makeBoard(generatedAt, deployState));
  await refreshBoard();
}

function renderHeader(language: "zh" | "en" = "en", props: HeaderBarProps = {}) {
  return render(
    <LanguageContext.Provider value={language}>
      <HeaderBar {...props} />
    </LanguageContext.Provider>,
  );
}

function shellState(): ShellState {
  return {
    recording: {
      available: true, on: false, mode: "off", engine_running: false, diagnosis: null,
      note: "", tcc_lost: false, screen_permission: true, resume_mode: "screen",
    },
    captions: {
      available: true, on: false, engine: "auto", paused: false, engine_dead: false,
      status_text: "", status_is_error: false,
      source: "both", translate: false, translate_direction: "auto", apple_locale: "zh",
      ark_model: "doubao-seed-1-6-flash", font_size: 24, opacity: 0.7,
    },
    permissions: { screen: "granted", microphone: "unknown", notifications: "unknown", vault: "unknown" },
    launch_at_login: false,
    hotkey: "⌃⌥Space",
    language: "en",
  };
}

describe("HeaderBar", () => {
  beforeEach(() => {
    resetStoreForTests();
    fetchBoardMock.mockReset();
    window.localStorage.clear();
    delete document.documentElement.dataset.theme;
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("新鲜数据（≤90s）：显示相对时间，无 actd 警告", async () => {
    await seedBoard(30);
    renderHeader();
    expect(screen.getByText((_, el) => el?.classList.contains("shell-freshness") === true && el.textContent === "Data generated just now")).toBeTruthy();
    expect(screen.queryByText(/actd may be down/)).toBeNull();
  });

  it("过期数据（>90s）：显示分钟数 + actd 可能未运行警告", async () => {
    await seedBoard(5 * 60);
    renderHeader();
    expect(screen.getByText((_, el) => el?.classList.contains("shell-freshness") === true && el.textContent === "Data generated 5 min ago — actd may be down")).toBeTruthy();
  });

  it("15s tick 自驱变陈旧：80s 时新鲜，跨过 90s 阈值后变警告", async () => {
    vi.useFakeTimers();
    await seedBoard(80);
    renderHeader();
    expect(screen.getByText((_, el) => el?.classList.contains("shell-freshness") === true && /Data generated 1m ago/.test(el.textContent ?? ""))).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(15_000); // 80s + 15s = 95s > 90s
    });
    expect(screen.getByText(/actd may be down/)).toBeTruthy();
  });

  it("主题切换：写 dataset.theme + localStorage zai.theme，往返翻转", () => {
    renderHeader();
    const toggle = screen.getByRole("button", { name: "Toggle dark mode" });
    fireEvent.click(toggle); // jsdom 无 matchMedia → 初始按 light，切到 dark
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem("zai.theme")).toBe("dark");
    fireEvent.click(toggle);
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(window.localStorage.getItem("zai.theme")).toBe("light");
  });

  it("§56 部署状态：无 deploy_state 时顶栏不渲染部署小字", async () => {
    await seedBoard(10);
    renderHeader();
    expect(screen.queryByText(/^v\d+\.\d+\.\d+/)).toBeNull();
  });

  it("§56 部署状态：healthy → 「v0.48.4 · deployed 12m ago」，次级色、无警告 class", async () => {
    const lastDeployed = new Date(Date.now() - 12 * 60 * 1000).toISOString();
    await seedBoard(10, {
      status: "deployed",
      version: "0.48.4",
      head: "abcdef0",
      last_deployed: lastDeployed,
      detail: "deployed 1111111 -> abcdef0",
    });
    renderHeader();
    const label = screen.getByText("v0.48.4 · deployed 12m ago");
    expect(label.className).toBe("shell-deploy");
    expect(label.getAttribute("title")).toBe("deployed 1111111 -> abcdef0");
  });

  it("§56 部署状态：rolled_back → 点名状态 + 警告 class；中文文案镜像", async () => {
    const lastDeployed = new Date(Date.now() - 3 * 3600 * 1000).toISOString();
    const state: DeployState = {
      status: "rolled_back",
      version: "0.48.3",
      last_deployed: lastDeployed,
      failed_sha: "deadbeef",
    };
    await seedBoard(10, state);
    renderHeader();
    const label = screen.getByText("v0.48.3 · deployed 3h ago · rolled back");
    expect(label.className).toBe("shell-deploy is-warn");
    cleanup();
    renderHeader("zh");
    expect(screen.getByText("v0.48.3 · 3小时前部署 · 已回滚")).toBeTruthy();
  });

  it("§56 部署状态：ci_pending / ci_failed 是警告态，各有双语文案（B1 CI 闸门）", async () => {
    await seedBoard(10, {
      status: "ci_pending",
      version: "0.48.6",
      detail: "waiting for CI on origin/main abc1234: ci is in_progress",
    });
    renderHeader();
    const pending = screen.getByText("v0.48.6 · waiting for CI on main");
    expect(pending.className).toBe("shell-deploy is-warn");
    expect(pending.getAttribute("title")).toContain("in_progress");
    cleanup();
    resetStoreForTests();
    await seedBoard(10, { status: "ci_failed", version: "0.48.6", failed_sha: "abc1234" });
    renderHeader("zh");
    expect(screen.getByText("v0.48.6 · main 的 CI 红了，未部署").className).toBe("shell-deploy is-warn");
  });

  it("§56 部署状态：install_incomplete / blocked_tcc（v0.48.20）是警告态，各有双语文案", async () => {
    await seedBoard(10, {
      status: "install_incomplete",
      version: "0.48.11",
      running_version: "0.48.8",
      detail: "install_report.json says v0.48.8, checkout is v0.48.11",
    });
    renderHeader();
    const incomplete = screen.getByText("v0.48.11 · install incomplete");
    expect(incomplete.className).toBe("shell-deploy is-warn");
    expect(incomplete.getAttribute("title")).toContain("v0.48.8");
    cleanup();
    resetStoreForTests();
    await seedBoard(10, { status: "blocked_tcc", version: "0.48.11", reason: "volume_access_denied" });
    renderHeader("zh");
    expect(screen.getByText("v0.48.11 · 后台任务读不到外置盘（需授权）").className).toBe("shell-deploy is-warn");
  });

  it("§56 部署状态：healthy 但 last_incident 在案 → 警告色 + 判决进 title（#135 review）", async () => {
    const verdict = "2026-09-02T00:48:54Z rollback_failed: rollback refused (store2 became the registry truth)";
    await seedBoard(10, {
      status: "up_to_date",
      version: "0.48.11",
      last_deployed: "2026-09-02T00:30:00Z",
      detail: "",
      last_incident: verdict,
    });
    renderHeader();
    const el = screen.getByText(/unresolved rollback verdict/);
    expect(el.textContent).toContain("v0.48.11");
    expect(el.className).toBe("shell-deploy is-warn");
    expect(el.getAttribute("title")).toBe(verdict);
    cleanup();
    resetStoreForTests();
    await seedBoard(10, { status: "up_to_date", version: "0.48.12", last_incident: verdict });
    renderHeader("zh");
    expect(screen.getByText("v0.48.12 · 上次回滚判决待处理").className).toBe("shell-deploy is-warn");
  });

  it("§56 部署状态：只有 version、还没成功部署过（无 last_deployed）→ 只显示版本", async () => {
    await seedBoard(10, { status: "up_to_date", version: "0.48.4" });
    renderHeader();
    expect(screen.getByText("v0.48.4")).toBeTruthy();
  });

  it("语言切换：store.language 翻转 + 持久化 zai.lang", () => {
    renderHeader("zh");
    fireEvent.click(screen.getByRole("button", { name: "切换到英文" }));
    expect(getState().language).toBe("en");
    expect(window.localStorage.getItem("zai.lang")).toBe("en");
  });
});

describe("HeaderBar 三档密度（§49 追记 2026-09-04）", () => {
  const withDevice = (generatedAt: string, deployState?: DeployState): Board =>
    ({ ...makeBoard(generatedAt, deployState), device_label: "This Mac" }) as Board;

  async function seedDeviceBoard(ageSeconds: number, deployState?: DeployState) {
    fetchBoardMock.mockResolvedValue(withDevice(new Date(Date.now() - ageSeconds * 1000).toISOString(), deployState));
    await refreshBoard();
  }

  beforeEach(() => {
    resetStoreForTests();
    fetchBoardMock.mockReset();
  });

  afterEach(() => {
    cleanup();
    delete window.webkit;
    resetShellBridgeForTests();
  });

  it("jsdom 没有 ResizeObserver：不给 prop 时 data-density=full，设备标签 / 新鲜度 / 部署小字都在", async () => {
    await seedDeviceBoard(10, { status: "deployed", version: "1.0.7", last_deployed: new Date(Date.now() - 60_000).toISOString() });
    renderHeader();
    expect(screen.getByRole("banner").getAttribute("data-density")).toBe("full");
    expect(screen.getByText("This Mac")).toBeTruthy();
    expect(document.querySelector(".shell-freshness")?.textContent).toBe("Data generated just now");
    expect(screen.getByText("v1.0.7 · deployed 1m ago")).toBeTruthy();
    expect(document.querySelector(".shell-connection")?.getAttribute("title")).toBe("Connecting");
  });

  it("compact：只收设备标签，新鲜度 / 部署小字照旧", async () => {
    await seedDeviceBoard(10, { status: "deployed", version: "1.0.7" });
    renderHeader("en", { density: "compact" });
    expect(screen.getByRole("banner").getAttribute("data-density")).toBe("compact");
    expect(screen.queryByText("This Mac")).toBeNull();
    expect(document.querySelector(".shell-freshness")?.textContent).toBe("Data generated just now");
    expect(screen.getByText("v1.0.7")).toBeTruthy();
  });

  it("tight：新鲜度 / 部署小字不占行，整句折进连接点的 tooltip；陈旧 / 非 healthy 时点带 is-warn", async () => {
    const deployedAt = new Date(Date.now() - 3 * 3600 * 1000).toISOString();
    await seedDeviceBoard(5 * 60, { status: "rolled_back", version: "1.0.6", last_deployed: deployedAt, failed_sha: "deadbeef" });
    renderHeader("en", { density: "tight" });
    expect(screen.getByRole("banner").getAttribute("data-density")).toBe("tight");
    expect(screen.queryByText("This Mac")).toBeNull();
    expect(document.querySelector(".shell-freshness")).toBeNull();
    expect(document.querySelector(".shell-deploy")).toBeNull();
    const connection = document.querySelector(".shell-connection")!;
    expect(connection.getAttribute("title")).toBe(
      "Connecting · Data generated 5 min ago — actd may be down · v1.0.6 · deployed 3h ago · rolled back",
    );
    expect(connection.className).toContain("is-warn");
  });

  it("tight + 新鲜 + healthy：tooltip 带上信息但连接点不报警", async () => {
    await seedDeviceBoard(10, { status: "up_to_date", version: "1.0.7" });
    renderHeader("zh", { density: "tight" });
    const connection = document.querySelector(".shell-connection")!;
    expect(connection.getAttribute("title")).toBe("连接中 · 数据生成于 刚刚 · v1.0.7");
    expect(connection.className).not.toContain("is-warn");
  });

  it("tight + 壳桥：录制 / 字幕开关保留 aria-label，录制的 title 说全「Rec: Off」（文字由 CSS 收起）", async () => {
    const state = shellState();
    window.webkit = { messageHandlers: { zaiShell: { postMessage: vi.fn(async () => state) } } };
    await seedDeviceBoard(10);
    renderHeader("en", { density: "tight" });
    const rec = await screen.findByRole("button", { name: "Recording controls" });
    expect(rec.getAttribute("title")).toBe("Rec: Off");
    expect(rec.textContent).toContain("Off"); // 文案节点仍在（判卷面按节点找），只是 tight 档 CSS 不显示
    expect(screen.getByRole("button", { name: "Live captions" }).getAttribute("title")).toBe("Live captions");
    cleanup();
    renderHeader("en", { density: "full" });
    expect((await screen.findByRole("button", { name: "Recording controls" })).getAttribute("title")).toBe("Recording controls");
  });
});

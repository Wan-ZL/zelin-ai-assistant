// 「一键更新」不许报假成功（CONTRACT §68.6 追记 2026-09-14；issue #309）。
// 生产机的 deploy_state 从 2026-09-05 起是 refused_branch（539 次拒绝），而关于页两次点「一键更新」
// 都说「已触发自动部署——几分钟后这里的版本会变」。本判例钉住诚实的四条路：
//   - about.deploy_state 属于 BLOCKING → 按钮一进页就禁用 + 「更新链路断着：…」+ 怎么修；
//   - 真点下去吃到 409 deploy_refused → 同一句文案，**不开** release 页、**不说**「已触发」；
//   - 旧 409（agent 未加载，details 里没有 reason）→ 原生非 Sparkle 兜底照旧打开 release 页；
//   - kickstart 真发生了：deferred / 中毒的 sha 不许拿到「几分钟后版本会变」的承诺。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchAbout, postUpdateInstall } from "../../api";
import { LanguageContext } from "../../i18n";
import { resetShellBridgeForTests } from "../../shellBridge";
import { resetStoreForTests } from "../../store";
import type { AboutInfo, DeployState } from "../../types";
import { AboutSection, deployGate, installNote, refusalDetails } from "./AboutSection";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    fetchBoard: vi.fn(), fetchHealth: vi.fn(), fetchAbout: vi.fn(), postUpdateCheck: vi.fn(),
    postUpdateInstall: vi.fn(), postUninstallTerminal: vi.fn(), postSetupStep: vi.fn(),
  };
});

const NOW = Date.parse("2026-09-14T12:00:00Z");
const zh = (z: string, _e: string) => z;
const en = (_z: string, e: string) => e;

/** 生产机 2026-09-09 的真形状：release 分支上 92 个数据 commit，auto-deploy 每 10 分钟拒一次 */
const REFUSED: DeployState = {
  status: "refused_branch",
  version: "1.0.23+92",
  detail: "HEAD is on 'release', not main",
  last_run: "2026-09-14T11:55:00Z",
};

const about: AboutInfo = {
  version: "1.0.23+92", home: "/h", repo: "/r",
  update_available: { latest: "1.0.98", url: "https://rel/1.0.98" },
  update_check: { checked_at: "2026-09-14T11:00:00Z", latest: "1.0.98", url: "https://rel/1.0.98" },
  deploy_state: REFUSED,
};

const renderIn = (language: "zh" | "en") =>
  render(<LanguageContext.Provider value={language}><AboutSection /></LanguageContext.Provider>);

const conflict = (details: Record<string, unknown>) =>
  new ApiError(409, { error: { code: "CONFLICT", message: "refused", details } });

beforeEach(() => {
  resetStoreForTests();
  resetShellBridgeForTests();
  vi.mocked(fetchAbout).mockReset();
  vi.mocked(postUpdateInstall).mockReset();
  vi.mocked(fetchAbout).mockResolvedValue(about);
  window.history.replaceState(null, "", "/?page=about");
});

afterEach(() => cleanup());

describe("deployGate / refusalDetails (pure)", () => {
  it("only the three states a kickstart cannot clear gate the button", () => {
    for (const status of ["refused_branch", "refused_dirty", "blocked_tcc"]) {
      expect(deployGate({ status }, zh), status).not.toBeNull();
    }
    for (const status of ["deployed", "up_to_date", "deferred", "ci_pending", "ci_failed", "failed", "", "brand_new"]) {
      expect(deployGate({ status }, zh), status).toBeNull();
    }
    expect(deployGate(null, zh)).toBeNull();
    expect(deployGate(undefined, zh)).toBeNull();
    expect(deployGate({ status: 7 } as unknown as DeployState, zh)).toBeNull();
  });

  it("the line names the状态 and the refusal verbatim, in both languages", () => {
    expect(deployGate(REFUSED, zh)!.line).toBe("更新链路断着：不在 main，部署暂停 — HEAD is on 'release', not main");
    expect(deployGate(REFUSED, en)!.line).toBe("The update path is broken: deploy paused: not on main — HEAD is on 'release', not main");
    expect(deployGate({ status: "refused_dirty" }, zh)!.line).toBe("更新链路断着：工作树有改动，部署暂停");
    expect(deployGate(REFUSED, zh)!.fix).toMatch(/auto-deploy\.sh --force/);
  });

  it("refusalDetails只认 409 + reason=deploy_refused（旧 409 = agent 未加载，另一条路）", () => {
    expect(refusalDetails(conflict({ reason: "deploy_refused", deploy_status: "refused_dirty", deploy_detail: "d" })))
      .toEqual({ status: "refused_dirty", detail: "d" });
    expect(refusalDetails(conflict({}))).toBeNull();
    expect(refusalDetails(conflict({ reason: "other" }))).toBeNull();
    expect(refusalDetails(new ApiError(500, { error: { code: "INTERNAL_ERROR", message: "x", details: { reason: "deploy_refused" } } }))).toBeNull();
    expect(refusalDetails(new Error("boom"))).toBeNull();
    // 坏类型的 details 不许把渲染打崩
    expect(refusalDetails(conflict({ reason: "deploy_refused", deploy_status: 3, deploy_detail: null })))
      .toEqual({ status: "", detail: "" });
  });
});

describe("installNote: only a healthy previous round may promise a new version", () => {
  const receipt = (deploy_status: string, deploy_detail = "") => ({ ok: true, label: "l", action: "kickstart", deploy_status, deploy_detail });

  it("healthy / unknown previous round keeps the native sentence", () => {
    expect(installNote(receipt("up_to_date"), null, NOW, en)).toMatch(/^Auto-deploy triggered/);
    expect(installNote(receipt("deployed"), null, NOW, en)).toMatch(/^Auto-deploy triggered/);
    // 旧 server 的回执没有这两个键 → 照旧那句话
    expect(installNote({ ok: true, label: "l", action: "kickstart" }, null, NOW, en)).toMatch(/^Auto-deploy triggered/);
    expect(installNote(null, null, NOW, en)).toMatch(/^Auto-deploy triggered/);
  });

  it("deferred says the sessions must end first — a kickstart carries no --force", () => {
    const state: DeployState = { status: "deferred", deferred_sessions: "2", deferred_reason: "sessions_running" };
    const note = installNote(receipt("deferred"), state, NOW, en);
    expect(note).not.toMatch(/changes in a few minutes/);
    expect(note).toMatch(/waiting for 2 sessions to finish or be accepted/);
    expect(note).toMatch(/auto-deploy\.sh --force/);
    expect(installNote(receipt("deferred"), state, NOW, zh)).toContain("等待 2 个会话结束或验收");
  });

  it("a poisoned sha says it will not be retried this round", () => {
    const state: DeployState = { status: "ci_failed", failed_sha: "abc1234def5678" };
    const note = installNote(receipt("ci_failed"), state, NOW, en);
    expect(note).not.toMatch(/changes in a few minutes/);
    expect(note).toContain("abc1234");
    expect(note).toMatch(/not retried until main moves/);
    expect(installNote(receipt("rolled_back"), { status: "rolled_back", failed_sha: "abc1234def" }, NOW, zh)).toContain("不会被重试");
  });

  it("other non-healthy rounds keep the promise and name the previous verdict", () => {
    // ci_pending：下一轮真的会重新问一次 CI，承诺仍然成立
    const note = installNote(receipt("ci_pending"), { status: "ci_pending" }, NOW, en);
    expect(note).toMatch(/^Auto-deploy triggered/);
    expect(note).toMatch(/last round: waiting for CI on main/);
    // 中毒家族但没有 failed_sha（投影里那把键缺席）→ 同样只补一句「上一轮」
    expect(installNote(receipt("failed"), { status: "failed" }, NOW, en)).toMatch(/^Auto-deploy triggered/);
  });
});

describe("AboutSection with a refusing auto-deploy", () => {
  it("zh: the button is disabled on arrival and the page says why; no request is fired", async () => {
    renderIn("zh");
    const button = await screen.findByRole("button", { name: "新版本 v1.0.98 可用 — 一键更新" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByText("更新链路断着：不在 main，部署暂停 — HEAD is on 'release', not main")).toBeTruthy();
    expect(screen.getByText(/提前跑一轮也是同样的拒绝/)).toBeTruthy();
    fireEvent.click(button);
    expect(postUpdateInstall).not.toHaveBeenCalled();
  });

  it("a healthy deploy_state (or none at all) leaves the button alone", async () => {
    vi.mocked(fetchAbout).mockResolvedValue({ ...about, deploy_state: { status: "up_to_date", version: "1.0.98" } });
    renderIn("zh");
    const button = await screen.findByRole("button", { name: "新版本 v1.0.98 可用 — 一键更新" }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    expect(screen.queryByText(/更新链路断着/)).toBeNull();
  });

  it("409 deploy_refused: the refusal is printed, the release page never opens, nothing says 已触发", async () => {
    vi.mocked(fetchAbout).mockResolvedValue({ ...about, deploy_state: null });   // 一进页还不知道
    vi.mocked(postUpdateInstall).mockRejectedValue(conflict({
      reason: "deploy_refused", deploy_status: "refused_dirty",
      deploy_detail: "working tree dirty: state/store2.db", fix: "git status",
    }));
    const opened = vi.spyOn(window, "open").mockImplementation(() => null);
    renderIn("zh");
    fireEvent.click(await screen.findByRole("button", { name: "新版本 v1.0.98 可用 — 一键更新" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent)
      .toMatch(/更新链路断着：工作树有改动，部署暂停 — working tree dirty: state\/store2\.db/));
    expect(screen.getByRole("alert").textContent).toMatch(/auto-deploy\.sh --force/);
    expect(screen.getByRole("alert").textContent).not.toMatch(/已触发/);
    expect(opened).not.toHaveBeenCalled();
    // 拒绝的状态刚被 server 现读出来 → 重拉 about，按钮跟着灰掉
    await waitFor(() => expect(fetchAbout).toHaveBeenCalledTimes(2));
    opened.mockRestore();
  });

  it("the old 409 (agent not loaded) still falls back to the release page", async () => {
    vi.mocked(fetchAbout).mockResolvedValue({ ...about, deploy_state: null });
    vi.mocked(postUpdateInstall).mockRejectedValue(conflict({ label: "com.zelin.aiassistant.autodeploy" }));
    const opened = vi.spyOn(window, "open").mockImplementation(() => null);
    renderIn("zh");
    fireEvent.click(await screen.findByRole("button", { name: "新版本 v1.0.98 可用 — 一键更新" }));
    await waitFor(() => expect(opened).toHaveBeenCalledWith("https://rel/1.0.98", "_blank", "noopener"));
    expect(screen.getByRole("alert").textContent).toMatch(/这台机器不走自动部署/);
    opened.mockRestore();
  });

  it("a deferred previous round: the click succeeds but the page does not promise a new version", async () => {
    vi.mocked(fetchAbout).mockResolvedValue({
      ...about,
      deploy_state: { status: "deferred", version: "1.0.97", deferred_sessions: "2", deferred_reason: "sessions_running" },
    });
    vi.mocked(postUpdateInstall).mockResolvedValue({ ok: true, label: "l", action: "kickstart", deploy_status: "deferred", deploy_detail: "deploy of v1.0.98 deferred" });
    renderIn("zh");
    fireEvent.click(await screen.findByRole("button", { name: "新版本 v1.0.98 可用 — 一键更新" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/等待 2 个会话结束或验收/));
    expect(screen.getByRole("alert").textContent).not.toMatch(/几分钟后这里的版本会变/);
  });
});

describe("the version row shows the tag, not the local commit count (§56.1 追记)", () => {
  it("zh: v1.0.23 + 「本地领先 92 个提交，未发版」", async () => {
    renderIn("zh");
    expect(await screen.findByText("v1.0.23")).toBeTruthy();
    expect(screen.queryByText("1.0.23+92")).toBeNull();
    expect(screen.getByText("本地领先 92 个提交，未发版")).toBeTruthy();
  });

  it("en: a released checkout shows the tag alone", async () => {
    vi.mocked(fetchAbout).mockResolvedValue({ ...about, version: "1.0.98", deploy_state: null });
    renderIn("en");
    expect(await screen.findByText("v1.0.98")).toBeTruthy();
    expect(screen.queryByText(/ahead of the tag/)).toBeNull();
  });
});

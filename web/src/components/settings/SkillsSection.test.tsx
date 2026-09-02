// 设置页「Skills」section 行为（CONTRACT §65，D13）：
//   1) 从 GET /api/skills 水合：一行一个 skill，状态徽章逐字镜像 wire state（enabled / disabled / custom…）；
//   2) 开关 = 一次 POST {name, action}，零多余字段；成功以 server 回执替换快照 + toast；
//   3) custom（本地改过的副本）与 foreign 行开关锁定，并显示「自定义 · 落后 N 版」+ 不覆盖的说明；
//   4) server 拒绝（409 CONFLICT）的整句原文以 toast(role=alert) 显示，不吞；
//   5) 未知 state 值原样展示（wire add-only，前端绝不因新值崩渲染）。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchSkills, postSkill } from "../../api";
import { LanguageContext } from "../../i18n";
import { resetStoreForTests } from "../../store";
import type { SkillRow, SkillsSnapshot } from "../../types";
import { SkillsSection } from "./SkillsSection";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchSkills: vi.fn(), postSkill: vi.fn() };
});

function row(over: Partial<SkillRow> = {}): SkillRow {
  return {
    name: "test-code",
    version: "0.2.1",
    upstream: null,
    upstream_version: null,
    default_enabled: true,
    description: "measurement ladder",
    path: "/home/me/.claude/skills/test-code",
    target: "/repo/skills/test-code",
    link: "symlink",
    state: "enabled",
    stale_target: false,
    installed_version: "0.2.1",
    relation: "same",
    distance: 0,
    decision: "enabled",
    project_visible: true,
    toggle: "disable",
    ...over,
  };
}

function snapshot(rows: SkillRow[]): SkillsSnapshot {
  return {
    skills: rows,
    skills_dir: "/home/me/.claude/skills",
    repo_skills_dir: "/repo/skills",
    state_path: "/repo/state/skills.json",
  };
}

const CUSTOM = row({
  name: "write-better",
  version: "1.2.0",
  default_enabled: false,
  link: "directory",
  state: "custom",
  installed_version: "1.0.0",
  relation: "behind",
  distance: 2,
  decision: null,
  project_visible: false,
  toggle: "locked",
  path: "/home/me/.claude/skills/write-better",
});

const OFF = row({
  name: "board-agent",
  version: "1.0.0",
  link: "none",
  state: "disabled",
  installed_version: null,
  decision: "disabled",
  toggle: "enable",
});

function renderSection(language: "en" | "zh" = "en") {
  return render(
    <LanguageContext.Provider value={language}>
      <SkillsSection />
    </LanguageContext.Provider>,
  );
}

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchSkills).mockReset().mockResolvedValue(snapshot([row(), OFF, CUSTOM]));
  vi.mocked(postSkill).mockReset();
});

afterEach(() => {
  cleanup();
});

describe("SkillsSection", () => {
  it("renders one row per skill with the server-judged state badge", async () => {
    renderSection();
    await screen.findByText("test-code");
    expect(screen.getByText("enabled")).toBeTruthy();
    expect(screen.getByText("disabled")).toBeTruthy();
    expect(screen.getByText("custom · 2 behind")).toBeTruthy();
    // the two default_enabled rows are project-visible (tracked .claude/skills symlinks); the custom one is not
    expect(screen.getAllByText("project-visible")).toHaveLength(2);
    expect(screen.getByText(/Link location/)).toBeTruthy();
  });

  it("enables with one POST {name, action} and swaps in the receipt", async () => {
    vi.mocked(postSkill).mockResolvedValue(snapshot([row(), row({ ...OFF, state: "enabled", toggle: "disable", link: "symlink" }), CUSTOM]));
    renderSection();
    const button = await screen.findByRole("button", { name: "Enable board-agent" });
    fireEvent.click(button);
    await waitFor(() => expect(postSkill).toHaveBeenCalledWith("board-agent", "enable"));
    expect(postSkill).toHaveBeenCalledTimes(1);
    await screen.findByRole("button", { name: "Disable board-agent" });
    expect(screen.getByRole("status").textContent).toContain("board-agent enabled");
  });

  it("disables an enabled skill", async () => {
    vi.mocked(postSkill).mockResolvedValue(snapshot([row({ state: "disabled", toggle: "enable", link: "none" }), OFF, CUSTOM]));
    renderSection();
    fireEvent.click(await screen.findByRole("button", { name: "Disable test-code" }));
    await waitFor(() => expect(postSkill).toHaveBeenCalledWith("test-code", "disable"));
    await screen.findByRole("button", { name: "Enable test-code" });
  });

  it("locks the toggle of a custom copy and explains why", async () => {
    renderSection();
    const button = await screen.findByRole("button", { name: "Enable write-better" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/locally edited copy \(v1\.0\.0\)/)).toBeTruthy();
    fireEvent.click(button);
    expect(postSkill).not.toHaveBeenCalled();
  });

  it("shows the server refusal verbatim as an alert toast", async () => {
    vi.mocked(postSkill).mockRejectedValue(new ApiError(409, {
      error: { code: "CONFLICT", message: "/home/me/.claude/skills/board-agent is a local custom copy", details: { code: "SKILL_CUSTOM_KEEP" } },
    }));
    renderSection();
    fireEvent.click(await screen.findByRole("button", { name: "Enable board-agent" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("is a local custom copy");
    // the button is usable again after the refusal
    expect((screen.getByRole("button", { name: "Enable board-agent" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("renders an unknown state value verbatim (wire add-only) and Chinese labels", async () => {
    vi.mocked(fetchSkills).mockResolvedValue(snapshot([row({ state: "quarantined", toggle: "locked" }), CUSTOM]));
    renderSection("zh");
    await screen.findByText("quarantined");
    expect(screen.getByText("自定义 · 落后 2 版")).toBeTruthy();
  });

  it("surfaces a read failure without crashing", async () => {
    vi.mocked(fetchSkills).mockRejectedValue(new ApiError(0, { error: { code: "READ_FAILED", message: "offline" } }));
    renderSection();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("offline");
  });
});

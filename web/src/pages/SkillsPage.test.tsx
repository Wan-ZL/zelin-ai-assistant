// 技能页判例（CONTRACT §67.5 / §54.4 2026-09-15 追记，owner 决策 D78）：?page=skills 渲染的是**同一个**
// SkillsSection（不复制一份），页头是「技能 / Skills」+ 计数（读 store 的同一份 GET /api/skills 快照），
// 「← 返回看板」回看板。设置页那一区只剩一行入口，点它换到本页；`?anchor=skills` 旧深链到达设置页即改道。
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchSkills } from "../api";
import { LanguageContext } from "../i18n";
import { readPage } from "../route";
import { resetStoreForTests } from "../store";
import { SkillsPointerSection } from "../components/settings/SkillsSection";
import { SkillsPage } from "./SkillsPage";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, fetchSkills: vi.fn(), postRevealTarget: vi.fn() };
});

function snapshot() {
  return {
    skills: [
      { name: "board-agent", version: "1.0.0", upstream: null, upstream_version: null, default_enabled: true,
        description: "看板 agent 通道礼仪", path: "/h/.claude/skills/board-agent", target: "/r/skills/board-agent", link: "symlink",
        state: "enabled", stale_target: false, installed_version: "1.0.0", relation: "same", distance: 0, decision: "enabled",
        project_visible: true, toggle: "disable" },
      { name: "write-better", version: "1.0.0", upstream: "singh", upstream_version: "1", default_enabled: false, description: "",
        path: "/h/.claude/skills/write-better", target: "/r/skills/write-better", link: "missing", state: "disabled",
        stale_target: false, installed_version: null, relation: "same", distance: 0, decision: null, project_visible: false,
        toggle: "enable" },
    ],
    skills_dir: "/h/.claude/skills",
    repo_skills_dir: "/r/skills",
    state_path: "/h/state/skills.json",
  };
}

function renderPage(language: "zh" | "en" = "en") {
  window.history.replaceState(null, "", "/?page=skills");
  return render(<LanguageContext.Provider value={language}>{<SkillsPage />}</LanguageContext.Provider>);
}

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchSkills).mockResolvedValue(snapshot() as never);
});

afterEach(cleanup);

describe("SkillsPage — ?page=skills（D78）", () => {
  it("是合法页，页头「技能 / Skills」+ 计数，正文就是 SkillsSection 的行（同一个组件，不复制）", async () => {
    expect(readPage("?page=skills")).toBe("skills");
    renderPage("zh");
    expect(document.querySelector(".skills-page .trash-page-title")?.textContent).toBe("技能");
    // 快照到达：计数 = 行数，行还是 SkillsSection 的（data-skill + 启用 / 停用 / 在 Finder 显示）
    await waitFor(() => expect(document.querySelectorAll(".skills-page [data-skill]").length).toBe(2));
    expect(document.querySelector(".skills-page .trash-page-count")?.textContent).toBe("2");
    expect(screen.getByRole("button", { name: "刷新" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "在 Finder 显示" }).length).toBe(2);
    expect(screen.getByRole("button", { name: "停用 board-agent" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "启用 write-better" })).toBeTruthy();
    expect(document.querySelector(".skills-page .settings-global-path")?.textContent).toContain("/h/.claude/skills");
  });

  it("英文页头与「← 返回看板」深链", async () => {
    renderPage("en");
    expect(document.querySelector(".skills-page .trash-page-title")?.textContent).toBe("Skills");
    const back = document.querySelector<HTMLAnchorElement>(".skills-page .trash-back-link");
    expect(back?.textContent).toBe("← Back to board");
    expect(new URL(back!.href).searchParams.get("page")).toBeNull();
    await waitFor(() => expect(vi.mocked(fetchSkills)).toHaveBeenCalled());
  });
});

describe("设置页的入口行（D78）", () => {
  it("点它就到 ?page=skills（href 是完整深链，左键由路由器的链接委托拦成 pushState）", () => {
    window.history.replaceState(null, "", "/?page=settings");
    render(<LanguageContext.Provider value="zh">{<SkillsPointerSection />}</LanguageContext.Provider>);
    const link = screen.getByRole("link", { name: "Skills 已搬到左侧「技能」页 →" }) as HTMLAnchorElement;
    expect(new URL(link.href).searchParams.get("page")).toBe("skills");
    // 同一行在英文下也在（文案单机制 text(zh, en)，没有第二套 i18n 表）
    cleanup();
    render(<LanguageContext.Provider value="en">{<SkillsPointerSection />}</LanguageContext.Provider>);
    const en = screen.getByRole("link", { name: 'Skills moved to "Skills" in the sidebar →' }) as HTMLAnchorElement;
    expect(new URL(en.href).searchParams.get("page")).toBe("skills");
  });
});

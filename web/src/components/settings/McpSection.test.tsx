// 设置页「MCP servers」区（CONTRACT §68.9 + 追记；原生 SettingsMCP.swift）：
//   1) 刷新后的总计行「共 N 个 server（用户 X · 项目 Y）」——两个作用域求和；两个都空时不出现，改出空态句 + 可选中的
//      `claude mcp add -s user <name> -- <command>`；
//   2) 每个作用域一颗「在 Finder 显示」= postRevealTarget("mcp_user" | "mcp_project")（客户端不传路径），文件不在时禁用；
//      server 拒绝的整句以 role=alert 显示；
//   3) 路径展示读 add-only path_display（$HOME → ~），老 server 缺席退回 path；
//   4) transport 章带 chip-transport-<t>（http 蓝 / sse 紫 / stdio 灰），env 章只给个数且 title 说明绝不显示值；
//   5) 坏 JSON 的作用域句子逐字原生（点「在 Finder 显示」用编辑器检查语法）。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchMcp, postRevealTarget } from "../../api";
import { LanguageContext } from "../../i18n";
import { resetStoreForTests } from "../../store";
import type { McpList, McpScope, McpServer } from "../../types";
import { McpSection, scopeRevealTarget } from "./McpSection";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchMcp: vi.fn(), postRevealTarget: vi.fn() };
});

function server(over: Partial<McpServer> = {}): McpServer {
  return { name: "fs", scope: "user", transport: "stdio", summary: "npx -y @x/fs", env_count: 0, ...over };
}

function scope(over: Partial<McpScope> = {}): McpScope {
  return { scope: "user", path: "/Users/demo/.claude.json", path_display: "~/.claude.json", exists: true, parseable: true, servers: [], ...over };
}

const TWO_SCOPES: McpList = {
  scopes: [
    scope({ servers: [server(), server({ name: "remote", transport: "http", summary: "https://mcp.example.com/v1?●●●", env_count: 2 }), server({ name: "events", transport: "sse", summary: "https://sse.example.com" })] }),
    scope({ scope: "project", path: "/Users/demo/zai/.mcp.json", path_display: "~/zai/.mcp.json", servers: [server({ name: "repo-tool", scope: "project" })] }),
  ],
};

function renderIn(language: "zh" | "en") {
  return render(<LanguageContext.Provider value={language}>{<McpSection />}</LanguageContext.Provider>);
}

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchMcp).mockReset();
  vi.mocked(postRevealTarget).mockReset();
});
afterEach(cleanup);

describe("McpSection", () => {
  it("sums both scopes into the native total line and shows ~ paths", async () => {
    vi.mocked(fetchMcp).mockResolvedValue(TWO_SCOPES);
    renderIn("zh");
    await screen.findByText("共 4 个 server（用户 3 · 项目 1）");
    expect(screen.getByText("~/.claude.json")).toBeTruthy();
    expect(screen.getByText("~/zai/.mcp.json")).toBeTruthy();
    expect(screen.queryByText(/claude mcp add -s user/)).toBeNull();
    cleanup();
    renderIn("en");
    await screen.findByText("4 servers (user 3 · project 1)");
  });

  it("falls back to the absolute path when an older server sends no path_display", async () => {
    const legacy = scope({ servers: [server()] });
    delete legacy.path_display;
    vi.mocked(fetchMcp).mockResolvedValue({ scopes: [legacy] });
    renderIn("en");
    await screen.findByText("/Users/demo/.claude.json");
  });

  it("empty in both scopes → no total line, the native hint and a selectable claude mcp add command", async () => {
    vi.mocked(fetchMcp).mockResolvedValue({ scopes: [scope({ exists: false }), scope({ scope: "project", exists: false, path_display: "~/zai/.mcp.json" })] });
    renderIn("zh");
    await screen.findByText("两个作用域都还没有 MCP server。到终端里加一个，回来点「刷新」就能看到：");
    expect(screen.getByText("claude mcp add -s user <name> -- <command>").tagName).toBe("CODE");
    expect(screen.queryByText(/^共 \d+ 个 server/)).toBeNull();
    expect(screen.getAllByText("文件不存在——这个作用域还没配置过 MCP server。")).toHaveLength(2);
  });

  it("reveals each scope through its own server target and disables the button when the file is missing", async () => {
    vi.mocked(fetchMcp).mockResolvedValue({ scopes: [scope({ servers: [server()] }), scope({ scope: "project", exists: false, servers: [] })] });
    vi.mocked(postRevealTarget).mockResolvedValue({ ok: true });
    renderIn("en");
    const buttons = await screen.findAllByRole("button", { name: "Reveal in Finder" });
    expect(buttons).toHaveLength(2);
    expect((buttons[0] as HTMLButtonElement).disabled).toBe(false);
    expect((buttons[1] as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(buttons[0]);
    await waitFor(() => expect(postRevealTarget).toHaveBeenCalledWith("mcp_user"));
    expect(vi.mocked(postRevealTarget).mock.calls[0]).toHaveLength(1); // 只传词表项：没有路径、没有 mode
    expect(scopeRevealTarget("project")).toBe("mcp_project");
    expect(scopeRevealTarget("local")).toBeNull();
  });

  it("surfaces a rejected reveal verbatim as an alert", async () => {
    vi.mocked(fetchMcp).mockResolvedValue({ scopes: [scope({ servers: [server()] })] });
    vi.mocked(postRevealTarget).mockRejectedValue(new ApiError(404, { error: { code: "NOT_FOUND", message: "no MCP config file in this scope yet" } }));
    renderIn("en");
    fireEvent.click(await screen.findByRole("button", { name: "Reveal in Finder" }));
    expect((await screen.findByRole("alert")).textContent).toContain("no MCP config file in this scope yet");
  });

  it("colours transport chips per transport and explains the env count without ever showing values", async () => {
    vi.mocked(fetchMcp).mockResolvedValue(TWO_SCOPES);
    renderIn("zh");
    await screen.findByText("共 4 个 server（用户 3 · 项目 1）");
    expect(screen.getByText("http").className).toBe("chip chip-transport-http");
    expect(screen.getByText("sse").className).toBe("chip chip-transport-sse");
    expect(screen.getAllByText("stdio")[0].className).toBe("chip chip-transport-stdio");
    const env = screen.getByText("env ×2");
    expect(env.getAttribute("title")).toBe("环境变量只显示数量——值可能含密钥，绝不显示。");
    expect(screen.getByText("https://mcp.example.com/v1?●●●").getAttribute("title")).toBe("https://mcp.example.com/v1?●●●");
  });

  it("a scope with broken JSON points at Reveal in Finder, in the native words", async () => {
    vi.mocked(fetchMcp).mockResolvedValue({ scopes: [scope({ parseable: false })] });
    renderIn("zh");
    await screen.findByText("JSON 解析失败——点「在 Finder 显示」用编辑器检查语法。");
    expect((screen.getByRole("button", { name: "在 Finder 显示" }) as HTMLButtonElement).disabled).toBe(false);
  });
});

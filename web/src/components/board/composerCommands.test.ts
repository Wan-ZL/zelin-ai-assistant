// 捕获历史（20 条 / 去重 / 最新在前）与斜杠命令（/rec 需壳、/lang、/open）——s4 1.8。
// 三动词闸门 / 别名 / 大小写 / 词表超集 / 提示行的判例在 composerSlashGate.test.ts（§41 2026-09-05 追记）。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { navigate } from "../../route";
import { resetStoreForTests, getState } from "../../store";
import { HISTORY_KEY, HISTORY_MAX, LEGACY_HISTORY_KEY, pushHistory, readHistory, runSlashCommand } from "./composerCommands";

vi.mock("../../route", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../route")>();
  return { ...actual, navigate: vi.fn() };
});

const en = (_zh: string, english: string) => english;

beforeEach(() => {
  resetStoreForTests();
  window.localStorage.clear();
  vi.mocked(navigate).mockReset();
  delete (window as Window & { webkit?: unknown }).webkit;
});

afterEach(() => window.localStorage.clear());

describe("capture history", () => {
  it("keeps the latest 20, newest first, deduped", () => {
    for (let i = 0; i < 25; i += 1) pushHistory(`item ${i}`);
    const history = readHistory();
    expect(history).toHaveLength(HISTORY_MAX);
    expect(history[0]).toBe("item 24");
    pushHistory("item 24");
    expect(readHistory().filter((x) => x === "item 24")).toHaveLength(1);
    window.localStorage.setItem(HISTORY_KEY, "not json");
    expect(readHistory()).toEqual([]);
  });

  it("uses the native UserDefaults key name and migrates the pre-1.0 key once", () => {
    expect(HISTORY_KEY).toBe("captureHistory"); // §66.2 setting:prefs:captureHistory 探针按字面量找
    window.localStorage.setItem(LEGACY_HISTORY_KEY, JSON.stringify(["old one", "older"]));
    expect(readHistory()).toEqual(["old one", "older"]);
    expect(window.localStorage.getItem(LEGACY_HISTORY_KEY)).toBeNull();
    expect(JSON.parse(window.localStorage.getItem(HISTORY_KEY) ?? "[]")).toEqual(["old one", "older"]);
    // 新键在场后旧键再出现也不再读（不会把已删的历史复活）
    window.localStorage.setItem(LEGACY_HISTORY_KEY, JSON.stringify(["ghost"]));
    pushHistory("fresh");
    expect(readHistory()).toEqual(["fresh", "old one", "older"]);
  });
});

/** 原生 Composer.swift 的「未识别或参数错误：」分支：error.kind=unrecognized，输入原文随行（保留不清空） */
function unrecognized(r: Awaited<ReturnType<typeof runSlashCommand>>) {
  if (!r.handled || !("error" in r) || r.error.kind !== "unrecognized") return null;
  return { input: r.error.input, usage: r.error.usage };
}

describe("slash commands", () => {
  it("plain text is not a command", async () => {
    expect(await runSlashCommand("hello", en)).toEqual({ handled: false });
  });

  it("/lang switches the UI language; bad arg gives usage", async () => {
    expect((await runSlashCommand("/lang en", en)).handled).toBe(true);
    expect(getState().language).toBe("en");
    const bad = await runSlashCommand("/lang fr", en);
    expect(unrecognized(bad)).toEqual({ input: "/lang fr", usage: expect.stringMatching(/Usage/) });
  });

  it("/open navigates to a known page only", async () => {
    await runSlashCommand("/open diagnostics", en);
    expect(String(vi.mocked(navigate).mock.calls[0][0])).toContain("page=diagnostics");
    const bad = await runSlashCommand("/open nowhere", en);
    expect(unrecognized(bad)).toEqual({ input: "/open nowhere", usage: expect.stringMatching(/Usage/) });
    expect(navigate).toHaveBeenCalledTimes(1);
  });

  it("/rec needs the shell bridge and a valid mode", async () => {
    const noBridge = await runSlashCommand("/rec screen", en);
    expect(noBridge.handled && "note" in noBridge && noBridge.note).toMatch(/only works inside the board app/);
    const postMessage = vi.fn().mockResolvedValue({ recording: { available: true, on: true, mode: "screen" }, captions: {} });
    window.webkit = { messageHandlers: { zaiShell: { postMessage } } };
    await runSlashCommand("/rec screen", en);
    expect(postMessage).toHaveBeenCalledWith({ method: "setRecording", on: true, mode: "screen" });
    await runSlashCommand("/rec off", en);
    expect(postMessage).toHaveBeenLastCalledWith({ method: "setRecording", on: false });
    const bad = await runSlashCommand("/rec video", en);
    expect(unrecognized(bad)).toEqual({ input: "/rec video", usage: expect.stringMatching(/Usage/) });
  });

  it("an unknown verb is not a command — it is a plain capture (native isCommand gate; see composerSlashGate.test.ts)", async () => {
    expect(await runSlashCommand("/wat", en)).toEqual({ handled: false });
  });
});

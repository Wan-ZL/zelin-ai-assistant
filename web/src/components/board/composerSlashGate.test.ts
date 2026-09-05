// 斜杠命令的三动词闸门与词表（CONTRACT §41 2026-09-05 追记；原生 Store.swift SlashCommands）：
//   1) 只有 /rec /open /lang 是命令——「/Users/… 整理一下」「/wat」「/rec整理」都是普通捕获（handled:false），照常铸卡；
//   2) 动词与参数不分大小写（原生 `parts[1].lowercased()`）；参数只看第二个 token；
//   3) /rec 收原生词 audio（→ 壳的 screen_audio）与 web 既有的 screen_audio；
//   4) /open 词表是原生五页的超集：board|deps|ingest|settings|about 在前，web 独有页在后；
//   5) hintLine 与用法句同源——同一份词表。
import { beforeEach, describe, expect, it, vi } from "vitest";
import { navigate } from "../../route";
import { resetStoreForTests } from "../../store";
import { hintLine, runSlashCommand } from "./composerCommands";

vi.mock("../../route", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../route")>();
  return { ...actual, navigate: vi.fn() };
});

const en = (_zh: string, english: string) => english;
const zh = (chinese: string) => chinese;

function usageOf(r: Awaited<ReturnType<typeof runSlashCommand>>): string | null {
  return r.handled && "error" in r && r.error.kind === "unrecognized" ? r.error.usage : null;
}

beforeEach(() => {
  resetStoreForTests();
  window.localStorage.clear();
  vi.mocked(navigate).mockReset();
  delete (window as Window & { webkit?: unknown }).webkit;
});

describe("slash gate — only /rec /open /lang are commands", () => {
  for (const capture of [
    "/Users/zelin/Downloads 整理一下",
    "/wat",
    "/rec整理",   // ICU `\b` 是 Unicode 词界：原生把它当捕获；JS 的 ASCII `\b` 会误判——闸门用 (?=\s|$)
    "/reckon the cost",
    "/opened the door",
    "/language",
    "/ rec screen", // 斜杠后有空格：动词是空串
    "/",
  ]) {
    it(`${JSON.stringify(capture)} is a plain capture (handled:false)`, async () => {
      expect(await runSlashCommand(capture, en)).toEqual({ handled: false });
      expect(navigate).not.toHaveBeenCalled();
    });
  }

  it("a recognised verb with a bad argument is still 未识别 (input kept, usage attached)", async () => {
    const r = await runSlashCommand("/rec video", en);
    expect(r).toEqual({ handled: true, error: { kind: "unrecognized", input: "/rec video", usage: "Usage: /rec off|screen|audio|screen_audio" } });
    expect(usageOf(await runSlashCommand("/open", en))).toMatch(/^Usage: \/open board\|deps\|ingest\|settings\|about\|/);
    expect(usageOf(await runSlashCommand("/lang fr", en))).toBe("Usage: /lang zh|en");
  });

  it("the verb is case-insensitive and only the second token is the argument", async () => {
    await runSlashCommand("/Open   Settings  extra words ignored", en);
    expect(String(vi.mocked(navigate).mock.calls[0][0])).toContain("page=settings");
    expect(usageOf(await runSlashCommand("/LANG", en))).toBe("Usage: /lang zh|en");
  });
});

describe("/rec vocabulary — native audio alias, case-folded, screen_audio kept", () => {
  function installBridge() {
    const postMessage = vi.fn().mockResolvedValue({ recording: { available: true, on: true, mode: "screen_audio" }, captions: {} });
    window.webkit = { messageHandlers: { zaiShell: { postMessage } } };
    return postMessage;
  }

  it("/rec audio maps to the shell's screen_audio (Store.swift modes table)", async () => {
    const postMessage = installBridge();
    const r = await runSlashCommand("/rec audio", en);
    expect(postMessage).toHaveBeenCalledWith({ method: "setRecording", on: true, mode: "screen_audio" });
    expect(r).toEqual({ handled: true, note: "Recording → screen_audio" });
  });

  it("/rec SCREEN and /REC Off are accepted (argument lower-cased like the native)", async () => {
    const postMessage = installBridge();
    await runSlashCommand("/rec SCREEN", en);
    expect(postMessage).toHaveBeenLastCalledWith({ method: "setRecording", on: true, mode: "screen" });
    await runSlashCommand("/REC Off", en);
    expect(postMessage).toHaveBeenLastCalledWith({ method: "setRecording", on: false });
  });

  it("/rec screen_audio (the web's existing word) still works", async () => {
    const postMessage = installBridge();
    await runSlashCommand("/rec screen_audio", en);
    expect(postMessage).toHaveBeenLastCalledWith({ method: "setRecording", on: true, mode: "screen_audio" });
  });

  it("prototype keys never sneak through the alias table", async () => {
    installBridge();
    expect(usageOf(await runSlashCommand("/rec constructor", en))).toMatch(/^Usage: \/rec /);
    expect(usageOf(await runSlashCommand("/rec __proto__", en))).toMatch(/^Usage: \/rec /);
  });
});

describe("/open vocabulary — superset of the native five pages", () => {
  for (const page of ["board", "deps", "ingest", "settings", "about"]) {
    it(`/open ${page} navigates (native MainSection)`, async () => {
      const r = await runSlashCommand(`/open ${page}`, en);
      expect(r).toEqual({ handled: true, note: `Opening ${page}…` });
      const url = String(vi.mocked(navigate).mock.calls[0][0]);
      if (page === "board") expect(url).not.toContain("page=");
      else expect(url).toContain(`page=${page}`);
    });
  }

  it("web-only pages stay accepted (trash / archive / permissions / diagnostics / setup)", async () => {
    for (const page of ["trash", "archive", "permissions", "diagnostics", "setup"]) {
      expect((await runSlashCommand(`/open ${page}`, en)).handled).toBe(true);
    }
    expect(navigate).toHaveBeenCalledTimes(5);
  });

  it("/open ask (retired with D29) is 未识别, not a navigation", async () => {
    expect(usageOf(await runSlashCommand("/open ask", en))).toMatch(/^Usage: \/open /);
    expect(navigate).not.toHaveBeenCalled();
  });
});

describe("hintLine — one line, same vocabulary as the usage strings", () => {
  it("lists the native words first, in native order, then the web-only pages", () => {
    expect(hintLine(zh)).toBe("命令：/rec off|screen|audio|screen_audio · /open board|deps|ingest|settings|about|trash|archive|permissions|diagnostics|setup · /lang zh|en");
    expect(hintLine(en)).toBe("Commands: /rec off|screen|audio|screen_audio · /open board|deps|ingest|settings|about|trash|archive|permissions|diagnostics|setup · /lang zh|en");
  });

  it("every /open word in the hint is accepted, and the usage string is the same list", async () => {
    const line = hintLine(en);
    const pages = /\/open ([a-z_|]+)/.exec(line)?.[1].split("|") ?? [];
    expect(pages.length).toBeGreaterThanOrEqual(10);
    for (const page of pages) expect((await runSlashCommand(`/open ${page}`, en)).handled).toBe(true);
    expect(usageOf(await runSlashCommand("/open nowhere", en))).toBe(`Usage: /open ${pages.join("|")}`);
    const rec = /\/rec ([a-z_|]+)/.exec(line)?.[1] ?? "";
    expect(usageOf(await runSlashCommand("/rec nope", en))).toBe(`Usage: /rec ${rec}`);
  });
});

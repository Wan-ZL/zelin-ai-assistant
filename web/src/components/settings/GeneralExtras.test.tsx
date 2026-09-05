// 通用区页脚「打开 config.yaml」（CONTRACT §68.1 追记；原生 Settings.openConfigYaml）：
//   1) config.yaml 缺席 → 先 POST /api/setup/config-from-example，再 POST /api/reveal {target:"config"}，并说「已从模板创建」；
//   2) config.yaml 在 → 不复制，只 reveal；
//   3) 复制撞 409（别处刚建好）→ 视同已在，照常 reveal，不报错；
//   4) 判定读点击时刻的 GET /api/setup（不是挂载时的快照）；
//   5) 其它复制错误（模板也缺 404）原句以 alert 显示、不 reveal。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchSetup, postRevealTarget, postSetupStep } from "../../api";
import { LanguageContext } from "../../i18n";
import { resetStoreForTests } from "../../store";
import type { SetupSnapshot } from "../../types";
import { GeneralExtras } from "./GeneralExtras";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchSetup: vi.fn(), postSetupStep: vi.fn(), postRevealTarget: vi.fn() };
});

function snapshot(over: Partial<SetupSnapshot> = {}): SetupSnapshot {
  return {
    needed: false, done: true, config_exists: true, config_example_exists: true,
    secrets: { "anthropic-api-key.txt": true }, home: "/tmp/home", protected_location: false, ...over,
  };
}

const calls: string[] = [];

function renderEn() {
  return render(
    <LanguageContext.Provider value="en">
      <GeneralExtras />
    </LanguageContext.Provider>,
  );
}

beforeEach(() => {
  resetStoreForTests();
  calls.length = 0;
  vi.mocked(fetchSetup).mockReset().mockResolvedValue(snapshot());
  vi.mocked(postSetupStep).mockReset().mockImplementation(async (step) => {
    calls.push(`setup:${step}`);
    return { ok: true, setup: snapshot() };
  });
  vi.mocked(postRevealTarget).mockReset().mockImplementation(async (target) => {
    calls.push(`reveal:${target}`);
    return {};
  });
});

afterEach(() => {
  cleanup();
});

describe("GeneralExtras · 打开 config.yaml", () => {
  it("copies the example first when config.yaml is missing, then reveals, and says so", async () => {
    vi.mocked(fetchSetup).mockResolvedValue(snapshot({ config_exists: false }));
    renderEn();
    fireEvent.click(screen.getByRole("button", { name: "Open config.yaml" }));
    await waitFor(() => expect(calls).toEqual(["setup:config-from-example", "reveal:config"]));
    expect((await screen.findByRole("status")).textContent).toBe("Created config.yaml from config.example.yaml");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("only reveals when config.yaml already exists", async () => {
    renderEn();
    fireEvent.click(screen.getByRole("button", { name: "Open config.yaml" }));
    await waitFor(() => expect(calls).toEqual(["reveal:config"]));
    expect(postSetupStep).not.toHaveBeenCalled();
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("treats a 409 from the copy as already-exists and still reveals", async () => {
    vi.mocked(fetchSetup).mockResolvedValue(snapshot({ config_exists: false }));
    vi.mocked(postSetupStep).mockImplementation(async (step) => {
      calls.push(`setup:${step}`);
      throw new ApiError(409, { error: { code: "CONFLICT", message: "config.yaml already exists - not overwriting" } });
    });
    renderEn();
    fireEvent.click(screen.getByRole("button", { name: "Open config.yaml" }));
    await waitFor(() => expect(calls).toEqual(["setup:config-from-example", "reveal:config"]));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();   // nothing was created by us → no 「已创建」 note
  });

  it("asks GET /api/setup at click time rather than trusting the mount-time snapshot", async () => {
    renderEn();
    // 挂载后（app 启动时那份）说 config 在；用户随后删了它——点击时刻的答案才算
    vi.mocked(fetchSetup).mockResolvedValue(snapshot({ config_exists: false }));
    fireEvent.click(screen.getByRole("button", { name: "Open config.yaml" }));
    await waitFor(() => expect(calls).toEqual(["setup:config-from-example", "reveal:config"]));
    expect(fetchSetup).toHaveBeenCalled();
  });

  it("shows any other copy failure verbatim and does not reveal", async () => {
    vi.mocked(fetchSetup).mockResolvedValue(snapshot({ config_exists: false, config_example_exists: false }));
    vi.mocked(postSetupStep).mockImplementation(async (step) => {
      calls.push(`setup:${step}`);
      throw new ApiError(404, { error: { code: "NOT_FOUND", message: "config.example.yaml not found" } });
    });
    renderEn();
    fireEvent.click(screen.getByRole("button", { name: "Open config.yaml" }));
    expect((await screen.findByRole("alert")).textContent).toContain("config.example.yaml not found");
    expect(calls).toEqual(["setup:config-from-example"]);
    expect(postRevealTarget).not.toHaveBeenCalled();
  });
});

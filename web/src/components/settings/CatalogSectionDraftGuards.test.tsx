// 通用设置区的草稿守则（§68.1 追记；原生 Settings.swift 头注「NO deferred save」的 web 对应）：
//   (a) 同 section 别处即时写（Slack 勾选器 / 目录「创建」）刷新目录 → 用户改过的键留草稿、没改的键跟新 effective；
//       同一个键两边都动了（文本框 vs 勾选器写 slack_channels）以 server 为准；自己「保存」成功 → 草稿对齐回执；
//   (b) 有未保存改动才挂 beforeunload（rail / ⌘数字 / /open 都是整页导航，这就是离页守卫），存净即摘；
//   (c) telemetry 联动禁用：level 在 enabled 关时禁用，capture_input 在 enabled 关或 level ≠ detailed 时禁用（只禁不改值）；
//   (d) number / int 草稿非法（负数 / 空 / 非整数）→ 「保存」禁用、该键不进 PUT；trash_retention_days 用原生整句提示；
//       只计用户改过的键——config.yaml 里既有的越界 effective 不锁整区。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchSettingsCatalog, putSettingsSection } from "../../api";
import { LanguageContext } from "../../i18n";
import { resetStoreForTests, saveSettingsSection } from "../../store";
import type { SettingsCatalog, SettingsField, SettingsSection } from "../../types";
import { CatalogSection, invalidKeys, mergeDraft } from "./CatalogSection";
import { isGated, isValidNumberDraft } from "./draftRules";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return { ...actual, fetchSettingsCatalog: vi.fn(), putSettingsSection: vi.fn() };
});

const bilingual = (zh: string, en: string) => ({ zh, en });

function field(over: Partial<SettingsField> & Pick<SettingsField, "key" | "kind">): SettingsField {
  return { label: bilingual(over.key, over.key), help: bilingual("", ""), default: null, choices: null, effective: null, source: "default", ...over };
}

const slackSection = (channels: string[] = [], owner = ""): SettingsSection => ({
  id: "slack", title: bilingual("Slack 接入", "Slack"), help: bilingual("", ""),
  fields: [
    field({ key: "owner_slack_user_id", kind: "string", label: bilingual("你的 Slack 用户 ID", "Your Slack user id"), default: "", effective: owner }),
    field({ key: "slack_channels", kind: "list", label: bilingual("监控频道", "Watched channels"), default: [], effective: channels }),
  ],
});

const telemetrySection = (enabled = true, level = "detailed"): SettingsSection => ({
  id: "telemetry", title: bilingual("产品改进计划", "Product improvement program"), help: bilingual("", ""),
  fields: [
    field({ key: "telemetry.enabled", kind: "bool", label: bilingual("参与产品改进", "Product improvement"), default: true, effective: enabled }),
    field({ key: "telemetry.level", kind: "enum", label: bilingual("行为事件级别", "Behavior-event level"), default: "detailed", choices: ["basic", "detailed"], effective: level }),
    field({ key: "telemetry.capture_input", kind: "bool", label: bilingual("上传我输入的文本", "Upload the text I type"), default: false, effective: false }),
  ],
});

const approvalSection = (): SettingsSection => ({
  id: "approval", title: bilingual("批准", "Approval"), help: bilingual("", ""),
  fields: [
    field({ key: "skip_permissions", kind: "bool", label: bilingual("免确认", "Skip confirmations"), default: true, effective: true }),
    field({ key: "show_cost_above_usd", kind: "number", label: bilingual("显示成本阈值", "Show cost above"), default: 5.0, effective: 5 }),
    field({ key: "trash_retention_days", kind: "int", label: bilingual("回收站保留天数", "Trash retention days"), default: 60, effective: 60 }),
  ],
});

function renderEn(node: React.ReactNode) {
  return render(<LanguageContext.Provider value="en">{node}</LanguageContext.Provider>);
}

function primeCatalog(...sections: SettingsSection[]) {
  const catalog: SettingsCatalog = { sections };
  vi.mocked(fetchSettingsCatalog).mockResolvedValue(catalog);
}

/** 派一个可取消的 beforeunload：返回浏览器会不会拦（defaultPrevented） */
function leaveAttempt(): boolean {
  const event = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(event);
  return event.defaultPrevented;
}

beforeEach(() => {
  resetStoreForTests();
  vi.mocked(fetchSettingsCatalog).mockReset();
  vi.mocked(putSettingsSection).mockReset();
});

afterEach(cleanup);

describe("CatalogSection draft survives a same-section refresh (a)", () => {
  it("keeps the key the user typed in and applies the refreshed value of an untouched key", async () => {
    primeCatalog(slackSection());
    renderEn(<CatalogSection sectionId="slack" />);
    const owner = await screen.findByLabelText("Your Slack user id") as HTMLInputElement;
    const channels = screen.getByLabelText("Watched channels") as HTMLInputElement;
    fireEvent.change(owner, { target: { value: "U123" } });
    expect(screen.getByText("1 unsaved")).toBeTruthy();

    // 勾选器那条路：别处 PUT 同一 section，回执替换目录里的 section → fingerprint 变
    vi.mocked(putSettingsSection).mockResolvedValue(slackSection(["C1"]));
    await saveSettingsSection("slack", { slack_channels: ["C1"] });

    await waitFor(() => expect(channels.value).toBe("C1"));     // 没改过的键跟新 effective
    expect(owner.value).toBe("U123");                            // 改过的键留草稿
    expect(screen.getByText("1 unsaved")).toBeTruthy();          // 仍然只有它未保存（slack_channels 不算脏）
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("realigns the draft to the server receipt after its own Save", async () => {
    primeCatalog(slackSection());
    renderEn(<CatalogSection sectionId="slack" />);
    const owner = await screen.findByLabelText("Your Slack user id") as HTMLInputElement;
    fireEvent.change(owner, { target: { value: "  U123  " } });
    vi.mocked(putSettingsSection).mockResolvedValue(slackSection([], "U123"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(putSettingsSection).toHaveBeenCalledWith("slack", { owner_slack_user_id: "  U123  " }));
    await waitFor(() => expect(owner.value).toBe("U123"));      // 显示值 = effective（原生 commit 成功后的规则）
    expect(screen.queryByText(/unsaved/)).toBeNull();
  });

  it("mergeDraft: first alignment takes everything, later ones keep touched keys whose server value did not move", () => {
    expect(mergeDraft(null, null, { a: 1, b: "x" })).toEqual({ a: 1, b: "x" });
    // b 改过、server 没动 b → 留草稿；a 没改 → 跟新 effective
    expect(mergeDraft({ a: 1, b: "typed" }, { a: 1, b: "x" }, { a: 2, b: "x" })).toEqual({ a: 2, b: "typed" });
    // b 改过、server 也动了 b（别处刚落盘）→ server 赢，草稿让位
    expect(mergeDraft({ a: 1, b: "typed" }, { a: 1, b: "x" }, { a: 2, b: "server" })).toEqual({ a: 2, b: "server" });
    // 刷新后多出的新键（server 升级）直接取 effective
    expect(mergeDraft({ a: 1 }, { a: 1 }, { a: 1, c: true })).toEqual({ a: 1, c: true });
  });

  it("a picker write to the SAME key the user is editing wins: the later Save never reverts the persisted change", async () => {
    // Slack 区同时有 slack_channels 的文本框和勾选器（同一个键）：框里未保存 "C1, C9"，勾选器勾 C2 → server 落 [C1, C2]
    primeCatalog(slackSection(["C1"]));
    renderEn(<CatalogSection sectionId="slack" />);
    const channels = await screen.findByLabelText("Watched channels") as HTMLInputElement;
    fireEvent.change(channels, { target: { value: "C1, C9" } });
    expect(screen.getByText("1 unsaved")).toBeTruthy();

    vi.mocked(putSettingsSection).mockResolvedValue(slackSection(["C1", "C2"]));
    await saveSettingsSection("slack", { slack_channels: ["C1", "C2"] });

    await waitFor(() => expect(channels.value).toBe("C1, C2"));  // server 刚写的值接管这一格（C9 让位，而不是 C2 被下次保存撤回）
    expect(screen.queryByText(/unsaved/)).toBeNull();
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("CatalogSection leave guard (b)", () => {
  it("arms beforeunload only while dirty and disarms after Save", async () => {
    primeCatalog(slackSection());
    renderEn(<CatalogSection sectionId="slack" />);
    const owner = await screen.findByLabelText("Your Slack user id") as HTMLInputElement;
    expect(leaveAttempt()).toBe(false);                          // 干净：不拦

    fireEvent.change(owner, { target: { value: "U123" } });
    expect(leaveAttempt()).toBe(true);                           // 脏：拦

    fireEvent.change(owner, { target: { value: "" } });          // 改回去 = 不脏
    expect(leaveAttempt()).toBe(false);

    fireEvent.change(owner, { target: { value: "U123" } });
    vi.mocked(putSettingsSection).mockResolvedValue(slackSection([], "U123"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByRole("status");
    expect(leaveAttempt()).toBe(false);                          // 存净即摘
  });

  it("disarms on unmount", async () => {
    primeCatalog(slackSection());
    const view = renderEn(<CatalogSection sectionId="slack" />);
    fireEvent.change(await screen.findByLabelText("Your Slack user id"), { target: { value: "U123" } });
    expect(leaveAttempt()).toBe(true);
    view.unmount();
    expect(leaveAttempt()).toBe(false);
  });
});

describe("telemetry cross-field gating (c)", () => {
  it("disables level when the switch is off, and capture_input unless enabled + detailed — from the draft, without changing values", async () => {
    primeCatalog(telemetrySection());
    renderEn(<CatalogSection sectionId="telemetry" />);
    const enabled = await screen.findByRole("switch", { name: "Product improvement" }) as HTMLInputElement;
    const level = screen.getByRole("combobox") as HTMLSelectElement;
    const capture = screen.getByRole("switch", { name: "Upload the text I type" }) as HTMLInputElement;
    expect(level.disabled).toBe(false);
    expect(capture.disabled).toBe(false);

    fireEvent.change(level, { target: { value: "basic" } });     // 原生：level != detailed → capture 禁用
    expect(capture.disabled).toBe(true);
    expect(level.disabled).toBe(false);
    expect(capture.checked).toBe(false);                         // 只禁不改值

    fireEvent.change(level, { target: { value: "detailed" } });
    expect(capture.disabled).toBe(false);

    fireEvent.click(enabled);                                    // 原生：!telemetryEnabled → 两个都禁用
    expect(enabled.checked).toBe(false);
    expect(level.disabled).toBe(true);
    expect(capture.disabled).toBe(true);
    expect(level.closest(".settings-field")?.classList.contains("is-gated")).toBe(true);

    fireEvent.click(enabled);
    expect(level.disabled).toBe(false);
    expect(capture.disabled).toBe(false);
  });

  it("gates from the server snapshot too (enabled=false on load)", async () => {
    primeCatalog(telemetrySection(false, "detailed"));
    renderEn(<CatalogSection sectionId="telemetry" />);
    await screen.findByRole("combobox");
    expect((screen.getByRole("combobox") as HTMLSelectElement).disabled).toBe(true);
    expect((screen.getByRole("switch", { name: "Upload the text I type" }) as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole("switch", { name: "Product improvement" }) as HTMLInputElement).disabled).toBe(false);
  });

  it("isGated only knows the telemetry pair; other keys are never gated", () => {
    expect(isGated("telemetry.level", { "telemetry.enabled": false })).toBe(true);
    expect(isGated("telemetry.level", { "telemetry.enabled": true })).toBe(false);
    expect(isGated("telemetry.capture_input", { "telemetry.enabled": true, "telemetry.level": "basic" })).toBe(true);
    expect(isGated("telemetry.capture_input", { "telemetry.enabled": true, "telemetry.level": "detailed" })).toBe(false);
    expect(isGated("language", { language: "zh" })).toBe(false);
  });
});

describe("invalid number drafts block Save (d)", () => {
  it("a non-integer int, a negative number or an empty box disables Save; the invalid key never reaches the PUT", async () => {
    primeCatalog(approvalSection());
    renderEn(<CatalogSection sectionId="approval" />);
    const days = await screen.findByLabelText("Trash retention days") as HTMLInputElement;
    const cost = screen.getByLabelText("Show cost above") as HTMLInputElement;
    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;

    fireEvent.click(screen.getByRole("switch", { name: "Skip confirmations" }));   // 一个合法改动
    expect(save.disabled).toBe(false);

    fireEvent.change(days, { target: { value: "1.5" } });        // int 不许小数（server 会 400 英文句——现在不出门）
    expect(save.disabled).toBe(true);
    expect(days.getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByText("Enter a whole number of days, e.g. 60 (0 = never auto-purge)")).toBeTruthy();
    expect(screen.getByText("1 unsaved")).toBeTruthy();          // 非法键不算「未保存」，只有开关那一项

    fireEvent.change(days, { target: { value: "" } });           // 空 = 非法（原生：解析失败写 NOTHING）
    expect(save.disabled).toBe(true);

    fireEvent.change(days, { target: { value: "30" } });         // 合法整数 → 放行
    expect(save.disabled).toBe(false);
    expect(screen.queryByText(/whole number of days/)).toBeNull();

    fireEvent.change(cost, { target: { value: "-1" } });         // number 不许负数
    expect(save.disabled).toBe(true);
    expect(screen.getByText("Enter a number ≥ 0, e.g. 5")).toBeTruthy();

    fireEvent.change(cost, { target: { value: "2.5" } });        // number 允许小数
    expect(save.disabled).toBe(false);

    vi.mocked(putSettingsSection).mockResolvedValue(approvalSection());
    fireEvent.click(save);
    await waitFor(() => expect(putSettingsSection).toHaveBeenCalledTimes(1));
    expect(vi.mocked(putSettingsSection).mock.calls[0]).toEqual(["approval", { skip_permissions: false, show_cost_above_usd: 2.5, trash_retention_days: 30 }]);
  });

  it("invalidKeys / isValidNumberDraft: int needs Number.isInteger, number only finite ≥ 0", () => {
    expect(isValidNumberDraft("int", 60)).toBe(true);
    expect(isValidNumberDraft("int", 1.5)).toBe(false);
    expect(isValidNumberDraft("int", -1)).toBe(false);
    expect(isValidNumberDraft("int", null)).toBe(false);
    expect(isValidNumberDraft("number", 2.5)).toBe(true);
    expect(isValidNumberDraft("number", Number.NaN)).toBe(false);
    expect(isValidNumberDraft("number", "5")).toBe(false);
    const s = approvalSection();
    expect(invalidKeys(s, { skip_permissions: true, show_cost_above_usd: 5, trash_retention_days: 60 })).toEqual([]);
    expect(invalidKeys(s, { skip_permissions: true, show_cost_above_usd: -1, trash_retention_days: 1.5 })).toEqual(["show_cost_above_usd", "trash_retention_days"]);
    // 没改过的键（草稿 === effective）永不在列，哪怕 effective 本身越界（config.yaml 来的 -5）
    const stored = { ...s, fields: s.fields.map((f) => (f.key === "trash_retention_days" ? { ...f, effective: -5, source: "config" as const } : f)) };
    expect(invalidKeys(stored, { skip_permissions: true, show_cost_above_usd: 5, trash_retention_days: -5 })).toEqual([]);
    expect(invalidKeys(stored, { skip_permissions: true, show_cost_above_usd: 5, trash_retention_days: -3 })).toEqual(["trash_retention_days"]);
  });

  it("an already-invalid effective from config.yaml does not lock Save for the rest of the section", async () => {
    // server 读文件不查 ≥0（settings_catalog._coerce_number）：`trash: retention_days: -5` 进页就是 -5。
    // 原生每格独立提交——别的开关照常能落；这一格原样显示（红字提示仍在），不脏、不进 PUT
    const stored = approvalSection();
    stored.fields = stored.fields.map((f) => (f.key === "trash_retention_days" ? { ...f, effective: -5, source: "config" as const } : f));
    primeCatalog(stored);
    renderEn(<CatalogSection sectionId="approval" />);
    const days = await screen.findByLabelText("Trash retention days") as HTMLInputElement;
    const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    expect(days.value).toBe("-5");
    expect(save.disabled).toBe(true);                            // 干净：没东西可保存

    fireEvent.click(screen.getByRole("switch", { name: "Skip confirmations" }));
    expect(screen.getByText("1 unsaved")).toBeTruthy();
    expect(save.disabled).toBe(false);                           // 没碰过的越界值不锁整区

    fireEvent.change(days, { target: { value: "-3" } });         // 一碰它就按法条挡
    expect(save.disabled).toBe(true);
    fireEvent.change(days, { target: { value: "-5" } });         // 改回存值 = 没碰过
    expect(save.disabled).toBe(false);

    vi.mocked(putSettingsSection).mockResolvedValue({ ...stored, fields: stored.fields.map((f) => (f.key === "skip_permissions" ? { ...f, effective: false } : f)) });
    fireEvent.click(save);
    await waitFor(() => expect(putSettingsSection).toHaveBeenCalledTimes(1));
    expect(vi.mocked(putSettingsSection).mock.calls[0]).toEqual(["approval", { skip_permissions: false }]);
  });
});

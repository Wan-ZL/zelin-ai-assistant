// pendingSettle.landed：逐动词的「真信号」谓词（原生 PendingSweep.swift cleared(by:) 的判例搬家；§39.3 / §21bis / §37）。
// 每一组都先钉「只有 generated_at 变、卡没动 → 不落地」，再钉该动词自己的落地信号。
import { describe, expect, it } from "vitest";
import type { Board } from "../../types";
import { currentLane, landed, normalizedTitle, recordPending, timeoutNotice, type PendingRecord } from "./pendingSettle";

const text = (zh: string, _en: string) => zh;

function board(over: Partial<Board> = {}, generated_at = "2026-09-05T10:00:00Z"): Board {
  return {
    generated_at, counts: {}, needs_approval: [], running: [], needs_input: [], review: [], completed: [], debt: [], trash: [],
    ...over,
  } as unknown as Board;
}

const proposal = (id: string, over: Record<string, unknown> = {}) =>
  ({ id, title: `${id} title`, summary: `${id} 摘要`, tier: "T1", show_cost: false, processing: false, sources: [], plan: ["step 1"], dod: [], ...over }) as never;
const task = (id: string, over: Record<string, unknown> = {}) => ({ id, name: `${id} name`, state: "working", ...over }) as never;

/** 同一块内容、只换 generated_at（actd 每个 pass 结尾的例行重写） */
const bump = (b: Board): Board => ({ ...b, generated_at: "2026-09-05T10:00:10Z" });

describe("换列动词：id 离开提交时所在的列", () => {
  const before = board({ needs_approval: [proposal("P-1"), proposal("P-2")] });

  it.each(["approve", "reject", "defer", "trash", "done_external"])("%s：generated_at 变但卡仍在提案列 → 不落地；卡离开 → 落地", (action) => {
    const rec = recordPending({ action, id: "P-1", comment: null }, before);
    expect(rec.sourceLane).toBe("needs_approval");
    expect(landed(rec, bump(before))).toBe(false);
    expect(landed(rec, board({ needs_approval: [proposal("P-2")], running: [task("P-1", { state: "queued" })] }))).toBe(true);
  });

  it("restore：卡仍在 trash → 不落地（哪怕新快照）；离开 trash → 落地", () => {
    const b = board({ trash: [{ id: "R-9", title: "t", permanent: false, trashed_at: "2026-09-01T00:00:00Z" }] });
    const rec = recordPending({ action: "restore", id: "R-9" }, b);
    expect(landed(rec, bump(b))).toBe(false);
    expect(landed(rec, board({ debt: [{ id: "R-9", title: "t" } as never] }))).toBe(true);
  });

  it("running 与 needs_input 视为同一列（原生 ids(in: .running)）：卡从 running 挪到 needs_input 不算离开", () => {
    const b = board({ running: [task("R-3")] });
    const rec = recordPending({ action: "abort_execution", id: "R-3", comment: null }, b);
    expect(landed(rec, board({ needs_input: [task("R-3", { state: "blocked" })] }))).toBe(false);
    expect(landed(rec, board({ needs_approval: [proposal("R-3")] }))).toBe(true);
  });

  it("提交时卡不在看板（无 sourceLane）→ 退回「新快照落地」判据", () => {
    const rec = recordPending({ action: "approve", id: "R-404", comment: null }, board());
    expect(rec.sourceLane).toBeNull();
    expect(landed(rec, board())).toBe(false);
    expect(landed(rec, bump(board()))).toBe(true);
  });
});

describe("comment：plan 指纹变 / steers 增 / 离开原列", () => {
  it("提案上的修改意见：generated_at 变、plan 不变 → 不落地；plan 追加了 tag → 落地", () => {
    const b = board({ needs_approval: [proposal("P-1")] });
    const rec = recordPending({ action: "comment", id: "P-1", comment: "改一下" }, b);
    expect(rec.planFingerprint).toBe("step 1");
    expect(landed(rec, bump(b))).toBe(false);
    expect(landed(rec, board({ needs_approval: [proposal("P-1", { plan: ["step 1", "[2026-09-05 修改方向] 改一下"] })] }))).toBe(true);
  });

  it("运行中卡的 comment（steer 中继）：steers[] 变长才落地", () => {
    const b = board({ running: [task("R-3", { steers: [] })] });
    const rec = recordPending({ action: "comment", id: "R-3", comment: "换方向" }, b);
    expect(rec.steerCount).toBe(0);
    expect(landed(rec, bump(b))).toBe(false);
    expect(landed(rec, board({ running: [task("R-3", { steers: [{ text: "换方向", ts: "x", status: "queued", delivered_at: null }] })] }))).toBe(true);
  });

  it("raising 占位上的 comment：fold 把卡改回 card_sent 仍在提案列，plan 变了照样落地；卡整个离开原列也落地", () => {
    const b = board({ needs_approval: [proposal("P-1", { processing: true, plan: [] })] });
    const rec = recordPending({ action: "comment", id: "P-1", comment: "x" }, b);
    expect(landed(rec, board({ needs_approval: [proposal("P-1", { plan: ["[2026-09-05 修改方向] x"] })] }))).toBe(true);
    expect(landed(rec, board({ running: [task("P-1")] }))).toBe(true);
  });
});

describe("set_title：后台 display_title（空白归一）等于新名", () => {
  const b = board({ review: [{ id: "R-7", name: "n", display_title: "旧名", state: "done" } as never] });
  it("generated_at 变但名字没变 → 不落地；display_title 等于新名（含全角空格归一）→ 落地", () => {
    const rec = recordPending({ action: "set_title", id: "R-7", title: "整理　合同  归档" }, b);
    expect(rec.title).toBe("整理 合同 归档");
    expect(landed(rec, bump(b))).toBe(false);
    expect(landed(rec, board({ review: [{ id: "R-7", name: "n", display_title: "整理 合同 归档", state: "done" } as never] }))).toBe(true);
  });
  it("normalizedTitle：所有空白 run 折成单空格（actd `\" \".join(title.split())` 同款）", () => {
    expect(normalizedTitle("  a \n b　c  ")).toBe("a b c");
  });
});

describe("merge_force：每张副卡都从所有列消失", () => {
  const b = board({ needs_approval: [proposal("P-1"), proposal("P-2")], debt: [{ id: "R-5", title: "t" } as never] });
  it("generated_at 变、副卡还在 → 不落地；一张副卡消失另一张还在 → 不落地；全消失 → 落地（主卡留着无妨）", () => {
    const rec = recordPending({ action: "merge_force", ids: ["P-1", "P-2", "R-5"], primary: "P-1" }, b);
    expect(rec.secondaries).toEqual(["P-2", "R-5"]);
    expect(landed(rec, bump(b))).toBe(false);
    expect(landed(rec, board({ needs_approval: [proposal("P-1")], debt: [{ id: "R-5", title: "t" } as never] }))).toBe(false);
    expect(landed(rec, board({ needs_approval: [proposal("P-1")] }))).toBe(true);
  });
  it("ids 去重、primary 不算副卡；没有副卡（退化）→ 看新快照", () => {
    const rec = recordPending({ action: "merge_force", ids: ["P-1", "P-1"], primary: "P-1" }, b);
    expect(rec.secondaries).toEqual([]);
    expect(landed(rec, b)).toBe(false);
    expect(landed(rec, bump(b))).toBe(true);
  });
});

describe("merge_apply / merge_dismiss：建议离开 merge_suggestions", () => {
  const b = board({ merge_suggestions: [{ id: "MS-1", ids: ["P-1", "P-2"], status: "done", verdict: "merge", requested_at: 1 }] as never });
  it.each(["merge_apply", "merge_dismiss"])("%s：建议还在 → 不落地（新快照也不）；建议消失 → 落地", (action) => {
    const rec = recordPending({ action, id: "MS-1", comment: null }, b);
    expect(landed(rec, bump(b))).toBe(false);
    expect(landed(rec, board({ merge_suggestions: [] }))).toBe(true);
    expect(landed(rec, board())).toBe(true); // 旧 server 无键
  });
});

describe("answer_input / pin / split_note", () => {
  it("answer_input：卡离开 needs_input 才落地", () => {
    const b = board({ needs_input: [task("R-3", { state: "blocked" })] });
    const rec = recordPending({ action: "answer_input", id: "R-3", text: "yes" }, b);
    expect(landed(rec, bump(b))).toBe(false);
    expect(landed(rec, board({ running: [task("R-3")] }))).toBe(true);
  });
  it("pin：回收站行 permanent 为真才落地", () => {
    const row = { id: "R-9", title: "t", permanent: false, trashed_at: "2026-09-01T00:00:00Z" };
    const b = board({ trash: [row] });
    const rec = recordPending({ action: "pin", id: "R-9" }, b);
    expect(landed(rec, bump(b))).toBe(false);
    expect(landed(rec, board({ trash: [{ ...row, permanent: true }] }))).toBe(true);
  });
  it("split_note：原 fold 行带上「已拆出」才落地；卡没有 notes_text → 等 180 s", () => {
    const notes = "[quick] 顺手修一下 [@2026-09-01T00:00:00Z]";
    const b = board({ needs_approval: [proposal("P-1", { notes_text: notes })] });
    const rec = recordPending({ action: "split_note", id: "P-1", note_ts: "2026-09-01T00:00:00Z" }, b);
    expect(landed(rec, bump(b))).toBe(false);
    expect(landed(rec, board({ needs_approval: [proposal("P-1", { notes_text: `${notes} [已拆出 R-88]` })] }))).toBe(true);
    expect(landed(rec, board({ needs_approval: [proposal("P-1")] }))).toBe(false);
  });
});

describe("未知动词 / 无 id → 退回新快照判据", () => {
  it("feedback（无 id）：generated_at 变即落地", () => {
    const rec = recordPending({ action: "feedback", text: "x" }, board());
    expect(landed(rec, board())).toBe(false);
    expect(landed(rec, bump(board()))).toBe(true);
  });
});

describe("timeoutNotice：逐动词的诚实文案（Store.swift sweepTimeouts / returnTimeoutText 逐字）", () => {
  const rec = (action: string, over: Partial<PendingRecord> = {}): PendingRecord => ({
    action, id: "P-1", sourceLane: "needs_approval", sentGeneratedAt: null, planFingerprint: null, steerCount: 0,
    title: null, secondaries: [], noteTs: null, label: "整理合同", ...over,
  });
  it("换列动词：卡还在 → 「后台响应超时，卡片已恢复可操作」；卡不在 → 点名 actd 的那句（卡名前 20 字）", () => {
    const here = board({ needs_approval: [proposal("P-1")] });
    expect(timeoutNotice(rec("approve"), here, text)).toBe("后台响应超时，卡片已恢复可操作");
    expect(timeoutNotice(rec("approve"), board(), text)).toBe("「整理合同」已提交但后台超时未确认，请检查 actd 是否在运行");
    expect(timeoutNotice(rec("approve"), null, text)).toBe("「整理合同」已提交但后台超时未确认，请检查 actd 是否在运行");
  });
  it.each([
    ["restore", "恢复超时，卡片仍在回收站，可重试（检查 actd 是否在运行）"],
    ["abort_execution", "退回提案超时，卡片仍在运行中列，可重试（检查 actd 是否在运行）"],
    ["revert_review", "退回待验收超时，卡片仍在「阶段性完成」列，可重试（检查 actd 是否在运行）"],
    ["unarchive", "放回看板超时，卡片仍在「永久性完成」区，可重试（检查 actd 是否在运行）"],
    ["stop_to_review", "去待验收超时，卡片仍在运行中列，可重试（检查 actd 是否在运行）"],
    ["comment", "修改意见超时未合并，卡片未变化，请重试（检查 actd 是否在运行）"],
    ["set_title", "改名超时未确认，卡片名字未变化，请重试（检查 actd 是否在运行）"],
    ["answer_input", "回答超时未确认，卡片仍在「需输入」，请重试（检查 actd 是否在运行）"],
    ["merge_apply", "合并建议操作超时，卡片已恢复可操作（检查 actd 是否在运行）"],
    ["merge_dismiss", "合并建议操作超时，卡片已恢复可操作（检查 actd 是否在运行）"],
    ["merge_force", "强制合并未确认，卡片未变化，请重试（检查 actd 是否在运行）"],
    ["split_note", "「拆成新卡」超时未生效，原备注未变化，请重试（检查 actd 是否在运行）"],
    ["raise", "「整理合同」研究提案超时，请重试"],
  ])("%s → %s", (action, expected) => {
    expect(timeoutNotice(rec(action), board(), text)).toBe(expected);
  });
  it("无 id 的动作 / 记录已丢 → 原通用句", () => {
    expect(timeoutNotice(rec("feedback", { id: null }), board(), text)).toMatch(/^已提交，但 180 秒内看板未回流/);
    expect(timeoutNotice(null, board(), text)).toMatch(/^已提交，但 180 秒内看板未回流/);
  });
  it("label：摘要优先面取 cardHeadline 前 20 字（钦定名压过摘要），名字优先面取 display_title > name", () => {
    const b = board({
      needs_approval: [proposal("P-1", { summary: "一二三四五六七八九十一二三四五六七八九十廿一", display_title: "钦定", user_titled: false })],
      debt: [{ id: "R-2", title: "t", summary: "s", display_title: "我起的名", user_titled: true } as never],
      running: [task("R-3", { display_title: "显示名" })],
    });
    expect(recordPending({ action: "approve", id: "P-1" }, b).label).toBe("一二三四五六七八九十一二三四五六七八九十");
    expect(recordPending({ action: "raise", id: "R-2" }, b).label).toBe("我起的名");
    expect(recordPending({ action: "stop_to_review", id: "R-3" }, b).label).toBe("显示名");
    expect(recordPending({ action: "approve", id: "R-404" }, b).label).toBe("R-404");
  });
});

describe("currentLane", () => {
  it("八个分区都扫；不在 → null；board 为 null → null", () => {
    const b = board({ archived: [{ id: "A-1", title: "t" } as never], completed: [task("C-1")] });
    expect(currentLane(b, "A-1")).toBe("archived");
    expect(currentLane(b, "C-1")).toBe("completed");
    expect(currentLane(b, "nope")).toBeNull();
    expect(currentLane(null, "A-1")).toBeNull();
  });
});

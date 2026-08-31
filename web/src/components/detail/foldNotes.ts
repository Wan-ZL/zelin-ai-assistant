// fold notes 解析（§38）：镜像 act/lib/registry.py parse_fold_notes 与
// mac/Sources/Cards.swift 的行格式（三端 lockstep，改一处必改三处）：
//   "[radar|quick] <text> [@<ts>]" (+ " [已拆出 R-yyy]" 拆出后)
// legacy 无 ts 行返回 ts=null；非 fold 行跳过（由调用方按原文展示）。
const FOLD_LINE_RE = /^\[(radar|quick)\] (.*)$/;
const FOLD_TS_RE = / \[@([^\]\s]+)\]$/;
const FOLD_SPLIT_RE = / \[已拆出 ([^\]\s]+)\]$/;

export interface FoldNote {
  kind: "radar" | "quick";
  text: string;
  ts: string | null;
  splitInto: string | null;
}

export interface ParsedNotes {
  folds: FoldNote[];
  /** 非 fold 行原文（保序），可能是人写备注 / "[拆自 R-xxx]" 面包屑 */
  rest: string[];
}

export function parseFoldNotes(notes: unknown): ParsedNotes {
  const folds: FoldNote[] = [];
  const rest: string[] = [];
  for (const rawLine of String(notes ?? "").split("\n")) {
    const line = rawLine.trim();
    if (!line) continue;
    const m = line.match(FOLD_LINE_RE);
    if (!m) { rest.push(line); continue; }
    let body = m[2];
    let splitInto: string | null = null;
    const sm = body.match(FOLD_SPLIT_RE);
    if (sm) {
      splitInto = sm[1];
      body = body.slice(0, sm.index);
    }
    let ts: string | null = null;
    const tm = body.match(FOLD_TS_RE);
    if (tm) {
      ts = tm[1];
      body = body.slice(0, tm.index);
    }
    folds.push({ kind: m[1] as "radar" | "quick", text: body.trim(), ts, splitInto });
  }
  return { folds, rest };
}

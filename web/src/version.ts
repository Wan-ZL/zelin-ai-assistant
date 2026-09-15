// 版本字符串的用户面读法（CONTRACT §56.1 追记 2026-09-14，issue #309）。
// 真源是 main 上的 git tag `vX.Y.Z`；`act.__version__` 在 HEAD 领先 tag 时解析成
// `X.Y.Z+N`（semver build metadata，N = 领先的 commit 数）。生产机的 checkout 上
// 叠着 ingest 的工作数据 commit，N 因此是「本地有多少条与代码无关的提交」——对用户
// 没有任何意义，却让「关于」页说 `1.0.23+92`、deploy_state 说 `1.0.23+101`，同一台
// 机器两个版本号。自本条起：用户面一律只显示 tag（`v1.0.23`），`+N` 降级成一句
// 辅助说明 / title。比较与判重仍用整串（server 与 update_check 的地盘，这里不碰）。

/** `"1.0.23+92"` → `"1.0.23"`；`"1.0.23"` → `"1.0.23"`；非字符串 / 空 → `""`。 */
export function releaseVersion(value: unknown): string {
  const raw = typeof value === "string" ? value.trim() : "";
  const plus = raw.indexOf("+");
  return plus < 0 ? raw : raw.slice(0, plus);
}

/** `"1.0.23+92"` → `92`（本地领先 tag 的提交数）；没有 `+N` 或 N 不是正整数 → `0`。 */
export function aheadCommits(value: unknown): number {
  const raw = typeof value === "string" ? value.trim() : "";
  const plus = raw.indexOf("+");
  if (plus < 0) return 0;
  const rest = raw.slice(plus + 1);
  if (!/^\d+$/.test(rest)) return 0;
  const n = Number(rest);
  return Number.isSafeInteger(n) && n > 0 ? n : 0;
}

/** `+N` 的那句人话（`N === 0` → null）——关于页放辅助行，顶栏放进 title。 */
export function aheadNote(value: unknown, text: (zh: string, en: string) => string): string | null {
  const n = aheadCommits(value);
  if (!n) return null;
  return text(`本地领先 ${n} 个提交，未发版`, `${n} local commit${n === 1 ? "" : "s"} ahead of the tag, unreleased`);
}

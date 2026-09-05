// 目录草稿的两条纯规则（§68.1 追记；CatalogSection 与 FieldControl 共用，避免互相 import）：
// (1) 数字 / 整数字段的草稿合法性——原生 numberField（Settings.swift）解析失败写 NOTHING，int 字段
//     （commitTrashDays）还要求整数；web 同样：非法草稿挡「保存」并从 PUT 里剔除，server 的英文 400 不再露面。
// (2) 跨字段联动禁用——原生 telemetry 组：level 在 enabled 关时禁用，capture_input 在 enabled 关或 level ≠ detailed
//     时禁用（Settings.swift `.disabled(!telemetryEnabled)` / `.disabled(!telemetryEnabled || telemetryLevel != "detailed")`）；
//     只禁不改值（原生 persistTelemetry 切 level 也不动 capture_input 的存值）。按草稿判，不按 effective——用户看到的就是它。
// (3) 带 `check` 的 string 字段（§68.1 追记；词表 email / session_id）——server 同一条规则的逐字镜像：email = `looks_like_email`
//     （原生 SettingsGmail.validateAddress：恰好一个 @、本地部分非空、域名含 . 且不以 . 起止、无空白）；session_id = `session_id_problem`
//     （原生 SettingsMaintainer.validateSessionID，§68.7 追记：以 - 开头 → reason `leading_hyphen`，其余不合 [A-Za-z0-9][A-Za-z0-9-]*
//     → `charset`；原生同款不设长度帽——字符句只说字符）；不合格的 reason 对上目录 `check.reasons` 就显示那句，否则 `check.message`；
//     空值 = 清键，server 也不查。
import type { SettingsField } from "../../types";

export type Draft = Record<string, unknown>;

/** number / int 草稿是否合法：非负有限数；int 还须是整数 */
export function isValidNumberDraft(kind: string, value: unknown): boolean {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return false;
  return kind !== "int" || Number.isInteger(value);
}

/** server `looks_like_email` 的逐字镜像（原生 validateAddress） */
export function looksLikeEmail(raw: string): boolean {
  const s = raw.trim();
  if (!s || /\s/.test(s)) return false;
  const parts = s.split("@");
  if (parts.length !== 2 || !parts[0]) return false;
  const domain = parts[1];
  return domain.includes(".") && !domain.startsWith(".") && !domain.endsWith(".");
}

/** server `SESSION_ID_RE` 的逐字镜像：首字符字母 / 数字，其余 [A-Za-z0-9-]，不设长度帽（原生同款） */
const SESSION_ID_RE = /^[A-Za-z0-9][A-Za-z0-9-]*$/;

/** server `session_id_problem` 的逐字镜像（原生 validateSessionID）：`leading_hyphen` / `charset` / null（合格） */
export function sessionIdProblem(raw: string): string | null {
  const s = raw.trim();
  if (s.startsWith("-")) return "leading_hyphen";
  return SESSION_ID_RE.test(s) ? null : "charset";
}

/** 草稿值不合 field.check 时的 reason 词（server 同一词表）；无 check / 未知 kind / 空值（= 清键）/ 合格 → null */
export function checkReason(field: SettingsField, value: unknown): string | null {
  const kind = field.check?.kind;
  if (kind !== "email" && kind !== "session_id") return null;
  const draft = typeof value === "string" ? value.trim() : "";
  if (!draft) return null;
  if (kind === "email") return looksLikeEmail(draft) ? null : "shape";
  return sessionIdProblem(draft);
}

/** 草稿值是否过得了 field.check：无 check / 未知 kind / 空值（= 清键）→ true */
export function passesCheck(field: SettingsField, value: unknown): boolean {
  return checkReason(field, value) === null;
}

/** 联动禁用规则：key → 「按这份草稿，该字段是否禁用」 */
const GATES: Record<string, (draft: Draft) => boolean> = {
  "telemetry.level": (draft) => draft["telemetry.enabled"] === false,
  "telemetry.capture_input": (draft) => draft["telemetry.enabled"] === false || draft["telemetry.level"] !== "detailed",
};

/** 该字段是否被同一 section 里的其它草稿值禁用（没有规则的键永不禁用） */
export function isGated(key: string, draft: Draft): boolean {
  const rule = GATES[key];
  return rule ? rule(draft) : false;
}

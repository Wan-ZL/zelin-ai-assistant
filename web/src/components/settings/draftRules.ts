// 目录草稿的纯规则（§68.1 追记；CatalogSection 与 FieldControl 共用，避免互相 import）：
// (1) 数字 / 整数字段的草稿合法性——原生 numberField（Settings.swift）解析失败写 NOTHING，int 字段还要求整数；
//     web 同样：非法草稿挡「保存」并从 PUT 里剔除，server 的英文 400 不再露面。
// (2) 跨字段联动禁用——机制留着（GATES 表），v2.0.0 起没有登记项：唯一的一组（遥测 level / capture_input）随遥测退役。
// (3) 带 `check` 的 string 字段（§68.1 追记；词表 truth = server/settings_catalog.CHECKS，今日只有 clock_time）——server 同一条
//     规则的逐字镜像：clock_time = `settings.CLOCK_TIME_RE`（§28 追记 2026-09-12 安静时段两端，只有 `shape` 一句）；
//     不合格的 reason 对上目录 `check.reasons` 就显示那句，否则 `check.message`；空值 = 清键，server 也不查（归一归 server：
//     `9:30` 这样的合格草稿照发，PUT 回来就是 `09:30`）。email（Gmail 地址）与 session_id（开发会话）随各自的 section 退役。
// (4) 带 `bounds` 的数字字段（§72.4；今日 screenpipe_media_retention_minutes）——server `out_of_bounds` 的逐字镜像：
//     闭区间外的草稿挡「保存」并从 PUT 里剔除（server 越界是 400，不夹取——文件里的数必须就是 cron 真用的数）。
import type { SettingsField } from "../../types";

export type Draft = Record<string, unknown>;

/** number / int 草稿是否合法：非负有限数；int 还须是整数 */
export function isValidNumberDraft(kind: string, value: unknown): boolean {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return false;
  return kind !== "int" || Number.isInteger(value);
}

/** server `out_of_bounds` 的逐字镜像（§72.4）：field.bounds 闭区间外 = true；没登记区间的字段恒 false */
export function outOfBounds(field: SettingsField, value: unknown): boolean {
  const bounds = field.bounds;
  if (!bounds || typeof value !== "number" || !Number.isFinite(value)) return false;
  return value < bounds.min || value > bounds.max;
}

/** 数字草稿最终是否合法：kind 规则 ∧ 在 bounds 内（越界的草稿挡「保存」，与 server 的 400 同一条规则） */
export function isValidNumberValue(field: SettingsField, value: unknown): boolean {
  return isValidNumberDraft(field.kind, value) && !outOfBounds(field, value);
}

/** server `settings.CLOCK_TIME_RE` 的逐字镜像（act/lib/config 同一正则）：24 小时 HH:MM，小时允许一位 */
const CLOCK_TIME_RE = /^([01]?\d|2[0-3]):([0-5]\d)$/;

/** 草稿是否是合格的 HH:MM（两端 trim 后判，与 server `run_check` 同款） */
export function looksLikeClockTime(raw: string): boolean {
  return CLOCK_TIME_RE.test(raw.trim());
}

/** 草稿值不合 field.check 时的 reason 词（server 同一词表）；无 check / 未知 kind / 空值（= 清键）/ 合格 → null */
export function checkReason(field: SettingsField, value: unknown): string | null {
  const kind = field.check?.kind;
  if (kind !== "clock_time") return null;
  const draft = typeof value === "string" ? value.trim() : "";
  if (!draft) return null;
  return looksLikeClockTime(draft) ? null : "shape";
}

/** 草稿值是否过得了 field.check：无 check / 未知 kind / 空值（= 清键）→ true */
export function passesCheck(field: SettingsField, value: unknown): boolean {
  return checkReason(field, value) === null;
}

/** 联动禁用规则：key → 「按这份草稿，该字段是否禁用」（v2.0.0 起没有登记项） */
const GATES: Record<string, (draft: Draft) => boolean> = {};

/** 该字段是否被同一 section 里的其它草稿值禁用（没有规则的键永不禁用） */
export function isGated(key: string, draft: Draft): boolean {
  const rule = GATES[key];
  return rule ? rule(draft) : false;
}

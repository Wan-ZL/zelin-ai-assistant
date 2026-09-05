// 目录草稿的两条纯规则（§68.1 追记；CatalogSection 与 FieldControl 共用，避免互相 import）：
// (1) 数字 / 整数字段的草稿合法性——原生 numberField（Settings.swift）解析失败写 NOTHING，int 字段
//     （commitTrashDays）还要求整数；web 同样：非法草稿挡「保存」并从 PUT 里剔除，server 的英文 400 不再露面。
// (2) 跨字段联动禁用——原生 telemetry 组：level 在 enabled 关时禁用，capture_input 在 enabled 关或 level ≠ detailed
//     时禁用（Settings.swift `.disabled(!telemetryEnabled)` / `.disabled(!telemetryEnabled || telemetryLevel != "detailed")`）；
//     只禁不改值（原生 persistTelemetry 切 level 也不动 capture_input 的存值）。按草稿判，不按 effective——用户看到的就是它。
export type Draft = Record<string, unknown>;

/** number / int 草稿是否合法：非负有限数；int 还须是整数 */
export function isValidNumberDraft(kind: string, value: unknown): boolean {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return false;
  return kind !== "int" || Number.isInteger(value);
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

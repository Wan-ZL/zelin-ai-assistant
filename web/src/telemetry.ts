// web 看板的 telemetry 两个发射点（CONTRACT §15 / §16；owner 决策 D48 / D49）——唯一的 choke point，两条纪律：
//   1) 永不 throw、永不 reject：analytics 与 consent 标记都是旁路，弄坏向导 / 横幅一次都不行（act/lib/analytics.py 同款
//      「never break the pipeline」）；调用方 `void trackEvent(…)` 或 `await` 都安全。
//   2) 只在**显式动作**上调：向导「完成」（wizard_complete + 标记）、披露块复选框保存成功（标记）、一键修复的下场
//      （pipeline_repair_result{ok}）。挂载 / 渲染时一律不调（§15 issue #37 追记的「永不在挂载时写」）。
// 事件名与字段由 server 白名单裁（server/analytics_ingest.py EVENTS）；§16 features.analytics gate 也在 server 侧——
// 这里不读设置、不猜开关，server 回 logged:false 就是 no-op。
import { postAnalytics, postTelemetryConsentShown } from "./api";
import type { WebAnalyticsEvent } from "./types";

/** consent-surface 标记（state/telemetry_consent_shown + _v2，write-once）：向导「完成」/ 披露块保存后调；幂等 */
export function markTelemetryConsentShown(): Promise<void> {
  return postTelemetryConsentShown().then(() => undefined, () => undefined);
}

/** 极简 analytics 事件（wizard_complete / pipeline_repair_result{ok}）；server 白名单外的名字根本传不进来（类型即词表） */
export function trackEvent(event: WebAnalyticsEvent, fields?: Record<string, boolean>): Promise<void> {
  return postAnalytics(event, fields).then(() => undefined, () => undefined);
}

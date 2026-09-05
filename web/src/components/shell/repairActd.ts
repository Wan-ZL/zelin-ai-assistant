// 一键修复的状态机（原生 Doctor.swift PipelineRepair 的 web 版，CONTRACT §68.8 / §47.4）：
//   POST /api/repair/actd → 每 1 s 拉一次 GET /api/health、最多 15 轮（原生 15×1 s 轮询 dashboard 新鲜度）
//   → 恢复：success 6 s（原生「让横幅庆祝一下再复位」）→ idle + refreshHealth（store 刷新，横幅随 verdict 退场）
//   → 15 轮都没恢复：failure（原生整句「后台服务已重启，但数据还没更新——…」）；POST 本身被拒：failure（server 原文）。
// 轮询直接调 fetchHealth、不写 store——横幅要留在屏上把「已恢复 ✓」说完；store 只在庆祝结束后刷一次。
// 宿主：PipelineBanner.RepairButton（横幅）与 FinaleStep（向导「后台服务」行）。卸载时清定时器、丢弃在飞的结果。
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchHealth, postRepairActd } from "../../api";
import { useI18n } from "../../i18n";
import { refreshHealth } from "../../store";
import type { HealthSnapshot } from "../../types";
import { errorMessage } from "../settings/useToast";

/** 原生 PipelineRepair：15 × 1 s 轮询；成功态停留 6 s */
export const REPAIR_POLL_MS = 1000;
export const REPAIR_POLL_ROUNDS = 15;
export const REPAIR_SUCCESS_MS = 6000;

export type RepairPhase =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "success" }
  /** cause = post：server 拒绝了 kickstart（detail = server 原文）；timeout：重启了但 15 s 内 health 没转好 */
  | { kind: "failure"; cause: "post" | "timeout"; detail: string };

/** 「恢复」= /api/health 的 verdict 不再是横幅要说话的三态：ok（心跳新鲜）或 unknown（无心跳文件但看板新鲜 = 数据在更新）。 */
export function isRecovered(health: HealthSnapshot): boolean {
  return health.verdict === "ok" || health.verdict === "unknown";
}

export interface RepairActd {
  phase: RepairPhase;
  /** 幂等：running 期间再点不重复发 POST */
  run: () => void;
}

export function useRepairActd(): RepairActd {
  const { text } = useI18n();
  const [phase, setPhase] = useState<RepairPhase>({ kind: "idle" });
  const alive = useRef(true);
  const busy = useRef(false);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      if (timer.current != null) window.clearTimeout(timer.current);
    };
  }, []);

  // 单飞的 sleep：同一时刻只有一个定时器在等，卸载时清掉它即可（promise 悬着不再有人读）
  const sleep = (ms: number) => new Promise<void>((resolve) => {
    timer.current = window.setTimeout(() => { timer.current = null; resolve(); }, ms);
  });

  const run = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    setPhase({ kind: "running" });
    try {
      await postRepairActd();
    } catch (err) {
      busy.current = false;
      if (alive.current) setPhase({ kind: "failure", cause: "post", detail: errorMessage(err) });
      return;
    }
    let recovered = false;
    for (let round = 0; round < REPAIR_POLL_ROUNDS; round += 1) {
      await sleep(REPAIR_POLL_MS);
      if (!alive.current) return;
      try {
        if (isRecovered(await fetchHealth())) { recovered = true; break; }
      } catch {
        /* server 正在跟着 actd 一起喘（或短暂不可达）——这一轮算没恢复，下一轮再问 */
      }
      if (!alive.current) return;
    }
    busy.current = false;
    if (!alive.current) return;
    if (!recovered) {
      setPhase({
        kind: "failure",
        cause: "timeout",
        detail: text("后台服务已重启，但数据还没更新——点「让 AI 修」深挖，或查看日志", "Service restarted but data still isn't updating — try \"Fix with AI\" or view the log"),
      });
      return;
    }
    setPhase({ kind: "success" });
    await sleep(REPAIR_SUCCESS_MS);
    if (!alive.current) return;
    setPhase({ kind: "idle" });
    void refreshHealth();
  }, [text]);

  return { phase, run: () => void run() };
}

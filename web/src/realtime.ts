// SSE 消费 + 120ms debounce 合并 refetch。
// 模式 fork 自 dashi（Apache-2.0，NOTICE 登记）：App.tsx L582-705 的 LocalRealtimeSync
// （scheduleRefresh 120ms 合并、onopen 全量刷新、onerror 置 reconnecting）
// + revisionPolling.mjs 的注入式可测结构（timer/EventSource 工厂全部可注入，fake timers 直测）。
// 事件契约（server/watcher.py，§49 / §63）：仅 "recaps.updated" {updated_at}（state/recap/ 的 mtime 变了）；
// v2.0.0 起 board.updated 随 dashboard.json 退役。无重连契约——EventSource 自动重连，每次 onopen 一律全量
// refetch 补齐断线期间丢的事件（dashi 实证的务实分层）。

export const RECAPS_UPDATED_EVENT = "recaps.updated";
const DEFAULT_DEBOUNCE_MS = 120;

export interface RealtimeOptions {
  /** debounce 合并后的实际拉取动作（通常 = store.refreshRecaps） */
  onRefetch: () => void;
  onConnectionChange?: (state: "connecting" | "live" | "reconnecting") => void;
  url?: string;
  debounceMs?: number;
  // 注入缝：测试用 fake timers / fake EventSource，生产走默认值
  createEventSource?: (url: string) => EventSource;
  setTimeout?: typeof globalThis.setTimeout;
  clearTimeout?: typeof globalThis.clearTimeout;
}

export interface RealtimeHandle {
  start(): void;
  stop(): void;
}

export function createRecapsRealtime({
  onRefetch,
  onConnectionChange = () => {},
  url = "/api/events",
  debounceMs = DEFAULT_DEBOUNCE_MS,
  createEventSource = (target) => new EventSource(target),
  setTimeout: scheduleTimeout = globalThis.setTimeout.bind(globalThis),
  clearTimeout: cancelTimeout = globalThis.clearTimeout.bind(globalThis),
}: RealtimeOptions): RealtimeHandle {
  let source: EventSource | undefined;
  let running = false;
  let refetchTimer: ReturnType<typeof globalThis.setTimeout> | undefined;

  // 120ms 窗口内的事件风暴（act.recap 一轮连写好几个文件）合并成一次 refetch
  const scheduleRefetch = () => {
    if (refetchTimer !== undefined) cancelTimeout(refetchTimer);
    refetchTimer = scheduleTimeout(() => {
      refetchTimer = undefined;
      if (running) onRefetch();
    }, debounceMs);
  };

  const handleRecapsUpdated = () => {
    // payload 只有 {updated_at}，不携带增量——一律走全量 refetch
    scheduleRefetch();
  };

  return {
    start() {
      if (running) return;
      running = true;
      onConnectionChange("connecting");
      source = createEventSource(url);
      source.addEventListener(RECAPS_UPDATED_EVENT, handleRecapsUpdated);
      source.onopen = () => {
        onConnectionChange("live");
        scheduleRefetch(); // 断线期间可能丢事件：每次连上都全量补一次
      };
      source.onerror = () => onConnectionChange("reconnecting");
    },
    stop() {
      if (!running) return;
      running = false;
      if (refetchTimer !== undefined) cancelTimeout(refetchTimer);
      refetchTimer = undefined;
      source?.removeEventListener(RECAPS_UPDATED_EVENT, handleRecapsUpdated);
      source?.close();
      source = undefined;
    },
  };
}

// 脚手架冒烟测试：realtime 的 debounce/合并/生命周期（经注入缝，零真实网络零真实计时器）。
// A11 在此基础上扩展 store/api/组件行为测试。
import { describe, expect, it, vi } from "vitest";
import { BOARD_UPDATED_EVENT, createBoardRealtime } from "./realtime";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  closed = false;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private listeners = new Map<string, Set<() => void>>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, listener: () => void) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(listener);
  }
  removeEventListener(type: string, listener: () => void) {
    this.listeners.get(type)?.delete(listener);
  }
  close() {
    this.closed = true;
  }
  emit(type: string) {
    this.listeners.get(type)?.forEach((listener) => listener());
  }
}

function setup() {
  vi.useFakeTimers();
  FakeEventSource.instances = [];
  const onRefetch = vi.fn();
  const connections: string[] = [];
  const handle = createBoardRealtime({
    onRefetch,
    onConnectionChange: (state) => connections.push(state),
    createEventSource: (url) => new FakeEventSource(url) as unknown as EventSource,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
  });
  return { handle, onRefetch, connections };
}

describe("createBoardRealtime", () => {
  it("merges a burst of board.updated events into one refetch after 120ms", () => {
    const { handle, onRefetch } = setup();
    handle.start();
    const source = FakeEventSource.instances[0];

    source.emit(BOARD_UPDATED_EVENT);
    vi.advanceTimersByTime(60);
    source.emit(BOARD_UPDATED_EVENT); // 60ms 后又来一发：窗口重置，仍只应 refetch 一次
    vi.advanceTimersByTime(119);
    expect(onRefetch).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(onRefetch).toHaveBeenCalledTimes(1);
    handle.stop();
    vi.useRealTimers();
  });

  it("refetches on every open (reconnect = full refetch) and reports connection states", () => {
    const { handle, onRefetch, connections } = setup();
    handle.start();
    const source = FakeEventSource.instances[0];

    source.onopen?.();
    vi.advanceTimersByTime(120);
    expect(onRefetch).toHaveBeenCalledTimes(1);

    source.onerror?.(); // 断线：EventSource 自动重连，onopen 再触发全量补拉
    source.onopen?.();
    vi.advanceTimersByTime(120);
    expect(onRefetch).toHaveBeenCalledTimes(2);
    expect(connections).toEqual(["connecting", "live", "reconnecting", "live"]);
    handle.stop();
    vi.useRealTimers();
  });

  it("stop() cancels pending refetch and closes the source", () => {
    const { handle, onRefetch } = setup();
    handle.start();
    const source = FakeEventSource.instances[0];

    source.emit(BOARD_UPDATED_EVENT);
    handle.stop();
    vi.advanceTimersByTime(1000);
    expect(onRefetch).not.toHaveBeenCalled();
    expect(source.closed).toBe(true);
    vi.useRealTimers();
  });
});

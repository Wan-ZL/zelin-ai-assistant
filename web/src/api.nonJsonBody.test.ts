// api.request 对「2xx 却不是 JSON」的响应体拒收（CONTRACT §49 追记 `store-resilience-drawer`）：此前 `response.json()` 抛错
// 时 `body = {}` 且 response.ok → resolve `{}`，空对象一路进 store 让看板渲染崩（`board.needs_approval` 取不到）。
// 自此合成 ApiError{status: 真 2xx, code: READ_FAILED, details.failure: "invalid-json"}——调用方留旧快照
//（原生 Store.swift:320-324 decode 失败分支）。非 2xx 的非 JSON 体仍是 REQUEST_FAILED + 通用文案（既有判例不变）。
// fetch 全程 stub，零真实网络。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, fetchBoard, postAction, setApiText } from "./api";

const fetchMock = vi.fn();

function expectApiError(promise: Promise<unknown>): Promise<ApiError> {
  return promise.then(
    () => {
      throw new Error("expected the request to reject");
    },
    (error) => {
      expect(error).toBeInstanceOf(ApiError);
      return error as ApiError;
    },
  );
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  setApiText((_chinese, english) => english);
});

describe("api.request · non-JSON 2xx body", () => {
  it("a 200 with a half-written body rejects with READ_FAILED carrying the real status (not `{}`)", async () => {
    fetchMock.mockResolvedValue(new Response('{"generated_at": "2026-09-05T', { status: 200 }));
    const error = await expectApiError(fetchBoard());
    expect(error.status).toBe(200);
    expect(error.code).toBe("READ_FAILED");
    expect(error.details).toEqual({ method: "GET", failure: "invalid-json" });
    expect(error.message).toBe("The server response is not valid JSON (200)");
    expect(fetchMock).toHaveBeenCalledTimes(1); // 不是网络失败：不重试
  });

  it("an empty 200 body rejects the same way", async () => {
    fetchMock.mockResolvedValue(new Response("", { status: 200 }));
    const error = await expectApiError(fetchBoard());
    expect(error.status).toBe(200);
    expect(error.code).toBe("READ_FAILED");
  });

  it("an HTML page served with 200 (proxy / wrong port) is rejected too", async () => {
    fetchMock.mockResolvedValue(new Response("<!doctype html><title>x</title>", { status: 200, headers: { "Content-Type": "text/html" } }));
    const error = await expectApiError(fetchBoard());
    expect(error.code).toBe("READ_FAILED");
    expect(error.status).toBe(200);
  });

  it("a write with a non-JSON 2xx body rejects as well (never resolves a fake `{}` receipt); still no retry", async () => {
    fetchMock.mockResolvedValue(new Response("ok", { status: 200 }));
    const error = await expectApiError(postAction({ id: "R-101", action: "approve" }));
    expect(error.code).toBe("READ_FAILED");
    expect(error.details).toEqual({ method: "POST", failure: "invalid-json" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("localizes the copy through the injected text function", async () => {
    setApiText((chinese) => chinese);
    fetchMock.mockResolvedValue(new Response("not json {", { status: 200 }));
    const error = await expectApiError(fetchBoard());
    expect(error.message).toBe("服务端响应不是合法 JSON（200）");
  });

  it("a valid JSON 200 still resolves the parsed body", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ generated_at: "x" }), { status: 200 }));
    await expect(fetchBoard()).resolves.toEqual({ generated_at: "x" });
  });

  it("a non-JSON 5xx keeps the generic REQUEST_FAILED envelope (unchanged taxonomy)", async () => {
    fetchMock.mockResolvedValue(new Response("boom", { status: 502 }));
    const error = await expectApiError(fetchBoard());
    expect(error.status).toBe(502);
    expect(error.code).toBe("REQUEST_FAILED");
    expect(error.message).toBe("Request failed (502)");
  });

  it("AbortError raised while reading the body still passes through untouched", async () => {
    const abort = new Error("aborted");
    abort.name = "AbortError";
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: () => Promise.reject(abort) } as unknown as Response);
    await expect(fetchBoard()).rejects.toBe(abort);
  });
});

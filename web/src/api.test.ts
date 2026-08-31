// api 错误分类学测试（fork 自 dashi 的 taxonomy，行为逐条钉死）：
// server envelope → ApiError{status,code,details}；GET 幂等重试 2 次退避后合成 READ_FAILED；
// 写请求绝不重试、合成 SERVICE_UNAVAILABLE；AbortError 原样穿透；payload 原样直发（零容忍不加字段）。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  deliverableUrl,
  fetchBoard,
  postAction,
  resolveApiUrl,
  setApiText,
} from "./api";

const fetchMock = vi.fn();

/** 立即挂上 rejection 处理（fake timers 下避免 unhandled rejection），并断言错误类型 */
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

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  setApiText((_chinese, english) => english); // 还原模块级注入的文案函数
});

describe("server error envelope", () => {
  it("wraps {error:{code,message,details}} into ApiError with status/code/details", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(400, {
        error: { code: "UNKNOWN_FIELD", message: "unknown field: foo", details: { field: "foo" } },
      }),
    );
    const error = await expectApiError(postAction({ id: "R-101", action: "approve" }));
    expect(error.status).toBe(400);
    expect(error.code).toBe("UNKNOWN_FIELD");
    expect(error.message).toBe("unknown field: foo");
    expect(error.details).toEqual({ field: "foo" });
    expect(fetchMock).toHaveBeenCalledTimes(1); // 4xx 是终局，不重试
  });

  it("falls back to REQUEST_FAILED + generic message when the error body is not JSON", async () => {
    fetchMock.mockResolvedValue(new Response("boom", { status: 500 }));
    const error = await expectApiError(fetchBoard());
    expect(error.status).toBe(500);
    expect(error.code).toBe("REQUEST_FAILED");
    expect(error.message).toBe("Request failed (500)");
  });
});

describe("network failure taxonomy", () => {
  it("GET retries twice with backoff, then synthesizes READ_FAILED (status 0)", async () => {
    vi.useFakeTimers();
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const settled = expectApiError(fetchBoard());
    await vi.runAllTimersAsync();
    const error = await settled;
    expect(fetchMock).toHaveBeenCalledTimes(3); // 首发 + 2 次重试
    expect(error.status).toBe(0);
    expect(error.code).toBe("READ_FAILED");
    expect(error.details).toEqual({ method: "GET", failure: "browser-network" });
  });

  it("GET recovers silently when a retry succeeds", async () => {
    vi.useFakeTimers();
    fetchMock
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(jsonResponse(200, { generated_at: "x" }));
    const settled = fetchBoard();
    await vi.runAllTimersAsync();
    await expect(settled).resolves.toEqual({ generated_at: "x" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("POST never retries and synthesizes SERVICE_UNAVAILABLE", async () => {
    fetchMock.mockRejectedValue(new Error("connection refused"));
    const error = await expectApiError(postAction({ id: "R-101", action: "approve" }));
    expect(fetchMock).toHaveBeenCalledTimes(1); // 写动作绝不自动重发
    expect(error.status).toBe(0);
    expect(error.code).toBe("SERVICE_UNAVAILABLE");
    expect(error.details).toEqual({ method: "POST", failure: "network" });
  });

  it("passes AbortError through untouched (no wrap, no retry)", async () => {
    const abort = new Error("aborted");
    abort.name = "AbortError";
    fetchMock.mockRejectedValue(abort);
    await expect(fetchBoard()).rejects.toBe(abort);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("localizes synthesized error copy through the injected text function", async () => {
    setApiText((chinese) => chinese);
    fetchMock.mockRejectedValue(new Error("connection refused"));
    const error = await expectApiError(postAction({ id: "R-101", action: "approve" }));
    expect(error.message).toBe("暂时连不上本地服务，请稍后重试。");
  });
});

describe("request shape", () => {
  it("postAction sends the payload verbatim as JSON — no fields added or dropped", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, { ok: true }));
    await postAction({ id: "R-101", action: "approve", comment: null });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(resolveApiUrl("/api/actions"));
    expect(init.method).toBe("POST");
    expect((init.headers as Headers).get("Content-Type")).toBe("application/json");
    // server 零容忍 UNKNOWN_FIELD：客户端不得偷偷注入任何字段
    expect(JSON.parse(init.body as string)).toEqual({ id: "R-101", action: "approve", comment: null });
  });

  it("deliverableUrl percent-encodes both segments (traversal text stays inert)", () => {
    expect(deliverableUrl("R-101", "report v1.html")).toBe(
      resolveApiUrl("/files/deliverables/R-101/report%20v1.html"),
    );
    // ../ 只会被编码成普通文本段，不产生上跳路径
    expect(deliverableUrl("R-101", "../secret")).toBe(
      resolveApiUrl("/files/deliverables/R-101/..%2Fsecret"),
    );
  });
});

// web telemetry 发射点（CONTRACT §15 / §16；D48 / D49）：
//   · postTelemetryConsentShown → POST /api/telemetry/consent-shown {}；postAnalytics → POST /api/analytics {event[, fields]}
//     （无字段时不带 fields 键——server 零容忍，形状要干净）；两者都是写请求：带 token 头、keepalive（整页导航时不被打断）；
//   · telemetry.ts 的两个包装永不 reject：server 400 / 401 / 断网 都吞成 undefined（analytics 不许弄坏向导 / 横幅）。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { postAnalytics, postTelemetryConsentShown } from "./api";
import { markTelemetryConsentShown, trackEvent } from "./telemetry";

const fetchMock = vi.fn();

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  (window as Window & { __ZAI_TOKEN__?: string }).__ZAI_TOKEN__ = "tok-123";
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete (window as Window & { __ZAI_TOKEN__?: string }).__ZAI_TOKEN__;
});

describe("api: consent-shown + analytics wire shape", () => {
  it("POST /api/telemetry/consent-shown with an empty object, token header and keepalive", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, { ok: true, written: true, shown_at: "2026-09-06T00:00:00Z" }));
    const receipt = await postTelemetryConsentShown();
    expect(receipt.written).toBe(true);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url.endsWith("/api/telemetry/consent-shown")).toBe(true);
    expect(init.method).toBe("POST");
    expect(init.body).toBe("{}");
    expect(init.keepalive).toBe(true);
    expect(new Headers(init.headers).get("X-Zai-Token")).toBe("tok-123");
  });

  it("POST /api/analytics: {event} alone when there are no fields, {event, fields} otherwise", async () => {
    fetchMock.mockImplementation(async () => jsonResponse(200, { ok: true, event: "wizard_complete", logged: true }));
    await postAnalytics("wizard_complete");
    expect(JSON.parse(String((fetchMock.mock.calls[0] as [string, RequestInit])[1].body))).toEqual({ event: "wizard_complete" });
    await postAnalytics("pipeline_repair_result", { ok: false });
    const init = (fetchMock.mock.calls[1] as [string, RequestInit])[1];
    expect(JSON.parse(String(init.body))).toEqual({ event: "pipeline_repair_result", fields: { ok: false } });
    expect(init.keepalive).toBe(true);
    await postAnalytics("wizard_complete", {});
    expect(JSON.parse(String((fetchMock.mock.calls[2] as [string, RequestInit])[1].body))).toEqual({ event: "wizard_complete" });
  });
});

describe("telemetry.ts never rejects", () => {
  it("swallows a server rejection (401 / 400) and a network failure", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(401, { error: { code: "UNAUTHORIZED", message: "missing or bad token", details: {} } }));
    await expect(markTelemetryConsentShown()).resolves.toBeUndefined();
    fetchMock.mockResolvedValueOnce(jsonResponse(400, { error: { code: "INVALID_FIELD", message: "event is not in the server whitelist", details: {} } }));
    await expect(trackEvent("wizard_complete")).resolves.toBeUndefined();
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(trackEvent("pipeline_repair_result", { ok: true })).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledTimes(3); // 写请求绝不重试
  });

  it("resolves after a 200 and forwards the event", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, { ok: true, event: "pipeline_repair_result", logged: false }));
    await expect(trackEvent("pipeline_repair_result", { ok: true })).resolves.toBeUndefined();
    expect(JSON.parse(String((fetchMock.mock.calls[0] as [string, RequestInit])[1].body))).toEqual({ event: "pipeline_repair_result", fields: { ok: true } });
  });
});

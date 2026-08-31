// 交付物 viewer 行为测试：iframe 沙箱红线、reveal 动作、final_draft 双形态。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DeliverableViewer } from "./DeliverableViewer";
import type { CardDetail } from "../../types";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body } as Response;
}

describe("DeliverableViewer", () => {
  it("renders markdown final_draft through MarkdownDocument", () => {
    const detail = { id: "R-110", final_draft: "# Weekly\n\n- point" } as unknown as CardDetail;
    const { container } = render(<DeliverableViewer detail={detail} />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Weekly");
    expect(container.querySelector("iframe")).toBeNull();
  });

  it("renders html-backfilled final_draft in a sandboxed srcdoc iframe (allow-scripts only)", () => {
    const detail = { id: "R-9", final_draft: "<!DOCTYPE html><html><body>hi</body></html>" } as unknown as CardDetail;
    const { container } = render(<DeliverableViewer detail={detail} />);
    const frame = container.querySelector("iframe")!;
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts"); // 绝不 allow-same-origin
    expect(frame.getAttribute("srcdoc")).toContain("hi");
  });

  it("serves discovered html files via /files/ url in a sandboxed iframe", () => {
    const detail = {
      id: "R-9",
      delivered_summary: "见 /Users/z/wb/deliverables/report.html",
    } as unknown as CardDetail;
    const { container } = render(<DeliverableViewer detail={detail} />);
    const frame = container.querySelector("iframe")!;
    expect(frame.getAttribute("src")).toContain("/files/deliverables/R-9/report.html");
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
  });

  it("posts /api/reveal with only card_id and surfaces failures", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true, revealed: "/tmp/x" }));
    vi.stubGlobal("fetch", fetchMock);
    const detail = { id: "R-9", final_draft: "text" } as unknown as CardDetail;
    render(<DeliverableViewer detail={detail} />);
    fireEvent.click(screen.getByRole("button", { name: "Reveal in Finder" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(String(url)).toContain("/api/reveal");
    expect(JSON.parse(String(init.body))).toEqual({ card_id: "R-9" });
    await screen.findByRole("button", { name: "Revealed in Finder" });
  });

  it("shows the empty state when a card has no previewable deliverable", () => {
    const detail = { id: "R-1" } as unknown as CardDetail;
    render(<DeliverableViewer detail={detail} />);
    expect(screen.getByText(/No previewable deliverable/)).toBeTruthy();
  });
});

// 会话名跟不上卡名时，详情面照实说一句（CONTRACT §37.1 追记）。
// 卡名改了以后 `--name` 只在下一次 dispatch/resume 生效——claude 改不了运行中会话的名字——
// 所以 `claude agents` 里那条还是旧名字。判据是 server 给的 `agent_name_stale`（防腐 #10：
// 不在客户端重算会话名），客户端只负责显示这一行，且没有列表名时不说话。
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { LanguageContext } from "../../i18n";
import type { CardDetail } from "../../types";
import { DetailFields } from "./DetailFields";

afterEach(cleanup);

const ZH = "会话名下次恢复会话时才跟上（claude 改不了运行中会话的名字）";
const EN = "The session name catches up at the next resume (claude cannot rename a live session)";

function show(detail: CardDetail, language: "zh" | "en" = "en") {
  return render(
    <LanguageContext.Provider value={language}>
      <DetailFields detail={detail} />
    </LanguageContext.Provider>,
  );
}

const running: CardDetail = {
  id: "R-410", lane: "running", title: "原始的冻结标题", name: "改过名的卡",
  display_title: "改过名的卡", session_id: "abc123", agent_name: "R-410 · 原始的冻结标题",
};

describe("§37.1 追记 — claude agents 列表名的过时提示", () => {
  it("agent_name_stale 为真时在列表名下面给一行（中英各一句）", () => {
    show({ ...running, agent_name_stale: true });
    expect(screen.getByText(EN)).toBeTruthy();
    cleanup();
    show({ ...running, agent_name_stale: true }, "zh");
    expect(screen.getByText(ZH)).toBeTruthy();
  });

  it("名字已经跟上（键缺席）就不说话", () => {
    show(running);
    expect(screen.queryByText(EN)).toBeNull();
  });

  it("没有列表名时不说话（没有会话就没有过时的名字）", () => {
    show({ ...running, agent_name: undefined, agent_name_stale: true });
    expect(screen.queryByText(EN)).toBeNull();
  });

  it("已验收卡上根本收不到这个键（server 不发）——那行承诺永不兑现，所以不该出现", () => {
    // server 侧的判例在 tests/test_session_name_follows_card_title.py（`_delivered_row`
    // 不带 `agent_name_stale`）；这里钉客户端那一半：键缺席 = 一个字都不说
    const completed: CardDetail = { ...running, lane: "completed", state: "delivered" };
    show(completed);
    expect(screen.queryByText(EN)).toBeNull();
    expect(screen.queryByText(ZH)).toBeNull();
  });

  it("这个键不落「其他字段」兜底区", () => {
    show({ ...running, agent_name_stale: true });
    expect(screen.queryByText("agent_name_stale")).toBeNull();
  });
});

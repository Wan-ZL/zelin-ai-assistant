#!/usr/bin/env python3
"""B-01 slack-mint：真形 Slack 私信重放一遍 → 铸出一张卡（§13 / §44.2 / §58 / §78）。

样本 = ``tests/fixtures/coverage_b/slack_message.json``（``fetch_new_messages``
的出参形状）。fetcher / extractor 全注入，绝不碰网络、绝不 spawn 真 claude。

§78（提案车道退役）：新卡的状态是 ``detected``（落潜在任务列），``card_sent``
永不再被写入。这一条是整条链上最容易静默退化的一格——铸卡路径若回写
``card_sent``，卡仍然在看板上可见（退役车道的存量行也投进 debt），红不了，
所以这里逐字钉死状态值。
"""
from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.qa.fixtures_b import _harness, _scan  # noqa: E402
from act.lib import registry  # noqa: E402

FIXTURE_ID = "B-01-slack-mint"


def scenario(_home):
    msg = _harness.fixture_json("coverage_b/slack_message.json")
    llm = _harness.FakeLLM(
        extraction=[{"summary": "把 Q3 rollout plan 在周四 review 前发给 manager",
                     "type": "comms", "tier": "T1", "needs_reply": True,
                     "plan": ["整理 Q3 数字", "回私信"], "permalink": msg["permalink"]}],
        decision={"action": "new_proposal", "confidence": "high"})
    n = _scan.slack_scan(msg, llm)
    cards = registry.load_all()
    facts = _harness.card_facts(cards[0] if cards else None)
    ok, why = _harness.check([
        ("scan_returned_one", n == 1),
        ("one_card", len(cards) == 1),
        ("status_detected", facts["status"] == "detected"),   # §78
        ("source_is_slack", facts["channel"] == "slack"),
        ("gate_consulted", len(llm.triage_calls) == 1),
    ])
    evidence = (f"slack ts={msg['ts']} → minted {facts['id']} "
                f"status={facts['status']} cards={len(cards)} {why}")
    return ok, evidence


def main() -> int:
    return _harness.run(FIXTURE_ID, scenario)


if __name__ == "__main__":
    raise SystemExit(main())

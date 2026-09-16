#!/usr/bin/env python3
"""B-01 slack-mint：真形 Slack 私信重放一遍 → 铸出一张提案卡（§13 / §44.2 / §58）。

样本 = ``tests/fixtures/coverage_b/slack_message.json``（``fetch_new_messages``
的出参形状）。fetcher / extractor 全注入，绝不碰网络、绝不 spawn 真 claude。
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
    card = cards[0] if cards else None
    ok, why = _harness.check([
        ("scan_returned_one", n == 1),
        ("one_card", len(cards) == 1),
        ("status_card_sent", bool(card) and card.status == "card_sent"),
        ("source_is_slack", bool(card) and (card.sources or [{}])[0].get("channel") == "slack"),
        ("gate_consulted", len(llm.triage_calls) == 1),
    ])
    evidence = (f"slack ts={msg['ts']} → minted {card.id if card else 'none'} "
                f"status={card.status if card else '-'} cards={len(cards)} {why}")
    return ok, evidence


def main() -> int:
    return _harness.run(FIXTURE_ID, scenario)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""B-04 gmail-mint：真形邮件重放一遍 → 铸出一张提案卡（§14 / §44.2 / §58）。

样本 = ``tests/fixtures/coverage_b/gmail_message.json``（IMAP 取件器的出参形状）。
取件器与提取器全注入：不连 IMAP、不出网、不 spawn 真 claude。
"""
from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.qa.fixtures_b import _harness, _scan  # noqa: E402
from act.lib import registry  # noqa: E402

FIXTURE_ID = "B-04-gmail-mint"


def extraction(msg: dict, **over) -> list:
    item = {"summary": "回复 manager：周四 review 前交 Q3 rollout plan",
            "type": "comms", "tier": "T1", "needs_reply": True, "plan": ["起草回复"],
            "from": msg["from"], "subject": msg["subject"], "message_id": msg["message_id"]}
    item.update(over)
    return [item]


def scenario(_home):
    msg = _harness.fixture_json("coverage_b/gmail_message.json")
    llm = _harness.FakeLLM(extraction=extraction(msg),
                           decision={"action": "new_proposal", "confidence": "high"})
    n = _scan.gmail_scan(msg, llm)
    cards = registry.load_all()
    card = cards[0] if cards else None
    ok, why = _harness.check([
        ("scan_returned_one", n == 1),
        ("one_card", len(cards) == 1),
        ("status_card_sent", bool(card) and card.status == "card_sent"),
        ("source_is_gmail", bool(card) and (card.sources or [{}])[0].get("channel") == "gmail"),
        ("gate_consulted", len(llm.triage_calls) == 1),
    ])
    evidence = (f"gmail uid={msg['uid']} → minted {card.id if card else 'none'} "
                f"status={card.status if card else '-'} cards={len(cards)} {why}")
    return ok, evidence


def main() -> int:
    return _harness.run(FIXTURE_ID, scenario)


if __name__ == "__main__":
    raise SystemExit(main())

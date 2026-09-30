#!/usr/bin/env python3
"""B-02 slack-fold：同一件事的第二条 Slack 消息折进既有卡，不出第二张（§44.2 / §58 / §78）。

三选一闸门判 ``relates_to`` + ``needs_action=false`` = 折叠成备注（§17 的静默并入
一族）。钉住的是「零新卡 + 备注落在目标卡上」，不是判官本身。

§78（提案车道退役）：目标卡的状态是 ``detected``（潜在任务列里等 owner 拍板的
机器卡），折叠**不许**动它——折叠是攒证据，不是替 owner 表态（§0 第 1 条）。
"""
from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.qa.fixtures_b import _harness, _scan  # noqa: E402
from act.lib import registry  # noqa: E402

FIXTURE_ID = "B-02-slack-fold"
NOTE = "manager 在私信里又催了一次 Q3 数字"


def scenario(_home):
    registry.save(registry.Requirement(
        id="R-900", title="把 Q3 rollout plan 发给 manager", status="detected",
        sources=[{"who": "manager", "channel": "slack", "date": "2026-09-14",
                  "quote": "send me the Q3 rollout plan"}]))
    msg = _harness.fixture_json("coverage_b/slack_message.json")
    llm = _harness.FakeLLM(
        extraction=[{"summary": "manager 又提了一次 Q3 rollout plan", "type": "comms",
                     "tier": "T1", "needs_reply": False, "plan": [],
                     "permalink": msg["permalink"]}],
        decision={"action": "relates_to", "req": "R-900", "note": NOTE,
                  "needs_action": False})
    n = _scan.slack_scan(msg, llm)
    cards = registry.load_all()
    target = registry.load("R-900")
    notes = (target.notes or "") if target else ""
    ok, why = _harness.check([
        ("no_new_card", n == 0),
        ("still_one_card", len(cards) == 1),
        ("note_folded_in", NOTE in notes),
        ("status_unchanged", bool(target) and target.status == "detected"),   # §78
    ])
    evidence = (f"second slack msg folded into R-900 cards={len(cards)} minted={n} "
                f"status={target.status if target else '-'} {why}")
    return ok, evidence


def main() -> int:
    return _harness.run(FIXTURE_ID, scenario)


if __name__ == "__main__":
    raise SystemExit(main())

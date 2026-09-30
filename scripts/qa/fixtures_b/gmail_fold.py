#!/usr/bin/env python3
"""B-05 gmail-fold：同一件事的第二封邮件折进既有卡，不出第二张（§14 / §44.2 / §58）。

与 B-02 同一道闸（``quick_capture.apply_triage`` 的 relates_to 分支）——两个源共用
一个三选一闸门，这里钉的是 Gmail 侧接线没绕过它。
"""
from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.qa.fixtures_b import _harness, _scan  # noqa: E402
from scripts.qa.fixtures_b.gmail_mint import extraction  # noqa: E402
from act.lib import registry  # noqa: E402

# §78（提案车道退役）：目标卡的状态是 ``detected``（潜在任务列里等 owner 拍板的
# 机器卡），折叠只攒证据、不许动状态（§0 第 1 条：只有 owner 能表态）。
FIXTURE_ID = "B-05-gmail-fold"
NOTE = "manager 又发了一封催 Q3 数字的邮件"


def scenario(_home):
    registry.save(registry.Requirement(
        id="R-901", title="把 Q3 rollout plan 发给 manager", status="detected",
        sources=[{"who": "manager", "channel": "gmail", "date": "2026-09-14",
                  "quote": "send the Q3 rollout plan"}]))
    msg = _harness.fixture_json("coverage_b/gmail_message.json")
    llm = _harness.FakeLLM(
        extraction=extraction(msg, summary="manager 又提了一次 Q3 rollout plan", needs_reply=False),
        decision={"action": "relates_to", "req": "R-901", "note": NOTE, "needs_action": False})
    n = _scan.gmail_scan(msg, llm)
    cards = registry.load_all()
    target = registry.load("R-901")
    ok, why = _harness.check([
        ("no_new_card", n == 0),
        ("still_one_card", len(cards) == 1),
        ("note_folded_in", NOTE in ((target.notes or "") if target else "")),
        ("status_unchanged", bool(target) and target.status == "detected"),   # §78
    ])
    evidence = (f"second gmail uid={msg['uid']} folded into R-901 cards={len(cards)} "
                f"minted={n} status={target.status if target else '-'} {why}")
    return ok, evidence


def main() -> int:
    return _harness.run(FIXTURE_ID, scenario)


if __name__ == "__main__":
    raise SystemExit(main())

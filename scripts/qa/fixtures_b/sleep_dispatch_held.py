#!/usr/bin/env python3
"""B-12 sleep-dispatch-held：机器在睡 → approved 卡一张都不派（CONTRACT §71.1 / §58）。

issue #311 的形态：04:10 的维护唤醒里给一台合着盖的 MacBook 派了三张卡。闸是
排队不是拒绝，所以这里同时钉「不派」与「不写卡」（零噪音）。电源读数来自真机
``pmset`` fixture 文本经注入 runner 的真解析路径——不起任何子进程。
"""
from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.qa.fixtures_b import _harness, _power  # noqa: E402
from act.lib import power, registry  # noqa: E402
from act.lib.registry import State  # noqa: E402

FIXTURE_ID = "B-12-sleep-dispatch-held"


def scenario(_home):
    reading = _power.read(_power.ASLEEP)
    verdict = power.verdict(reading)
    for rid in ("R-8100", "R-8101"):
        _power.approved(rid)
    before = {r.id: r.execution for r in registry.load_all()}
    n, ex = _power.dispatch_under(reading)
    after = {r.id: r for r in registry.load_all()}
    ok, why = _harness.check([
        ("probe_parsed_the_fixture", reading.get("state") == 1 and reading.get("max_state") == 4),
        ("verdict_is_asleep", verdict == power.ASLEEP),
        ("nothing_dispatched", n == 0 and ex.calls == []),
        ("cards_still_approved", all(r.status == State.APPROVED.value for r in after.values())),
        ("gate_wrote_nothing", all(after[i].execution == before[i] for i in before)),
    ])
    evidence = (f"pmset powerstate={reading.get('state')}/{reading.get('max_state')} → verdict={verdict}; "
                f"dispatched={n} of {len(after)} approved cards, cards untouched {why}")
    return ok, evidence


def main() -> int:
    return _harness.run(FIXTURE_ID, scenario, env={"AIASSISTANT_POWER_PROBE": "1"})


if __name__ == "__main__":
    raise SystemExit(main())

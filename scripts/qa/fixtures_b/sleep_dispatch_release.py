#!/usr/bin/env python3
"""B-13 sleep-dispatch-release：醒来第一个 tick 就把同一批卡派出去（CONTRACT §71.1 / §58）。

闸是排队不是永久拒绝——B-12 按住的那两张，在醒着的读数下必须照常起跑。读数同样
来自真机 ``pmset`` / ``ioreg`` fixture 文本（满醒 = 电源档 4/4 + Graphics 位在）。
"""
from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.qa.fixtures_b import _harness, _power  # noqa: E402
from act.lib import power  # noqa: E402

FIXTURE_ID = "B-13-sleep-dispatch-release"


def scenario(_home):
    asleep = power.verdict(_power.read(_power.ASLEEP))
    reading = _power.read(_power.AWAKE)
    verdict = power.verdict(reading)
    for rid in ("R-8100", "R-8101"):
        _power.approved(rid)
    n, ex = _power.dispatch_under(reading)
    ok, why = _harness.check([
        ("same_pass_was_asleep_before", asleep == power.ASLEEP),
        ("probe_parsed_the_fixture", reading.get("state") == reading.get("max_state") == 4),
        ("verdict_is_awake", verdict == power.AWAKE),
        ("both_cards_dispatched", n == 2 and sorted(ex.calls) == ["R-8100", "R-8101"]),
    ])
    evidence = (f"after wake: powerstate={reading.get('state')}/{reading.get('max_state')} "
                f"caps={reading.get('capabilities')} verdict={verdict}; dispatched={n} "
                f"cards={sorted(ex.calls)} {why}")
    return ok, evidence


def main() -> int:
    return _harness.run(FIXTURE_ID, scenario, env={"AIASSISTANT_POWER_PROBE": "1"})


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""B-09 daily-loop-0330-max2：03:30 解锁的每日循环一轮最多铸 2 张提案（§70 / §58）。

假时钟钉两件事：03:29 不到点、03:30 到点（``daily_loop.due``，真源
``config.DEFAULT_DAILY_LOOP_TIME``），以及预算闸——五条信号里只落
``daily_loop_max_proposals_per_day`` 张（真源 ``config.DEFAULT_DAILY_LOOP_MAX_PROPOSALS``），
余下的记在 ``skipped["cap"]`` 里而不是静默丢。绝不出网（gh 不参与本场景）。
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.qa.fixtures_b import _harness  # noqa: E402
from act.lib import config, daily_loop, registry  # noqa: E402
from act.lib.loop_inputs import Signal  # noqa: E402

FIXTURE_ID = "B-09-daily-loop-0330-max2"
TODAY = "2026-09-15"
AT_0330 = _dt.datetime(2026, 9, 15, 3, 30)
KINDS = ("pr_red", "pr_comment", "mutation", "issue", "material")


def signal(kind: str, n: int) -> Signal:
    return Signal(kind=kind, fingerprint=f"{kind}:{n}", title=f"{kind} 信号 {n}，标题够长可以成卡",
                  summary="why", plan=["p1"], dod=["d1"], cost_usd=2.0, evidence="ev",
                  priority=10 + n, ref="")


def scenario(home: Path):
    cfg = config.Config()
    budget = cfg.daily_loop_max_proposals_per_day
    state = {}
    early = daily_loop.due(cfg, state, AT_0330 - _dt.timedelta(minutes=1))
    unlocked = daily_loop.due(cfg, state, AT_0330)
    ran_today = daily_loop.due(cfg, {"last_run_day": TODAY}, AT_0330)

    signals = [signal(k, i) for i, k in enumerate(KINDS)]
    chosen, skipped = daily_loop.select_signals(signals, taken=set(), gh_titles=[], budget=budget)
    filed = daily_loop.file_proposals(chosen, TODAY, str(home))
    cards = registry.load_all()
    ok, why = _harness.check([
        ("locked_at_0329", early is False),
        ("unlocked_at_0330", unlocked is True),
        ("once_a_day", ran_today is False),
        ("budget_is_two", budget == config.DEFAULT_DAILY_LOOP_MAX_PROPOSALS == 2),
        ("chose_at_most_budget", len(chosen) == budget),
        ("rest_counted_as_cap", skipped["cap"] == len(KINDS) - budget),
        ("filed_exactly_budget", len(filed) == budget and len(cards) == budget),
        ("cards_are_self_improve", all(c.sources[0]["channel"] == "self_improve" for c in cards)),
    ])
    evidence = (f"03:30 unlocked (03:29 locked); {len(KINDS)} signals → filed {len(filed)} "
                f"cards (budget={budget}, cap-skipped={skipped['cap']}) {why}")
    return ok, evidence


def main() -> int:
    return _harness.run(FIXTURE_ID, scenario)


if __name__ == "__main__":
    raise SystemExit(main())

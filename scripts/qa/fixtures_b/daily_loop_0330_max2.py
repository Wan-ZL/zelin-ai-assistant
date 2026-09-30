#!/usr/bin/env python3
"""B-09 daily-loop-0330-max2：03:30 解锁的每日循环一轮最多铸 2 张卡（§70 / §58 / §78）。

假时钟钉两件事：03:29 不到点、03:30 到点（``daily_loop.due``，真源
``config.DEFAULT_DAILY_LOOP_TIME``），以及挑选闸。D86 之后只剩一种 card kind（``material``；
GitHub 面的 issue / pr_red / pr_comment / mutation 随读取器删除），「每 class 一天一条」的
规则因此把一天封顶在 1 张：五条素材信号只落 1 张，余下四条记在 ``skipped["kind_taken"]``
里而不是静默丢；``daily_loop_max_proposals_per_day``（真源
``config.DEFAULT_DAILY_LOOP_MAX_PROPOSALS``，仍是 2）照旧是上界——额度为 0 时一张不铸、
记进 ``skipped["cap"]``。绝不出网（素材抓取不参与本场景，信号直接构造）。FIXTURE_ID 是
持久化 token，逐字不动。

§78（提案车道退役）：铸出来的卡落 ``detected``（潜在任务列）。旋钮与函数名里的
``proposals`` 是持久化 token（配置键 / 存量安装），逐字不动；变的只有状态值，
所以这里把状态也钉进 check 列表——每日循环是回写 ``card_sent`` 最容易漏网的一处。
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
N_SIGNALS = 5


def signal(n: int, kind: str = "material") -> Signal:
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

    signals = [signal(i) for i in range(N_SIGNALS)]
    chosen, skipped = daily_loop.select_signals(signals, taken=set(), budget=budget)
    none_chosen, none_skipped = daily_loop.select_signals(signals[:1], taken=set(), budget=0)
    filed = daily_loop.file_proposals(chosen, TODAY, str(home))
    cards = registry.load_all()
    ok, why = _harness.check([
        ("locked_at_0329", early is False),
        ("unlocked_at_0330", unlocked is True),
        ("once_a_day", ran_today is False),
        ("budget_is_two", budget == config.DEFAULT_DAILY_LOOP_MAX_PROPOSALS == 2),
        ("one_card_per_kind", len(chosen) == 1),
        ("rest_counted_as_kind_taken", skipped["kind_taken"] == N_SIGNALS - 1),
        ("budget_zero_files_none", none_chosen == [] and none_skipped["cap"] == 1),
        ("filed_exactly_one", len(filed) == 1 and len(cards) == 1),
        ("cards_are_self_improve", all(c.sources[0]["channel"] == "self_improve" for c in cards)),
        ("cards_land_in_detected", all(c.status == "detected" for c in cards)),   # §78
    ])
    evidence = (f"03:30 unlocked (03:29 locked); {N_SIGNALS} material signals → filed {len(filed)} "
                f"card (budget={budget}, kind-taken={skipped['kind_taken']}) {why}")
    return ok, evidence


def main() -> int:
    return _harness.run(FIXTURE_ID, scenario)


if __name__ == "__main__":
    raise SystemExit(main())

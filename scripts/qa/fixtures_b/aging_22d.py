#!/usr/bin/env python3
"""B-08 aging-22d：22 天没动过的待验收卡照 §70.6 老化（假时钟；§70.2 追记 / §58）。

两阶段（issue #312 / D74）：第一遍只盖 add-only 执行戳 ``review_stale_notified_at``
并发**一条**汇总通知；戳满 20 小时的第二遍才 ``trash(… "stale:review_stale")``，
prev_status=review、可恢复。时钟全部注入（``today`` / ``now``），不依赖真实日期。
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.qa.fixtures_b import _harness  # noqa: E402
from act.lib import config, maintenance, registry  # noqa: E402
from act.lib.registry import Requirement, State  # noqa: E402

FIXTURE_ID = "B-08-aging-22d"
TODAY = _dt.date(2026, 9, 15)
NOW = _dt.datetime(2026, 9, 15, 3, 30, tzinfo=_dt.timezone.utc)
AGE_DAYS = 22


class Notifier:
    """注入缝（防腐 #3）：记下每一次横幅，绝不真弹通知。"""

    def __init__(self):
        self.calls = []

    def __call__(self, title, body, *a, **kw):
        self.calls.append((title, body))
        return True


def review_card(rid: str, age: int = AGE_DAYS) -> Requirement:
    day = (TODAY - _dt.timedelta(days=age)).isoformat()
    return Requirement(id=rid, title=f"{rid} 的交付草稿，等 owner 验收",
                       status=State.REVIEW.value,
                       sources=[{"channel": "meeting", "date": day, "quote": "q"}],
                       execution={"review_at": day + "T09:00:00Z"})


def scenario(_home):
    cfg = config.Config()
    registry.save(review_card("P-1"))
    notifier = Notifier()
    rows = maintenance.sweep_review_notices(cfg, today=TODAY, now=NOW, notifier=notifier)
    stamped = registry.load("P-1")
    same_round = maintenance.sweep_stale(cfg, today=TODAY, now=NOW)

    later = NOW + _dt.timedelta(hours=21)
    archived = maintenance.sweep_stale(cfg, today=later.date(), now=later)
    trashed = registry.load("P-1")
    restored = registry.restore(registry.load("P-1"))
    ok, why = _harness.check([
        ("stamped_once", [r["id"] for r in rows] == ["P-1"]),
        ("one_banner_for_the_round", len(notifier.calls) == 1),
        ("stamp_is_add_only", bool(stamped.execution.get(maintenance.REVIEW_NOTICE_STAMP))),
        ("still_review_after_pass_one", stamped.status == State.REVIEW.value),
        ("not_archived_in_the_same_round", same_round == []),
        ("archived_after_20h", [r["rule"] for r in archived] == ["review_stale"]),
        ("trashed_with_reason", trashed.trash_reason == "stale:review_stale"),
        ("restorable", restored.status == State.REVIEW.value),
    ])
    evidence = (f"review card idle {AGE_DAYS}d (threshold {cfg.daily_loop_review_stale_days}d): "
                f"stamped+1 banner, archived at +21h rule=review_stale restorable {why}")
    return ok, evidence


def main() -> int:
    return _harness.run(FIXTURE_ID, scenario)


if __name__ == "__main__":
    raise SystemExit(main())

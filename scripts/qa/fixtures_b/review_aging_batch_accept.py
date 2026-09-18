#!/usr/bin/env python3
"""B-11 review-aging-batch-accept：待验收列老化 → owner 一次验收一批（issue #312 / §11 / §70.2 追记 / §58）。

老化第一遍给三张 22 天没动的待验收卡盖戳、整轮**一条**横幅（宪法第 10 条）；
owner 随后在看板上把这一批一起验收——三个 inbox 动作文件，actd 主循环一趟 drain
全部落账（§44 单写者：写卡的只有 actd）。收尾断言：三张都是 delivered、带
``accepted_at``，第二遍老化清扫因此没东西可归档。
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
import uuid
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.qa.fixtures_b import _harness  # noqa: E402
from scripts.qa.fixtures_b.aging_22d import NOW, TODAY, Notifier, review_card  # noqa: E402
from act import actd  # noqa: E402
from act.lib import config, maintenance, registry  # noqa: E402
from act.lib.registry import State  # noqa: E402

FIXTURE_ID = "B-11-review-aging-batch-accept"
IDS = ("P-1", "P-2", "P-3")


def drop_accept(rid: str) -> None:
    """看板上那一下「验收」= 一个 inbox 动作文件（形状真源 = tests/fixtures/inbox/accept.golden.json）。"""
    body = {"action": "accept", "comment": None, "id": rid,
            "ts": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")}
    (config.INBOX_DIR / f"{uuid.uuid4()}.json").write_text(
        json.dumps(body, ensure_ascii=False), encoding="utf-8")


def scenario(_home):
    cfg = config.Config()
    for rid in IDS:
        registry.save(review_card(rid))
    notifier = Notifier()
    rows = maintenance.sweep_review_notices(cfg, today=TODAY, now=NOW, notifier=notifier)

    for rid in IDS:
        drop_accept(rid)
    applied = actd.process_inbox()
    cards = {r.id: r for r in registry.load_all()}
    later = NOW + _dt.timedelta(hours=21)
    nothing_left = maintenance.sweep_stale(cfg, today=later.date(), now=later)
    ok, why = _harness.check([
        ("all_three_stamped", sorted(r["id"] for r in rows) == list(IDS)),
        ("one_banner_for_the_batch", len(notifier.calls) == 1),
        ("drain_applied_three", applied == 3),
        ("all_delivered", all(cards[i].status == State.DELIVERED.value for i in IDS)),
        ("accepted_at_recorded", all(cards[i].execution.get("accepted_at") for i in IDS)),
        ("nothing_left_to_archive", nothing_left == []),
    ])
    evidence = (f"3 review cards idle 22d → 1 banner, batch accept drained {applied} inbox files, "
                f"all delivered; second-pass sweep archived {len(nothing_left)} {why}")
    return ok, evidence


def main() -> int:
    return _harness.run(FIXTURE_ID, scenario)


if __name__ == "__main__":
    raise SystemExit(main())

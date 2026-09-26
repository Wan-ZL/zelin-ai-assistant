#!/usr/bin/env python3
"""B-07 screenpipe-corroborate-only：屏幕 OCR 只佐证、永不铸卡（CONTRACT §45 / §58）。

回声环的一刀（owner 2026-07-25 拍板）：``provenance="screen"`` 的候选，哪怕 triage
判 ``new_proposal``，也一张卡都不许发；判 ``relates_to`` 命中还开着的卡时照常折叠。
两半都在这里跑一遍——只证明「不铸」不够，「还能佐证」也得同时成立，否则把闸门
写成静默 no-op 也能过。

§78（提案车道退役）：被佐证的那张卡现在是 ``detected``（潜在任务列）。屏幕佐证
落下备注之后它必须**还是** ``detected``——§45 的那一刀在新模型下就是「屏幕不许
把卡往前推一格」，退役前后一字不改。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.qa.fixtures_b import _harness  # noqa: E402
from act import radar  # noqa: E402
from act.lib import config, registry  # noqa: E402

FIXTURE_ID = "B-07-screenpipe-corroborate-only"
BASE = 1_760_000_000.0
NOTE = "屏幕上又见到一次 Q3 rollout plan"


def _item(title: str) -> dict:
    return {"title": title, "type": "action", "tier": "T1", "hardness": "soft",
            "deadline": None, "cost_estimate_usd": None, "urgent": False,
            "quote": "Q3 rollout plan", "provenance": "screen", "speaker": "human"}


def _scan(items: list, decision: dict):
    runner = lambda text: json.dumps(items, ensure_ascii=False)          # noqa: E731
    triager = lambda prompt: _harness.proc(json.dumps(decision, ensure_ascii=False))  # noqa: E731
    return radar.scan(runner=runner, triager=triager)


def scenario(home: Path):
    raw = home / "vault" / "2 - raw"
    raw.mkdir(parents=True)
    config.CONFIG_PATH.write_text(f'sources:\n  obsidian_raw: "{raw.as_posix()}"\n',
                                  encoding="utf-8")
    registry.save(registry.Requirement(
        id="R-902", title="把 Q3 rollout plan 发给 manager", status="detected",
        sources=[{"who": "manager", "channel": "slack", "date": "2026-09-14",
                  "quote": "Q3 rollout plan"}]))

    def note(name: str, mtime: float):
        p = raw / name
        p.write_text("screenpipe OCR 抄下来的一段屏幕文字", encoding="utf-8")
        os.utime(p, (mtime, mtime))

    note("2026-09-15-screenpipe-a.md", BASE)
    blocked = _scan([_item("屏幕上看到的一件新事，够不着卡")],
                    {"action": "new_proposal", "confidence": "high"})
    after_block = registry.load_all()

    note("2026-09-15-screenpipe-b.md", BASE + 120)
    folded = _scan([_item("屏幕上又见到 Q3 rollout plan")],
                   {"action": "relates_to", "req": "R-902", "note": NOTE,
                    "needs_action": False})
    target = registry.load("R-902")
    ok, why = _harness.check([
        ("echo_blocked_once", blocked.get("echo_blocked") == 1),
        ("no_card_from_screen", blocked.get("cards") == 0 and len(after_block) == 1),
        ("fold_not_blocked", folded.get("echo_blocked") == 0),
        ("still_one_card", len(registry.load_all()) == 1),
        ("corroboration_landed", NOTE in ((target.notes or "") if target else "")),
        ("status_not_promoted", bool(target) and target.status == "detected"),   # §78
    ])
    evidence = (f"screen new_proposal blocked={blocked.get('echo_blocked')} cards={blocked.get('cards')}; "
                f"relates_to folded into R-902 total_cards={len(registry.load_all())} {why}")
    return ok, evidence


def main() -> int:
    return _harness.run(FIXTURE_ID, scenario)


if __name__ == "__main__":
    raise SystemExit(main())

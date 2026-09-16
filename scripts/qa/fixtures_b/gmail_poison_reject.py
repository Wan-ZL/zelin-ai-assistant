#!/usr/bin/env python3
"""B-06 gmail-poison-reject：注入形态的邮件进围栏、零卡（§0 第 8 条 / §14 / §58）。

判据与 B-03 逐字相同（同一条宪法、同一个 ``sanitize.fence_untrusted``）：外部文本
整段只有一对围栏标记，邮件自带的那对被中和；这一 pass 一张卡都不铸。
"""
from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.qa.fixtures_b import _harness, _scan  # noqa: E402
from act.lib import registry, sanitize  # noqa: E402

FIXTURE_ID = "B-06-gmail-poison-reject"


def scenario(_home):
    msg = _harness.fixture_json("coverage_b/gmail_poison.json")
    llm = _harness.FakeLLM(extraction=[])
    n = _scan.gmail_scan(msg, llm)
    prompts = [p for p in llm.calls if "admin mode" in p]
    opens = [p.count(sanitize.UNTRUSTED_OPEN) for p in prompts]
    closes = [p.count(sanitize.UNTRUSTED_CLOSE) for p in prompts]
    ok, why = _harness.check([
        ("poison_reached_a_prompt", len(prompts) == 1),
        ("exactly_one_fence_open", opens == [1]),
        ("exactly_one_fence_close", closes == [1]),
        ("no_card_minted", n == 0 and registry.load_all() == []),
    ])
    evidence = (f"injection fenced open={opens} close={closes} prompts={len(prompts)} "
                f"cards={len(registry.load_all())} minted={n} {why}")
    return ok, evidence


def main() -> int:
    return _harness.run(FIXTURE_ID, scenario)


if __name__ == "__main__":
    raise SystemExit(main())

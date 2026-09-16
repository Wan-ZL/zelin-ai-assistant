#!/usr/bin/env python3
"""B-03 slack-poison-reject：注入形态的 Slack 消息进围栏、零卡（§0 第 8 条 / §13 / §58）。

样本里那条消息自己带了一对围栏标记，想把后半段伪装成系统指令。
``sanitize.fence_untrusted`` 的职责是：**整段外部文本只有一对围栏**，消息里自带的
标记被中和。所以这里数标记出现次数（公开常量，不看私名），再确认这一 pass 一张卡
都没铸。
"""
from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.qa.fixtures_b import _harness, _scan  # noqa: E402
from act.lib import registry, sanitize  # noqa: E402

FIXTURE_ID = "B-03-slack-poison-reject"


def scenario(_home):
    msg = _harness.fixture_json("coverage_b/slack_poison.json")
    llm = _harness.FakeLLM(extraction=[])          # 围栏里的「指令」不被当指令读
    n = _scan.slack_scan(msg, llm)
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

"""雷达一个 pass 的注入缝集合（CONTRACT §13 Slack / §14 Gmail / §58）。

B-01…B-06 六个场景共用这两个入口：token / auth / 取件器 / 提取器 / §44.2 fold
判官全部经参数或临时 patch 注入——绝不出网、绝不 spawn 真 claude。
"""
from __future__ import annotations

import contextlib
import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.qa.fixtures_b import _harness  # noqa: E402
from act import radar_gmail, radar_slack  # noqa: E402
from act.lib import config, silent_merge  # noqa: E402


def slack_scan(msg: dict, llm) -> int:
    """一条真形 Slack 消息走完整 pass；返回新铸卡数。"""
    with contextlib.ExitStack() as stack:
        stack.enter_context(_harness.patched(radar_slack, "get_token", lambda cfg=None: "xoxp-fixture"))
        stack.enter_context(_harness.patched(
            radar_slack, "verify_token", lambda token: {"ok": True, "user_id": "U_ME"}))
        stack.enter_context(_harness.patched(silent_merge, "JUDGE_RUNNER", _harness.judge_runner()))
        return radar_slack.scan(config.Config(),
                                fetcher=lambda tok, me, cfg, markers: [dict(msg)],
                                extractor=llm)


def gmail_scan(msg: dict, llm) -> int:
    """一封真形 Gmail 邮件走完整 pass（IMAP 侧完全注入）；返回新铸卡数。"""
    with contextlib.ExitStack() as stack:
        stack.enter_context(_harness.patched(radar_gmail, "get_app_password", lambda cfg=None: "app-pw"))
        stack.enter_context(_harness.patched(silent_merge, "JUDGE_RUNNER", _harness.judge_runner()))
        return radar_gmail.scan(config.Config(),
                                fetcher=lambda cfg, last_uid: ([dict(msg)], int(msg["uid"])),
                                extractor=llm)

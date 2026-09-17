#!/usr/bin/env python3
"""B-14 terminal-503-no-heartbeat：壳没在跑 → POST /api/terminal 503（§68.7 / §58）。

队列没有消费者就不入队——页面据这个 503 降级成「复制指令」。钉的是三件事：
状态码 503、envelope code ``SHELL_UNAVAILABLE``、**队列一条都没多**。
"""
from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.qa.fixtures_b import _harness, _terminal  # noqa: E402

FIXTURE_ID = "B-14-terminal-503-no-heartbeat"


def scenario(home: Path):
    _terminal.seed_board(home)                    # 心跳文件故意不写
    status, code, body = _terminal.post(home)
    entries = _terminal.queue_entries(home)
    ok, why = _harness.check([
        ("status_503", status == 503),
        ("code_shell_unavailable", code == "SHELL_UNAVAILABLE"),
        ("envelope_points_at_the_heartbeat",
         "heartbeat" in (body.get("error", {}).get("details") or {})),
        ("nothing_enqueued", entries == []),
    ])
    evidence = (f"POST /api/terminal (no shell.heartbeat) → {status} {code}; "
                f"queue={len(entries)} entries {why}")
    return ok, evidence


def main() -> int:
    return _harness.run(FIXTURE_ID, scenario)


if __name__ == "__main__":
    raise SystemExit(main())

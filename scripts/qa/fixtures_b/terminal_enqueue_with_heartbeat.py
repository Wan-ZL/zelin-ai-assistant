#!/usr/bin/env python3
"""B-15 terminal-enqueue-with-heartbeat：心跳新鲜 → 200 且请求入队（§68.7 / §58）。

server 只入队（``state/terminal_queue/<id>.json``），壳按节拍消费——所以验收面是
回执 + 队列里那一条：命令由 server 从投影行推导（``copy_cmd``，绝不收客户端文本），
``shell_line`` 带 ``export AIASSISTANT_HOME=`` 且不带 ``exec``（复合命令经 exec 会
静默退出，退役的 .command 通道就是这样坏的）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.qa.fixtures_b import _harness, _terminal  # noqa: E402

FIXTURE_ID = "B-15-terminal-enqueue-with-heartbeat"


def scenario(home: Path):
    row = _terminal.seed_board(home)
    _terminal.beat(home)
    status, code, body = _terminal.post(home)
    entries = _terminal.queue_entries(home)
    entry = {}
    if entries:
        entry = json.loads((Path(body["command_file"])).read_text(encoding="utf-8"))
    ok, why = _harness.check([
        ("status_200", status == 200 and code == ""),
        ("ok_true", body.get("ok") is True),
        ("command_derived_from_the_projection", body.get("command") == row["copy_cmd"]),
        ("exactly_one_queue_entry", len(entries) == 1),
        ("entry_matches_the_receipt",
         entry.get("id") == body.get("queue_id") and entry.get("kind") == "takeover"
         and entry.get("card_id") == _terminal.CARD_ID),
        ("shell_line_exports_home_and_never_execs",
         "export AIASSISTANT_HOME=" in str(entry.get("shell_line"))
         and "exec " not in str(entry.get("shell_line"))),
    ])
    evidence = (f"POST /api/terminal (fresh heartbeat) → {status} queue_id={body.get('queue_id')} "
                f"entries={len(entries)} cmd={body.get('command')} {why}")
    return ok, evidence


def main() -> int:
    return _harness.run(FIXTURE_ID, scenario)


if __name__ == "__main__":
    raise SystemExit(main())

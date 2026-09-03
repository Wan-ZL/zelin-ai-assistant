#!/usr/bin/env python3
"""web/src/parity.test.tsx 用的 demo fixture（docs/CONTRACT.md §63.2）。

vitest 跑在 jsdom 里，拿不到 python 与 server，所以把 scripts/demo_seed.py 的
`initial` 场景（固定 now，确定性）连同 server/lanes.py 的列目录落成两份 JSON：
  ui/parity/fixtures/demo-board.json   —— dashboard.json 形（+ archived[] 两行：demo_seed
                                          没有封存行，而看板右侧书立条要渲染它们）
  ui/parity/fixtures/lanes.json        —— GET /api/lanes 响应体（server.lanes.catalog()）
全部虚构数据（demo_seed 的人名/仓库均为虚构）。tests/test_ui_parity_fixture.py 钉
「重跑零 diff」。

用法：
    python3 scripts/ui/parity_fixture.py --write | --check
"""

import argparse
import datetime as dt
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, ".."))
sys.path.insert(0, os.path.join(_HERE, "..", ".."))
import demo_seed  # noqa: E402
import ui_common as uc  # noqa: E402
from server import lanes as server_lanes  # noqa: E402

FIXTURES_DIR = os.path.join(uc.PARITY_DIR, "fixtures")
BOARD_PATH = os.path.join(FIXTURES_DIR, "demo-board.json")
LANES_PATH = os.path.join(FIXTURES_DIR, "lanes.json")
FIXED_NOW = dt.datetime(2026, 9, 2, 12, 0, 0, tzinfo=dt.timezone.utc)


def _archived(now):
    """两行封存卡：一行 owner 点的永久完成、一行自动封存（ArchiveStrip 的两种落款）。"""
    return [
        {"id": "P-071", "title": "inkweld 周报模板定稿", "summary": "模板已交付并沿用三周",
         "kind": "suggestion", "archived_at": demo_seed._iso(now - dt.timedelta(days=9)),
         "archive_reason": "user", "prev_status": "delivered", "display_id": "R-071",
         "work_id": "R-071", "id_kind": "work"},
        {"id": "P-064", "title": "example-bench README 补 badge", "summary": "冷交付 30 天自动封存",
         "kind": "suggestion", "archived_at": demo_seed._iso(now - dt.timedelta(days=21)),
         "archive_reason": "auto", "prev_status": "delivered", "display_id": "R-064",
         "work_id": "R-064", "id_kind": "work"},
    ]


def build_board(now=FIXED_NOW):
    board = demo_seed.build("initial", now=now)
    board["archived"] = _archived(now)
    board["counts"]["archived"] = len(board["archived"])
    board["device_label"] = "demo-mac"
    return board


def build_lanes():
    return server_lanes.catalog()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--board", default=BOARD_PATH)
    parser.add_argument("--lanes", default=LANES_PATH)
    args = parser.parse_args(argv)
    fresh = {args.board: uc.dump_json(build_board()), args.lanes: uc.dump_json(build_lanes())}
    if args.write:
        for path, text in fresh.items():
            uc.write_text(path, text)
        print("wrote %s" % ", ".join(os.path.relpath(p, uc.REPO_ROOT) for p in fresh))
    return _check_fresh(fresh) if args.check else 0


def _check_fresh(fresh):
    stale = [p for p, text in fresh.items() if not os.path.exists(p) or uc.read_text(p) != text]
    if stale:
        print("stale fixture(s): %s — rerun with --write"
              % ", ".join(os.path.relpath(p, uc.REPO_ROOT) for p in stale), file=sys.stderr)
        return 1
    print("parity fixtures are fresh")
    return 0


if __name__ == "__main__":
    sys.exit(main())

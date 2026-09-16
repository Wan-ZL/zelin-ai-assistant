#!/usr/bin/env python3
"""B-10 media-prune-60min：60 分钟原始媒体清理那一轮的回执与投影（§72.4 / §58）。

链（``ingest/screenpipe-cleanup.sh``）每 30 分钟删一次早于
``recording.media_retention_minutes`` 分钟的 jpg/mp4，并覆盖写一份回执
``state/screenpipe_prune.json``；``GET /api/screenpipe/disk`` 经
``server.screenpipe_disk.media_prune`` 把它原样带出来 + 算出岁数与 ``stale``。

本场景钉三件事：出厂保留期就是 60（act 侧真源 = ``config.DEFAULT_MEDIA_RETENTION_MINUTES``）、
一轮干净跑完的回执照原样投影且不 stale、回执缺席时报 ``never`` + ``stale``
（「清理停了」与「跑了但没东西可删」必须分得开）。

不起子进程（那会碰 owner 真的 ``~/.screenpipe``）：这里写的就是脚本
``write_receipt`` 那一行的字节形状，判据全在 Python 侧的投影上。
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.qa.fixtures_b import _harness  # noqa: E402
from act.lib import config  # noqa: E402
from server import screenpipe_disk as disk  # noqa: E402

FIXTURE_ID = "B-10-media-prune-60min"
NOW = 1_800_000_000.0


def _iso(epoch: float) -> str:
    return _dt.datetime.fromtimestamp(epoch, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def scenario(home: Path):
    retention = config.Config().screenpipe_media_retention_minutes
    missing = disk.media_prune(home, NOW)
    receipt = {"ts": _iso(NOW - 600), "state": "ok", "retention_minutes": retention,
               "deleted_files": 12, "deleted_bytes": 345_678,
               "data_dir": str(home / ".screenpipe" / "data"), "last_ok_ts": _iso(NOW - 600)}
    (home / "state" / disk.PRUNE_RECEIPT_NAME).write_text(
        json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    fresh = disk.media_prune(home, NOW)
    (home / "state" / disk.PRUNE_RECEIPT_NAME).write_text(
        json.dumps(dict(receipt, ts=_iso(NOW - disk.PRUNE_STALE_S - 60),
                        last_ok_ts=_iso(NOW - disk.PRUNE_STALE_S - 60)), ensure_ascii=False),
        encoding="utf-8")
    stopped = disk.media_prune(home, NOW)
    ok, why = _harness.check([
        ("factory_window_is_60", retention == config.DEFAULT_MEDIA_RETENTION_MINUTES == 60),
        ("no_receipt_is_never", missing["state"] == "never" and missing["stale"] is True),
        ("receipt_projected_verbatim",
         (fresh["state"], fresh["deleted_files"], fresh["retention_minutes"]) == ("ok", 12, 60)),
        ("fresh_round_not_stale", fresh["stale"] is False and fresh["age_seconds"] == 600.0),
        ("a_stopped_chain_is_stale", stopped["stale"] is True),
    ])
    evidence = (f"prune receipt retention={retention}min state={fresh['state']} "
                f"deleted={fresh['deleted_files']} age={fresh['age_seconds']}s stale={fresh['stale']}; "
                f"missing→{missing['state']} {why}")
    return ok, evidence


def main() -> int:
    return _harness.run(FIXTURE_ID, scenario)


if __name__ == "__main__":
    raise SystemExit(main())

"""judges.json 的一行摘要（CONTRACT §77 拟；QA 面 §58）。

输出**恰好**一行：`JUDGES seats=<k> pass=<p> rounds=<r>`——k / p 取最后一轮（active 席位数、及格席位数），
r = 跑过的轮数。BLOCKED 席（kimi-k3）不计入 k（缺 FIREWORKS_API_KEY，不顶替、不删席）。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

PASS_TOTAL = 7


def summarize(doc: Dict[str, Any]) -> str:
    rounds = doc.get("rounds") or []
    if not rounds:
        return "JUDGES seats=0 pass=0 rounds=0"
    last = max(rounds, key=lambda r: r.get("round", 0))
    seats = [s for s in last.get("seats", []) if s.get("status") not in ("BLOCKED", "ERROR") and "total" in s]
    passing = [s for s in seats if int(s["total"]) >= PASS_TOTAL]
    return f"JUDGES seats={len(seats)} pass={len(passing)} rounds={len(rounds)}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="print the one-line judges summary")
    ap.add_argument("judges", help="path to judges.json")
    args = ap.parse_args(argv)
    print(summarize(json.loads(Path(args.judges).read_text(encoding="utf-8"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

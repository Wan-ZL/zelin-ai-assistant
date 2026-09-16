"""`judge_summary.py` 的那一行（CONTRACT §77 拟 demo 视频管线；QA 面 §58）。

验收命令 grep 的就是这一行，形状必须逐字不变：`JUDGES seats=<k> pass=<p> rounds=<r>`——
k / p 取**最后一轮**（BLOCKED / ERROR 席不计入 k），r = 轮数。无网络，喂的是 judges.json 的 fixture。
"""
from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MEDIA = REPO / "scripts" / "media"

_SPEC = importlib.util.spec_from_file_location("_zai_media_judge_summary", MEDIA / "judge_summary.py")
summary_mod = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(summary_mod)


def _seat(name, total):
    return {"seat": name, "scores": {}, "total": total, "verdict": "pass" if total >= 7 else "fail", "fixes": []}


DOC = {
    "rounds": [
        {"round": 1, "seats": [_seat("claude-fable-5-1", 6), _seat("claude-opus-5", 7), _seat("gpt-5.5", 7)]},
        {"round": 2, "seats": [_seat("claude-fable-5-1", 7), _seat("claude-opus-5", 8), _seat("gpt-5.5", 8)]},
    ],
    "blocked_seats": [{"seat": "kimi-k3", "status": "BLOCKED", "reason": "FIREWORKS_API_KEY absent"}],
}


class SummaryTest(unittest.TestCase):
    def test_last_round_wins(self):
        self.assertEqual(summary_mod.summarize(DOC), "JUDGES seats=3 pass=3 rounds=2")

    def test_single_round(self):
        doc = {"rounds": DOC["rounds"][:1], "blocked_seats": DOC["blocked_seats"]}
        self.assertEqual(summary_mod.summarize(doc), "JUDGES seats=3 pass=2 rounds=1")

    def test_blocked_and_error_seats_drop_out_of_k(self):
        doc = {"rounds": [{"round": 1, "seats": [
            _seat("claude-opus-5", 9),
            {"seat": "kimi-k3", "status": "BLOCKED", "reason": "FIREWORKS_API_KEY absent"},
            {"seat": "gpt-5.5", "status": "ERROR", "reason": "HTTP 500"},
        ]}]}
        self.assertEqual(summary_mod.summarize(doc), "JUDGES seats=1 pass=1 rounds=1")

    def test_rounds_out_of_order_still_take_the_highest(self):
        doc = {"rounds": list(reversed(DOC["rounds"]))}
        self.assertEqual(summary_mod.summarize(doc), "JUDGES seats=3 pass=3 rounds=2")

    def test_empty_document(self):
        self.assertEqual(summary_mod.summarize({"rounds": []}), "JUDGES seats=0 pass=0 rounds=0")

    def test_cli_prints_exactly_one_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "judges.json"
            path.write_text(json.dumps(DOC, ensure_ascii=False), encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = summary_mod.main([str(path)])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue(), "JUDGES seats=3 pass=3 rounds=2\n")


if __name__ == "__main__":
    unittest.main()

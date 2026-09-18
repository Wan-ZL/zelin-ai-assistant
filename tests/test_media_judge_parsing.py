"""评委回复的解析与判决（CONTRACT §77 拟 demo 视频管线；QA 面 §58）。

LLM 输出不可信（宪法第 11 条）：围栏、前后废话、浮点分数、越界分数、多回一维——都得消毒成
一条定形的席位记录；解析不了的一席记 ERROR，不拖垮另外两席，也不许悄悄算进及格数。
全程无网络：这里喂的是回复文本的 fixture。
"""
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MEDIA = REPO / "scripts" / "media"


def _load(name: str):
    import sys

    if str(MEDIA) not in sys.path:
        sys.path.insert(0, str(MEDIA))
    spec = importlib.util.spec_from_file_location(f"_zai_media_{name}", MEDIA / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


judge = _load("judge")

GOOD = json.dumps({
    "seat": "whoever",
    "scores": {"真实性": 2, "清晰": 2, "可读": 1, "节奏": 2, "无误导": 2},
    "total": 99,
    "verdict": "fail",
    "fixes": ["第 3 镜放慢", "  ", 4],
}, ensure_ascii=False)


class ParseTest(unittest.TestCase):
    def test_plain_json(self):
        rec = judge.parse_seat_payload("claude-opus-5", GOOD)
        self.assertEqual(rec["seat"], "claude-opus-5")  # 席位名由调用方定，不信模型自报
        self.assertEqual(rec["total"], 9)               # total 按分项重算，模型写的 99 作废
        self.assertEqual(rec["verdict"], "pass")        # verdict 由 total 判定，模型写的 fail 作废
        self.assertEqual(rec["fixes"], ["第 3 镜放慢", "4"])

    def test_fenced_json_with_chatter(self):
        text = "好的，我的评分如下：\n```json\n" + GOOD + "\n```\n希望有帮助。"
        self.assertEqual(judge.parse_seat_payload("gpt-5.5", text)["total"], 9)

    def test_floats_and_out_of_range_are_clamped(self):
        text = json.dumps({"scores": {"真实性": 1.4, "清晰": 2.0, "可读": 7, "节奏": -3, "无误导": 0}}, ensure_ascii=False)
        rec = judge.parse_seat_payload("seat", text)
        self.assertEqual(rec["scores"], {"真实性": 1, "清晰": 2, "可读": 2, "节奏": 0, "无误导": 0})
        self.assertEqual(rec["total"], 5)
        self.assertEqual(rec["verdict"], "fail")

    def test_missing_dimension_is_an_error(self):
        text = json.dumps({"scores": {"真实性": 2, "清晰": 2, "可读": 2, "节奏": 2}}, ensure_ascii=False)
        with self.assertRaises(ValueError):
            judge.parse_seat_payload("seat", text)

    def test_string_score_is_an_error(self):
        text = json.dumps({"scores": {"真实性": "2", "清晰": 2, "可读": 2, "节奏": 2, "无误导": 2}}, ensure_ascii=False)
        with self.assertRaises(ValueError):
            judge.parse_seat_payload("seat", text)

    def test_bool_score_is_an_error(self):
        text = json.dumps({"scores": {"真实性": True, "清晰": 2, "可读": 2, "节奏": 2, "无误导": 2}}, ensure_ascii=False)
        with self.assertRaises(ValueError):
            judge.parse_seat_payload("seat", text)

    def test_no_json_at_all(self):
        with self.assertRaises(ValueError):
            judge.parse_seat_payload("seat", "我拒绝回答。")

    def test_fixes_cap_at_five(self):
        text = json.dumps({"scores": {d: 0 for d in judge.DIMENSIONS}, "fixes": [f"f{i}" for i in range(9)]})
        self.assertEqual(len(judge.parse_seat_payload("seat", text)["fixes"]), 5)


class SeatRecordTest(unittest.TestCase):
    def test_a_failing_seat_is_recorded_not_raised(self):
        rec = judge._seat_record("claude-fable-5-1", lambda: (_ for _ in ()).throw(RuntimeError("HTTP 529: overloaded")))
        self.assertEqual(rec["status"], "ERROR")
        self.assertIn("529", rec["reason"])


class VerdictTest(unittest.TestCase):
    def _seat(self, name, total):
        return {"seat": name, "scores": {}, "total": total, "verdict": "pass" if total >= 7 else "fail", "fixes": []}

    def test_two_of_three_passes(self):
        seats = [self._seat("a", 7), self._seat("b", 6), self._seat("c", 8)]
        self.assertEqual(judge.round_verdict(seats), {"seats": 3, "pass": 2, "verdict": "pass"})

    def test_one_of_three_fails_the_round(self):
        seats = [self._seat("a", 7), self._seat("b", 6), self._seat("c", 3)]
        self.assertEqual(judge.round_verdict(seats), {"seats": 3, "pass": 1, "verdict": "fail"})

    def test_blocked_and_error_seats_are_not_counted(self):
        seats = [self._seat("a", 8), self._seat("b", 9),
                 {"seat": "kimi-k3", "status": "BLOCKED", "reason": "FIREWORKS_API_KEY absent"},
                 {"seat": "c", "status": "ERROR", "reason": "boom"}]
        self.assertEqual(judge.round_verdict(seats), {"seats": 2, "pass": 2, "verdict": "pass"})


class FrameBudgetTest(unittest.TestCase):
    def test_key_frames_sit_in_the_middle_of_each_shot(self):
        shots = [{"id": "a", "seconds": 10, "zh": "一", "en": "one"}, {"id": "b", "seconds": 4, "zh": "二", "en": "two"}]
        self.assertEqual(judge.key_frame_seconds(shots), [5, 12])

    def test_frames_are_capped_and_keep_every_key_frame(self):
        available = list(range(134))
        keys = [7, 20, 33, 46, 59, 72, 85, 98, 111, 124, 130]
        picked = judge.pick_frames(available, keys, cap=40)
        self.assertLessEqual(len(picked), 40)
        self.assertEqual(picked, sorted(set(picked)))
        for key in keys:
            self.assertIn(key, picked)

    def test_cap_below_the_key_frame_count_keeps_the_earliest(self):
        picked = judge.pick_frames(list(range(20)), [1, 5, 9, 13], cap=2)
        self.assertEqual(picked, [1, 5])

    def test_blocked_seat_is_declared_not_substituted(self):
        self.assertEqual(judge.BLOCKED_SEAT["seat"], "kimi-k3")
        self.assertEqual(judge.BLOCKED_SEAT["status"], "BLOCKED")
        self.assertEqual(judge.ANTHROPIC_SEATS, ("claude-fable-5-1", "claude-opus-5"))
        self.assertEqual(judge.OPENAI_SEAT, "gpt-5.5")


if __name__ == "__main__":
    unittest.main()

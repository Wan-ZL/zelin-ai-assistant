"""字幕时间轴的算术（CONTRACT §77 拟 demo 视频管线；QA 面 §58）。

SRT 的时间来自 shots.json 的计划时长累加——剪辑把每段裁到同一个计划时长，所以这里钉的是
「累加 + 尾隙」这套算术，不是任何一次录制的实测长度（实测会随机器漂移）。
"""
from __future__ import annotations

import importlib.util
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


srt = _load("srt")
shots_mod = _load("shots")

FIXTURE = [
    {"id": "a", "seconds": 10, "zh": "一", "en": "one"},
    {"id": "b", "seconds": 5, "zh": "二", "en": "two"},
    {"id": "c", "seconds": 12, "zh": "三", "en": "three"},
]


class StampTest(unittest.TestCase):
    def test_hours_minutes_seconds_millis(self):
        self.assertEqual(srt.stamp(0), "00:00:00,000")
        self.assertEqual(srt.stamp(13.8), "00:00:13,800")
        self.assertEqual(srt.stamp(61.25), "00:01:01,250")
        self.assertEqual(srt.stamp(3723.004), "01:02:03,004")

    def test_negative_is_rejected(self):
        with self.assertRaises(ValueError):
            srt.stamp(-0.001)


class TimelineTest(unittest.TestCase):
    def test_shots_are_laid_end_to_end(self):
        rows = shots_mod.timeline(FIXTURE)
        self.assertEqual([(r["start"], r["end"]) for r in rows], [(0, 10), (10, 15), (15, 27)])

    def test_total_is_the_sum(self):
        self.assertEqual(shots_mod.total_seconds(FIXTURE), 27)


class CueTest(unittest.TestCase):
    def test_each_cue_ends_before_the_next_one_starts(self):
        rows = srt.cues(FIXTURE, "zh")
        self.assertEqual([c["index"] for c in rows], [1, 2, 3])
        self.assertEqual([c["start"] for c in rows], [0.0, 10.0, 15.0])
        self.assertEqual([round(c["end"], 3) for c in rows], [9.8, 14.8, 26.8])
        for earlier, later in zip(rows, rows[1:]):
            self.assertLess(earlier["end"], later["start"])

    def test_text_follows_the_language(self):
        self.assertEqual([c["text"] for c in srt.cues(FIXTURE, "en")], ["one", "two", "three"])

    def test_zero_gap_keeps_cues_inside_the_shot(self):
        rows = srt.cues(FIXTURE, "zh", tail_gap=0.0)
        self.assertEqual([c["end"] for c in rows], [10.0, 15.0, 27.0])

    def test_gap_never_pushes_the_end_before_the_start(self):
        rows = srt.cues([{"id": "tiny", "seconds": 1, "zh": "短", "en": "short"}], "zh", tail_gap=5.0)
        self.assertEqual(rows[0]["end"], 0.0)


class RenderTest(unittest.TestCase):
    def test_block_shape_is_srt(self):
        body = srt.render(FIXTURE, "zh")
        self.assertTrue(body.startswith("1\n00:00:00,000 --> 00:00:09,800\n一\n"))
        self.assertEqual(body.count(" --> "), 3)
        self.assertIn("\n\n3\n", body)


class ShotValidationTest(unittest.TestCase):
    def test_committed_storyboard_obeys_the_caps(self):
        data = shots_mod.load()
        total = shots_mod.total_seconds(data["shots"])
        self.assertGreaterEqual(total, shots_mod.MIN_TOTAL_SECONDS)
        self.assertLessEqual(total, shots_mod.MAX_TOTAL_SECONDS)
        for shot in data["shots"]:
            self.assertLessEqual(shot["seconds"], shots_mod.MAX_SHOT_SECONDS, shot["id"])


if __name__ == "__main__":
    unittest.main()

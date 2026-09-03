"""scripts/demo_seed.py ``--english`` / ``--lang`` (issue #18) — one dataset, two vocabularies.

Pinned behavior:
  - default output is byte-for-byte what it was before the flag existed (Chinese;
    nothing in ``build()`` changes for lang="zh");
  - ``--english`` / ``--lang en`` produce a dashboard with NO CJK character in any
    string value, for every scene — the ``_EN`` table must cover every dataset
    string (add a Chinese string without its row and this fails);
  - localization changes only string VALUES: ids, keys, numbers, timestamps, lane
    sizes and the validator's verdict are identical in both languages;
  - ``--check`` accepts what ``--english`` wrote; the ``_EN`` table has no unused rows
    (dead vocabulary would rot silently).
In-process (module loaded by path, stdlib-only) — no subprocess.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - hermetic sandbox HOME

from act.lib import card_summary

REPO_ROOT = Path(__file__).resolve().parent.parent
# Server-owned wire vocabulary that is Chinese by contract and rendered per language by
# the client (§64 verdict tokens → web VerdictChip labels): not prose, not a translation target.
_WIRE_VOCAB = frozenset(card_summary.VERDICTS)
DEMO_SEED_PATH = REPO_ROOT / "scripts" / "demo_seed.py"
# CJK unified ideographs + fullwidth/CJK punctuation（「」，。？：）
_CJK = re.compile(r"[　-〿㐀-䶿一-鿿＀-￯]")
NOW = dt.datetime(2026, 9, 2, 12, 0, 0, tzinfo=dt.timezone.utc)


def _load():
    spec = importlib.util.spec_from_file_location("_zai_demo_seed_i18n", DEMO_SEED_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _strings(obj, path=""):
    """Yield (json_path, value) for every string leaf."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _strings(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


def _shape(obj):
    """Same structure with every string leaf replaced by a marker."""
    if isinstance(obj, dict):
        return {k: _shape(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_shape(v) for v in obj]
    if isinstance(obj, str):
        return "<str>"
    return obj


class DemoSeedEnglishTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = _load()

    def test_default_is_unchanged_chinese(self):
        zh = self.ds.build("initial", NOW)
        self.assertEqual(zh, self.ds.build("initial", NOW, lang="zh"))
        hero = next(c for c in zh["needs_approval"] if c["id"] == self.ds.HERO_ID)
        self.assertEqual(hero["title"], "example-bench: leaderboard 一键导出评测报告")
        self.assertTrue(any(_CJK.search(v) for _, v in _strings(zh)))

    def test_english_has_no_cjk_in_any_scene_and_validates(self):
        for scene in self.ds.SCENES:
            with self.subTest(scene=scene):
                en = self.ds.build(scene, NOW, lang="en")
                leaks = [(p, v) for p, v in _strings(en) if _CJK.search(v) and v not in _WIRE_VOCAB]
                self.assertEqual(leaks, [], f"untranslated strings in scene={scene}")
                self.assertEqual(self.ds.validate(en), [])

    def test_localization_changes_only_string_values(self):
        for scene in self.ds.SCENES:
            with self.subTest(scene=scene):
                zh = self.ds.build(scene, NOW)
                en = self.ds.build(scene, NOW, lang="en")
                self.assertEqual(_shape(zh), _shape(en))
                self.assertEqual(zh["counts"], en["counts"])
                self.assertEqual(zh["generated_at"], en["generated_at"])
                for lane in self.ds.SECTIONS:
                    self.assertEqual([c["id"] for c in zh[lane]], [c["id"] for c in en[lane]])
                    self.assertEqual([c.get("display_id") for c in zh[lane]],
                                     [c.get("display_id") for c in en[lane]])

    def test_english_hero_reads_in_english(self):
        en = self.ds.build("initial", NOW, lang="en")
        hero = next(c for c in en["needs_approval"] if c["id"] == self.ds.HERO_ID)
        self.assertEqual(hero["title"], "example-bench: one-click leaderboard report export")
        self.assertEqual(hero["tier_hint"], "One-click approval")
        self.assertIn("Export report", hero["summary"])
        review = self.ds.build("review", NOW, lang="en")
        hero_review = next(c for c in review["review"] if c["id"] == self.ds.HERO_ID)
        self.assertTrue(hero_review["delivered_summary"].startswith("Draft PR example-bench#42 opened"))

    def test_every_en_row_is_used_by_some_scene(self):
        used = set()
        for scene in self.ds.SCENES:
            used.update(v for _, v in _strings(self.ds.build(scene, NOW)))
        unused = sorted(k for k in self.ds._EN if k not in used)
        self.assertEqual(unused, [], "dead rows in _EN — dataset string changed without its row")

    def test_cli_flags_write_english_and_check_accepts_it(self):
        with tempfile.TemporaryDirectory(prefix="demo-seed-en-") as tmp:
            for argv in (["--english"], ["--lang", "en"]):
                with self.subTest(argv=argv):
                    self.assertEqual(self.ds.main([tmp, *argv]), 0)
                    path = Path(tmp) / "state" / "dashboard.json"
                    dash = json.loads(path.read_text(encoding="utf-8"))
                    self.assertFalse(any(_CJK.search(v) and v not in _WIRE_VOCAB for _, v in _strings(dash)))
                    self.assertEqual(self.ds.main([str(path), "--check"]), 0)
            self.assertEqual(self.ds.main([tmp]), 0)   # default still Chinese
            dash = json.loads((Path(tmp) / "state" / "dashboard.json").read_text(encoding="utf-8"))
            self.assertTrue(any(_CJK.search(v) for _, v in _strings(dash)))


if __name__ == "__main__":
    unittest.main()

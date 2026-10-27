"""scripts/demo_seed — build() output pinned byte for byte (v2.0.0 recap fixture).

``tests/fixtures/demo_seed/build.golden.json`` holds the full ``initial`` scene for a
frozen clock plus a sha256 per scene, so any drift in the fixture data, in the
``recap_text`` rendering it borrows (copy_* / problems) or in the file layout flips
this test. Regenerate deliberately (and say why in the PR):

    python3 tests/test_demo_seed_golden.py --regenerate
"""
import datetime as dt
import hashlib
import json
import sys
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import demo_seed  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures" / "demo_seed"
NOW = dt.datetime(2026, 9, 2, 12, 0, 0, tzinfo=dt.timezone.utc)


def _digest(files: dict) -> str:
    text = json.dumps(files, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _golden() -> dict:
    return {"sha256": {scene: _digest(demo_seed.build(scene, now=NOW)) for scene in demo_seed.SCENES},
            "initial": demo_seed.build("initial", now=NOW)}


class BuildGoldenTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads((FIXTURES / "build.golden.json").read_text(encoding="utf-8"))

    def test_every_scene_hash(self):
        for scene in demo_seed.SCENES:
            with self.subTest(scene=scene):
                self.assertEqual(_digest(demo_seed.build(scene, now=NOW)), self.golden["sha256"][scene])

    def test_initial_full_scene(self):
        self.assertEqual(demo_seed.build("initial", now=NOW), self.golden["initial"])

    def test_no_stale_scene_in_the_golden(self):
        self.assertEqual(sorted(self.golden["sha256"]), sorted(demo_seed.SCENES))


if __name__ == "__main__":
    if sys.argv[1:] == ["--regenerate"]:
        FIXTURES.mkdir(parents=True, exist_ok=True)
        (FIXTURES / "build.golden.json").write_text(
            json.dumps(_golden(), ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print("wrote", FIXTURES / "build.golden.json")
    else:
        unittest.main()

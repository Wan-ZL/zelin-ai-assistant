"""贴图附件 GC — actd._sweep_attachment_dirs / gc_attachments.

state/attachments/ 与 state/feedback/attachments/ 只写不删会无限增长；日频
节流的 sweep 删「无引用且 mtime > 30 天」的孤儿。引用源 = registry 全部卡
（含 trash 状态与 archive/ 里的归档卡）的 execution.attachments + 每份
state/feedback/*.json 的 images。被引用的永不删；年轻孤儿（<30 天）留给
in-flight 的 inbox 动作。

Hermetic: everything under the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import os
import time
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd
from act.lib import config, feedback, registry
from act.lib.registry import Requirement

ATT_DIR = config.STATE_DIR / "attachments"
FB_ATT_DIR = config.STATE_DIR / "feedback" / "attachments"

OLD = 40 * 24 * 3600     # comfortably past the 30-day grace
YOUNG = 5 * 24 * 3600


def _mk_file(path, age_s):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"png-bytes")
    old = time.time() - age_s
    os.utime(path, (old, old))
    return path


class SweepTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for d in (config.REGISTRY_DIR, registry.ARCHIVE_DIR,
                  feedback.FEEDBACK_DIR, ATT_DIR, FB_ATT_DIR):
            if d.exists():
                for p in d.glob("*"):
                    if p.is_file():
                        p.unlink()

    def tearDown(self):
        # leave no cards/records behind for other suites — including the
        # attachment DIRS themselves (test_feedback's _clear_dir unlinks every
        # FEEDBACK_DIR entry and would trip over a leftover subdirectory).
        self.setUp()
        for d in (FB_ATT_DIR, ATT_DIR):
            try:
                d.rmdir()
            except OSError:
                pass

    def test_old_orphans_are_deleted_in_both_dirs(self):
        a = _mk_file(ATT_DIR / "orphan-1.png", OLD)
        b = _mk_file(FB_ATT_DIR / "orphan-2.png", OLD)
        removed = actd._sweep_attachment_dirs()
        self.assertEqual(removed, 2)
        self.assertFalse(a.exists())
        self.assertFalse(b.exists())

    def test_young_orphans_survive(self):
        a = _mk_file(ATT_DIR / "young-1.png", YOUNG)
        self.assertEqual(actd._sweep_attachment_dirs(), 0)
        self.assertTrue(a.exists())

    def test_registry_referenced_files_survive_any_age(self):
        kept = _mk_file(ATT_DIR / "kept-1.png", OLD)
        trashed = _mk_file(ATT_DIR / "kept-2.png", OLD)
        registry.save(Requirement(
            id="R-9301", title="活动卡", status="executing",
            execution={"attachments": [str(kept)]}))
        registry.save(Requirement(
            id="R-9302", title="回收站卡", status="trashed",
            execution={"attachments": [str(trashed)]}))
        self.assertEqual(actd._sweep_attachment_dirs(), 0)
        self.assertTrue(kept.exists())
        self.assertTrue(trashed.exists())

    def test_archived_card_attachments_survive(self):
        # 归档卡是真实工作数据 — the sweep must load archive/ too.
        kept = _mk_file(ATT_DIR / "kept-arch.png", OLD)
        req = Requirement(id="R-9303", title="归档卡", status="delivered",
                          execution={"attachments": [str(kept)]})
        registry.save(req)
        registry.archive(req, "test")
        self.assertTrue((registry.ARCHIVE_DIR / "R-9303.yaml").exists())
        self.assertEqual(actd._sweep_attachment_dirs(), 0)
        self.assertTrue(kept.exists())

    def test_feedback_record_images_survive(self):
        kept = _mk_file(FB_ATT_DIR / "kept-fb.png", OLD)
        gone = _mk_file(FB_ATT_DIR / "orphan-fb.png", OLD)
        rec = feedback.record_feedback([], "带图建议", cfg=None,
                                       transport=lambda row: None,
                                       images=[str(kept)])
        self.assertIsNotNone(rec)
        self.assertEqual(actd._sweep_attachment_dirs(), 1)
        self.assertTrue(kept.exists())
        self.assertFalse(gone.exists())

    def test_gc_attachments_is_daily_throttled(self):
        orphan = _mk_file(ATT_DIR / "throttled.png", OLD)
        # fresh marker -> the sweep must not even run
        actd._ATTACH_GC_MARKER.parent.mkdir(parents=True, exist_ok=True)
        actd._ATTACH_GC_MARKER.touch()
        self.assertEqual(actd.gc_attachments(), 0)
        self.assertTrue(orphan.exists())
        # stale marker -> the sweep runs and refreshes the marker
        stale = time.time() - actd._ATTACH_GC_INTERVAL_S - 60
        os.utime(actd._ATTACH_GC_MARKER, (stale, stale))
        self.assertEqual(actd.gc_attachments(), 1)
        self.assertFalse(orphan.exists())
        self.assertGreater(actd._ATTACH_GC_MARKER.stat().st_mtime, stale)

    def test_corrupt_card_file_aborts_the_whole_sweep(self):
        # fail SAFE: load_all silently SKIPS a corrupt card file, so its
        # attachment references would be invisible and the files "orphans" —
        # the GC parses strictly instead and removes NOTHING this pass.
        orphan_a = _mk_file(ATT_DIR / "protected-a.png", OLD)
        orphan_b = _mk_file(FB_ATT_DIR / "protected-b.png", OLD)
        (config.REGISTRY_DIR / "R-9304.yaml").write_text(
            "{{{ this is not yaml ::", encoding="utf-8")
        try:
            actd._ATTACH_GC_MARKER.unlink()
        except OSError:
            pass
        self.assertEqual(actd.gc_attachments(), 0)
        self.assertTrue(orphan_a.exists())
        self.assertTrue(orphan_b.exists())

    def test_corrupt_feedback_record_skips_only_the_feedback_dir(self):
        # one unreadable feedback record hides ITS images — the feedback
        # attachments dir is left alone this pass, while state/attachments/
        # (guarded by the intact registry scan) is still swept.
        fb_orphan = _mk_file(FB_ATT_DIR / "protected-fb.png", OLD)
        att_orphan = _mk_file(ATT_DIR / "true-orphan.png", OLD)
        (feedback.FEEDBACK_DIR / "broken.json").write_text(
            "{not json", encoding="utf-8")
        self.assertEqual(actd._sweep_attachment_dirs(), 1)
        self.assertTrue(fb_orphan.exists())
        self.assertFalse(att_orphan.exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

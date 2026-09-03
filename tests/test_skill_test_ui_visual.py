"""test-ui skill · VISUAL 传感器判例（合成 PNG，零二进制 fixture）：1% 植入色块 → changed_pct 0.01 ± 1e-6 且区域
bbox 对；容差；遮罩排除 + masked_ratio 超帽；尺寸不同 = dimensions 不缩放；截断 PNG 抛；热图尺寸；golden 台账
（sha 不符 = unreviewed、无 reason = reasonless、悬空、坏 manifest、无 manifest）；machine_key；compare_shot
阈值边界；odiff 三种退出码解析。零子进程（odiff 走 FakeRunner）。

法典：docs/CONTRACT.md §UI-parity.4；设计 vnext2-plan R2.8。
"""
import contextlib
import io
import os
import tempfile
import unittest

from tests import skill_test_ui_testkit as kit

import visual  # noqa: E402

tc = kit.tc


def _img(data):
    return visual.Image(*tc.decode_png(data))


def _quiet_main(argv):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return visual.main(argv)


class DiffTestCase(unittest.TestCase):
    def test_one_percent_block(self):
        """200×100 = 20,000 px；20×10 色块 = 200 px = 1%。"""
        base, planted = _img(kit.make_png(200, 100)), _img(kit.make_png(200, 100, blocks=[[10, 10, 20, 10, (255, 0, 0)]]))
        result = visual.diff_images(base, planted)
        self.assertEqual(result["status"], "changed")
        self.assertAlmostEqual(result["changed_pct"], 0.01, delta=1e-6)
        self.assertEqual(result["changed_pixels"], 200)
        self.assertEqual(result["regions"], [[8, 8, 24, 16]])  # 8px tile-aligned bbox around (10,10,20,10)
        self.assertEqual(result["tiles"]["changed"], 6)
        same = visual.diff_images(base, base)
        self.assertEqual((same["status"], same["changed_pct"], same["regions"]), ("same", 0.0, []))

    def test_tolerance_and_masks(self):
        base = _img(kit.make_png(100, 100, (100, 100, 100)))
        faint = _img(kit.make_png(100, 100, (100, 100, 100), blocks=[[0, 0, 50, 50, (103, 100, 100)]]))
        self.assertEqual(visual.diff_images(base, faint, tolerance=3)["status"], "same")
        self.assertEqual(visual.diff_images(base, faint, tolerance=2)["changed_pixels"], 2500)
        planted = _img(kit.make_png(100, 100, blocks=[[0, 0, 50, 50, (0, 0, 0)]]))
        masked = visual.diff_images(_img(kit.make_png(100, 100)), planted, masks=[[0, 0, 50, 50]])
        self.assertEqual((masked["changed_pixels"], masked["masked_ratio"]), (0, 0.25))
        over = visual.diff_images(base, base, masks=[[0, 0, 80, 80]])
        self.assertGreater(over["masked_ratio"], 0.2)  # 64% > default cap 0.2

    def test_dimensions_never_resized(self):
        result = visual.diff_images(_img(kit.make_png(10, 10)), _img(kit.make_png(12, 10)))
        self.assertEqual((result["status"], result["changed_pct"], result["dimensions"]), ("dimensions", 1.0, [[10, 10], [12, 10]]))

    def test_unreadable_png_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.png")
            with open(path, "wb") as fh:
                fh.write(kit.make_png(8, 8)[:30])
            with self.assertRaises(ValueError):
                visual.Image.load(path)
            self.assertEqual(_quiet_main(["diff", path, path]), 1)

    def test_heatmap_and_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = os.path.join(tmp, "a.png"), os.path.join(tmp, "b.png")
            with open(a, "wb") as fh:
                fh.write(kit.make_png(16, 16))
            with open(b, "wb") as fh:
                fh.write(kit.make_png(16, 16, blocks=[[0, 0, 8, 8, (0, 0, 0)]]))
            heat = os.path.join(tmp, "heat.png")
            self.assertEqual(_quiet_main(["diff", a, b, "--heatmap", heat, "--mask", "8,8,4,4"]), 1)
            w, h, ch, rows = tc.decode_png(open(heat, "rb").read())
            self.assertEqual((w, h, ch), (16, 16, 3))
            self.assertEqual(rows[0][:3], bytes((255, 0, 0)))   # hot tile
            self.assertEqual(rows[15][-3:], bytes((0, 0, 0)))   # cold tile
            self.assertEqual(_quiet_main(["diff", a, a]), 0)


class ManifestTestCase(unittest.TestCase):
    def _goldens(self, tmp, manifest):
        golden = os.path.join(tmp, "board.png")
        with open(golden, "wb") as fh:
            fh.write(kit.make_png(4, 4))
        if manifest is not None:
            kit.make_repo(tmp, {"manifest.json": manifest})
        return golden

    def test_manifest_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            golden = self._goldens(tmp, None)
            self.assertEqual(visual.check_manifest(tmp)["unreviewed"], ["board.png"])
            sha = tc.sha256_file(golden)
            good = tc.dump_json({"machine": "x", "entries": {"board.png": {"sha256": sha, "reason": "initial bless"}}})
            kit.make_repo(tmp, {"manifest.json": good})
            self.assertTrue(visual.check_manifest(tmp)["ok"])
            reasonless = tc.dump_json({"entries": {"board.png": {"sha256": sha, "reason": " "}}})
            kit.make_repo(tmp, {"manifest.json": reasonless})
            self.assertEqual(visual.check_manifest(tmp)["reasonless"], ["board.png"])
            swapped = tc.dump_json({"entries": {"board.png": {"sha256": "0" * 64, "reason": "x"}, "gone.png": {"sha256": "1", "reason": "y"}}})
            kit.make_repo(tmp, {"manifest.json": swapped})
            result = visual.check_manifest(tmp)
            self.assertEqual((result["unreviewed"], result["dangling"], result["ok"]), (["board.png"], ["gone.png"], False))
            kit.make_repo(tmp, {"manifest.json": "{not json"})
            self.assertEqual(visual.check_manifest(tmp)["error"], "manifest.json unreadable")
            self.assertEqual(_quiet_main(["manifest", tmp]), 1)
        self.assertEqual(visual.check_manifest("/nonexistent/dir")["count"], 0)

    def test_machine_key(self):
        self.assertEqual(visual.machine_key("darwin", "Chromium 131", 2), "darwin-chromium131-dpr2")


class CompareShotTestCase(unittest.TestCase):
    def test_threshold_boundary_and_mask_cap(self):
        """visual.compare_shot: `changed_pct > max_changed_pct` — 正好等于阈值放行。"""
        with tempfile.TemporaryDirectory() as tmp:
            shot, golden = os.path.join(tmp, "s.png"), os.path.join(tmp, "g.png")
            with open(golden, "wb") as fh:
                fh.write(kit.make_png(200, 100))
            with open(shot, "wb") as fh:
                fh.write(kit.make_png(200, 100, blocks=[[0, 0, 20, 10, (0, 0, 0)]]))  # 1%
            strict = visual.compare_shot(shot, golden, {"max_changed_pct": 0.0})
            self.assertEqual((strict["item_status"], strict["tool"], strict["over_mask_cap"]), ("CHANGED", "internal", False))
            self.assertEqual(visual.compare_shot(shot, golden, {"max_changed_pct": 0.01})["item_status"], "PRESENT")
            capped = visual.compare_shot(shot, golden, {"max_changed_pct": 0.5, "max_mask_ratio": 0.1}, masks=[[0, 0, 100, 100]])
            self.assertTrue(capped["over_mask_cap"])
            # exactly at the cap is not over it (masked 40×100 of 200×100 = 0.2 vs max_mask_ratio 0.2)
            at_cap = visual.compare_shot(shot, golden, {"max_changed_pct": 0.5, "max_mask_ratio": 0.2}, masks=[[0, 0, 40, 100]])
            self.assertEqual((at_cap["masked_ratio"], at_cap["over_mask_cap"]), (0.2, False))
            # a mask covers [x, x+w): the changed pixel column right at the mask's right edge still counts
            edge = visual.compare_shot(shot, golden, {"max_changed_pct": 0.0}, masks=[[0, 0, 19, 10]])
            self.assertEqual(edge["changed_pixels"], 10)
            self.assertEqual(visual.compare_shot(shot, golden, {"max_changed_pct": 0.0}, masks=[[0, 0, 20, 10]])["changed_pixels"], 0)

    def test_odiff_parsing(self):
        same = kit.FakeRunner(default=(0, "", ""))
        self.assertEqual(visual.diff_with_odiff(same, "a", "b", "o")["status"], "same")
        dims = kit.FakeRunner(default=(22, "", ""))
        self.assertEqual(visual.diff_with_odiff(dims, "a", "b", "o")["status"], "dimensions")
        changed = kit.FakeRunner(default=(21, "Different pixels: 200 (1.000000%)", ""))
        result = visual.diff_with_odiff(changed, "a", "b", "o")
        self.assertEqual((result["changed_pixels"], result["changed_pct"], result["tool"]), (200, 0.01, "odiff"))
        garbage = kit.FakeRunner(default=(21, "???", ""))
        self.assertIsNone(visual.diff_with_odiff(garbage, "a", "b", "o"))
        with tempfile.TemporaryDirectory() as tmp:
            shot = os.path.join(tmp, "s.png")
            with open(shot, "wb") as fh:
                fh.write(kit.make_png(4, 4))
            result = visual.compare_shot(shot, shot, {"max_changed_pct": 0.0}, runner=garbage, out_dir=tmp, tools={"odiff": "/bin/odiff"})
            self.assertEqual(result["tool"], "internal")  # unparseable odiff → internal instrument, recorded


if __name__ == "__main__":
    unittest.main()

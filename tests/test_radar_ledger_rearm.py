"""§47.5 复活闸 — 一条「等 iCloud 等到放弃」的案底重新上膛一次，且只一次.

`_is_due` 对「``gave_up`` 且 mtime 未变」的条目**永久**跳过：2026-08 那三篇
screenpipe note 烧完额度之后，即使 iCloud 早把文件放回来了，也再没有任何一轮
cron 会去读它们——§40 诊断卡看着像「留痕」，实际是静默丢失穿了件外套（§0 宪法
第 11 条）。本文件钉住这道出口的四条边界：

- 认领范围：只认「还没在本机」的读取失败，毒 note / 毒邮件 / 提取失败不碰；
- 只复活一次（add-only 字段 ``rearmed``）——真的永远拉不回来的 note 第二次
  烧完额度就老实留在案底；
- 只复活**本轮读得到**的 key：换根（vault-mirror）之后的遗留案底翻成
  ``gave_up=False`` 却永不重读 = 抹掉留痕，比不复活更糟；
- 换根遗留的案底按同名 note 搬到当前根上，搬完真的被重读、销案。

读取路径（dataless 探针 + brctl 催下载）的判例在
tests/test_radar_dataless_note.py。
"""
import json
import os
import shutil
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports
from tests.test_radar import BASE, RadarScanBase

from act import radar
from act.lib import config

LEGACY = ("unreadable note 2026-08-18-screenpipe-0019.md: "
          "[Errno 11] Resource deadlock avoided")


class RearmGateTestCase(unittest.TestCase):
    """闸门本身（不跑整轮 pass）。"""

    KEY = "/vault/2 - raw/2026-08-18-screenpipe-0019.md"

    def _rearm(self, ledger, md_files=None):
        summary = {"skipped": []}
        if md_files is None:
            md_files = [(Path(k), BASE) for k in ledger]
        return radar._rearm_deferred_giveups(ledger, summary, md_files), summary

    def _stuck(self, **over):
        entry = {"mtime": BASE, "attempts": 5, "last_error": LEGACY,
                 "gave_up": True}
        entry.update(over)
        return {self.KEY: entry}

    def test_a_waiting_give_up_gets_one_more_full_budget(self):
        ledger = self._stuck()
        rearmed, summary = self._rearm(ledger)
        self.assertEqual(rearmed, [self.KEY])
        entry = ledger[self.KEY]
        self.assertEqual(entry["attempts"], 0)
        self.assertFalse(entry["gave_up"])
        self.assertTrue(entry["rearmed"])
        self.assertTrue(any("re-armed" in s for s in summary["skipped"]))
        # 复活后 _is_due 又肯看它了（钉住这条卡死链的另一半）
        self.assertTrue(radar._is_due(entry, BASE, marker=BASE + 10))

    def test_it_only_ever_re_arms_once(self):
        ledger = self._stuck()
        self._rearm(ledger)
        ledger[self.KEY].update(attempts=radar.FAILED_MAX_ATTEMPTS_DEFERRED,
                                gave_up=True)          # 又烧完一遍
        rearmed, summary = self._rearm(ledger)
        self.assertEqual(rearmed, [])
        self.assertTrue(ledger[self.KEY]["gave_up"])   # 留痕，不再复活
        self.assertEqual(summary["skipped"], [])
        self.assertFalse(radar._is_due(ledger[self.KEY], BASE, marker=BASE + 10))

    def test_other_ledger_entries_are_left_alone(self):
        ledger = {
            "gmail:uid:7": {"mtime": 7.0, "attempts": 4, "gave_up": True,
                            "last_error": "poison message (unparseable headers)"},
            "/vault/poison.md": {"mtime": BASE, "attempts": 5, "gave_up": True,
                                 "last_error": "unparseable extraction on poison.md"},
            "/vault/live.md": {"mtime": BASE, "attempts": 2,
                               "last_error": LEGACY, "gave_up": False},
        }
        before = json.loads(json.dumps(ledger))
        rearmed, _ = self._rearm(ledger)
        self.assertEqual(rearmed, [])
        self.assertEqual(ledger, before)

    def test_only_note_read_failures_are_claimed(self):
        # 提取/落库失败各有自己的额度语义，字面撞上 errno 文本也不放行
        self.assertFalse(radar._is_deferred_error(
            "claude -p failed on a.md: OSError: [Errno 60] Operation timed out"))
        self.assertFalse(radar._is_deferred_error(
            "claude -p failed on a.md: [Errno 11] Resource deadlock avoided"))
        self.assertFalse(radar._is_deferred_error(
            "unreadable note a.md: [Errno 13] Permission denied"))
        self.assertFalse(radar._is_deferred_error(None))
        self.assertTrue(radar._is_deferred_error(LEGACY))           # 历史案底
        self.assertTrue(radar._is_deferred_error(                    # 新案底
            f"{radar.DEFERRED_PREFIX}: a.md: still dataless"))

    def test_an_entry_this_pass_cannot_read_is_never_re_armed(self):
        """本轮 md_files 里没有的 key（换根遗留、同名 note 也不在）：复活它
        等于抹掉 gave_up 留痕却永不重读——loop_inputs 只看 gave_up。"""
        ledger = self._stuck()
        rearmed, summary = self._rearm(ledger, md_files=[])
        self.assertEqual(rearmed, [])
        self.assertTrue(ledger[self.KEY]["gave_up"])
        self.assertNotIn("rearmed", ledger[self.KEY])
        self.assertTrue(any("point outside the current vault root" in s
                            for s in summary["skipped"]))

    def test_a_stale_root_entry_is_re_keyed_onto_the_note_this_pass_sees(self):
        ledger = self._stuck()
        mirrored = Path("/state/vault-mirror/2 - raw/"
                        "2026-08-18-screenpipe-0019.md")
        rearmed, summary = self._rearm(ledger, md_files=[(mirrored, BASE)])
        self.assertEqual(rearmed, [str(mirrored)])
        self.assertNotIn(self.KEY, ledger)                  # 搬走了，不是复制
        self.assertFalse(ledger[str(mirrored)]["gave_up"])
        self.assertTrue(any("re-keyed" in s for s in summary["skipped"]))

    def test_re_keying_never_clobbers_a_live_entry(self):
        mirrored = Path("/state/vault-mirror/2 - raw/"
                        "2026-08-18-screenpipe-0019.md")
        ledger = self._stuck()
        ledger[str(mirrored)] = {"mtime": BASE, "attempts": 1,
                                 "last_error": "unparseable extraction",
                                 "gave_up": False}
        rearmed, _ = self._rearm(ledger, md_files=[(mirrored, BASE)])
        self.assertEqual(rearmed, [])
        self.assertEqual(ledger[str(mirrored)]["attempts"], 1)   # 没被覆盖
        self.assertTrue(ledger[self.KEY]["gave_up"])             # 老案底留痕


class RearmPassTestCase(RadarScanBase):
    """整轮 pass 视角：卡死的案底真的被重排、读到、销案。"""

    def test_a_stuck_give_up_is_re_armed_and_then_processed(self):
        cloudy = self._note("cloudy.md", "ask: ship the thing", BASE)
        radar._save_failed_queue({str(cloudy): {
            "mtime": BASE, "attempts": radar.FAILED_MAX_ATTEMPTS,
            "gave_up": True, "last_error": ("unreadable note cloudy.md: "
                                            "[Errno 11] Resource deadlock avoided")}})
        radar._write_marker(BASE + 100)            # marker 早已越过它

        summary = radar.scan(runner=lambda t: "[]")

        self.assertEqual(summary["files_scanned"], 1)   # 旧代码：0（永久跳过）
        self.assertEqual(radar._load_failed_queue(), {})

    def test_a_stale_root_case_is_re_keyed_and_cleared_under_mirror_mode(self):
        """live 形状：案底的 key 是真 vault 路径，而本轮的 effective root 是
        state/vault-mirror/2 - raw（claude 的 TCC 隔离）。"""
        real = self._note("cloudy.md", "ask: ship the thing", BASE)
        mirror = config.STATE_DIR / "vault-mirror" / "2 - raw"
        mirror.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, config.STATE_DIR / "vault-mirror",
                        ignore_errors=True)
        mode = config.STATE_DIR / "vault_sync_mode"
        mode.write_text("mirror", encoding="utf-8")
        self.addCleanup(mode.unlink)
        mirrored = mirror / "cloudy.md"
        mirrored.write_text("ask: ship the thing", encoding="utf-8")
        os.utime(mirrored, (BASE, BASE))
        radar._save_failed_queue({str(real): {
            "mtime": BASE, "attempts": radar.FAILED_MAX_ATTEMPTS,
            "gave_up": True, "last_error": ("unreadable note cloudy.md: "
                                            "[Errno 11] Resource deadlock avoided")}})
        radar._write_marker(BASE + 100)
        self.assertEqual(config.effective_obsidian_raw(config.load_config()), mirror)

        summary = radar.scan(runner=lambda t: "[]")

        self.assertEqual(summary["files_scanned"], 1)
        self.assertEqual(radar._load_failed_queue(), {})   # 销案，留痕不再复活
        self.assertTrue(real.exists())                     # 真 vault 没被碰


if __name__ == "__main__":
    unittest.main()

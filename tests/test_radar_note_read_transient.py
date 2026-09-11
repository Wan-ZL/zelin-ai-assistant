"""act/radar — 环境类不可读的 note 不许被判死（§47.5，宪法第 11 条）。

事故：三篇 screenpipe note 连续 5 轮 `[Errno 11] Resource deadlock avoided`
（EDEADLK，iCloud 把文件 evict 成 dataless）→ `gave_up=True`，而 `_is_due` 对
「gave_up 且 mtime 未变」的条目**永久**跳过：文件几分钟后读得动了也再没人读。

本文件钉住四件事：
- 分类：errno 属环境类 vs. 这篇 note 本身坏了（非 UTF-8 语义不变）；
- 同 pass 退避重读一次就成功（第一次 open 常常就把下载踢起来了）；
- 重试耗尽 → 进台账、错误串带机器标记、按放宽额度算 gave_up；
- 复活闸：环境类 gave_up 案底重新上膛**一次**（add-only `rearmed`），
  真的永久读不了的 note 第二次照样留痕。
"""
import errno
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import radar
from act.lib import config

BASE = 1_760_000_000.0


class TransientReadClassificationTestCase(unittest.TestCase):
    def test_environmental_errnos_are_claimed_note_level_ones_are_not(self):
        self.assertTrue(radar._is_transient_read_error(
            "unreadable note a.md: transient read error (EDEADLK): [Errno 11] x"))
        # 历史案底（本标记出生前写的）按 strerror 文本认领
        self.assertTrue(radar._is_transient_read_error(
            "unreadable note a.md: [Errno 11] Resource deadlock avoided"))
        self.assertFalse(radar._is_transient_read_error(
            "unreadable note a.md: 'utf-8' codec can't decode byte 0xff"))
        self.assertFalse(radar._is_transient_read_error(
            "unreadable note a.md: [Errno 13] Permission denied"))
        self.assertFalse(radar._is_transient_read_error(None))
        # 只认领读取失败：提取/落库失败各有自己的额度语义，哪怕字面撞上
        self.assertFalse(radar._is_transient_read_error(
            "claude -p failed on a.md: OSError: [Errno 60] Operation timed out"))

    def test_the_errno_name_does_not_drift_across_platforms(self):
        """errno.errorcode 对同值别名给最后注册的名字：Linux 上 EDEADLK(35) 会
        报成 'EDEADLOCK'、EAGAIN(11) 报成 'EWOULDBLOCK'。机器标记是跨机器读的
        台账字段，必须逐字稳定（§47.5）。"""
        self.assertEqual(radar._errno_name(errno.EDEADLK), "EDEADLK")
        self.assertEqual(radar._errno_name(errno.EAGAIN), "EAGAIN")
        self.assertEqual(radar._errno_name(errno.EWOULDBLOCK), "EAGAIN")
        # 表外的 errno 仍按 errorcode 兜底，没有号就回字面数字
        self.assertEqual(radar._errno_name(errno.EACCES), "EACCES")
        self.assertEqual(radar._errno_name(None), "None")

    def test_the_attempt_cap_is_per_class(self):
        self.assertEqual(
            radar._max_attempts_for("unreadable note a.md: [Errno 11] "
                                    "Resource deadlock avoided"),
            radar.FAILED_MAX_ATTEMPTS_TRANSIENT_READ)
        self.assertEqual(radar._max_attempts_for("unparseable extraction on a.md"),
                         radar.FAILED_MAX_ATTEMPTS)
        self.assertGreater(radar.FAILED_MAX_ATTEMPTS_TRANSIENT_READ,
                           radar.FAILED_MAX_ATTEMPTS)


class ReadNoteTextTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="radar-read-")
        self.addCleanup(self.tmp.cleanup)
        self.note = Path(self.tmp.name) / "n.md"
        self.note.write_text("fine", encoding="utf-8")
        self._backoff = radar.NOTE_READ_BACKOFF_S
        radar.NOTE_READ_BACKOFF_S = 0
        self.addCleanup(setattr, radar, "NOTE_READ_BACKOFF_S", self._backoff)

    @staticmethod
    def _flaky(fails: int, code: int = errno.EDEADLK):
        """前 ``fails`` 次抛环境类 OSError，之后正常读。"""
        real = Path.read_text
        state = {"n": 0}

        def read_text(self, *a, **kw):
            state["n"] += 1
            if state["n"] <= fails:
                raise OSError(code, os.strerror(code))
            return real(self, *a, **kw)

        return read_text, state

    def test_one_bad_open_then_success_inside_the_same_pass(self):
        read_text, state = self._flaky(1)
        with mock.patch.object(Path, "read_text", read_text):
            text, error = radar._read_note_text(self.note)
        self.assertEqual((text, error), ("fine", None))
        self.assertEqual(state["n"], 2)          # 重读了一次，没进台账

    def test_retries_exhausted_returns_a_marked_error(self):
        read_text, state = self._flaky(99)
        with mock.patch.object(Path, "read_text", read_text):
            text, error = radar._read_note_text(self.note)
        self.assertIsNone(text)
        self.assertEqual(state["n"], radar.NOTE_READ_MAX_RETRIES + 1)
        self.assertTrue(error.startswith("unreadable note n.md:"))
        self.assertIn(radar.TRANSIENT_READ_MARK, error)
        self.assertIn("EDEADLK", error)
        self.assertTrue(radar._is_transient_read_error(error))
        self.assertTrue(radar._is_note_level_error(error))   # 不许把整轮判 systemic

    def test_a_note_level_oserror_is_not_retried(self):
        read_text, state = self._flaky(99, code=errno.EACCES)
        with mock.patch.object(Path, "read_text", read_text):
            text, error = radar._read_note_text(self.note)
        self.assertIsNone(text)
        self.assertEqual(state["n"], 1)          # 权限问题重读一万次也一样
        self.assertFalse(radar._is_transient_read_error(error))

    def test_non_utf8_semantics_unchanged(self):
        self.note.write_bytes(b"\xff\xfe not utf8")
        text, error = radar._read_note_text(self.note)
        self.assertIsNone(text)
        self.assertIn("unreadable note n.md:", error)
        self.assertFalse(radar._is_transient_read_error(error))


class RearmGiveUpsTestCase(unittest.TestCase):
    KEY = "/vault/2 - raw/2026-08-18-screenpipe-0019.md"
    LEGACY = ("unreadable note 2026-08-18-screenpipe-0019.md: "
              "[Errno 11] Resource deadlock avoided")

    def _rearm(self, ledger):
        summary = {"skipped": []}
        return radar._rearm_transient_read_giveups(ledger, summary), summary

    def test_environmental_give_up_gets_one_more_full_budget(self):
        ledger = {self.KEY: {"mtime": BASE, "attempts": 5,
                             "last_error": self.LEGACY, "gave_up": True}}
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
        ledger = {self.KEY: {"mtime": BASE, "attempts": 5,
                             "last_error": self.LEGACY, "gave_up": True}}
        self._rearm(ledger)
        ledger[self.KEY].update(attempts=20, gave_up=True)   # 又烧完一遍
        rearmed, summary = self._rearm(ledger)
        self.assertEqual(rearmed, [])
        self.assertTrue(ledger[self.KEY]["gave_up"])         # 留痕，不再复活
        self.assertEqual(summary["skipped"], [])
        self.assertFalse(radar._is_due(ledger[self.KEY], BASE, marker=BASE + 10))

    def test_other_ledger_entries_are_left_alone(self):
        ledger = {
            "gmail:uid:7": {"mtime": 7.0, "attempts": 4, "gave_up": True,
                            "last_error": "poison message (unparseable headers)"},
            "/vault/poison.md": {"mtime": BASE, "attempts": 5, "gave_up": True,
                                 "last_error": "unparseable extraction on poison.md"},
            "/vault/live.md": {"mtime": BASE, "attempts": 2,
                               "last_error": self.LEGACY, "gave_up": False},
        }
        before = json.loads(json.dumps(ledger))
        rearmed, _ = self._rearm(ledger)
        self.assertEqual(rearmed, [])
        self.assertEqual(ledger, before)


class TransientReadPassTestCase(unittest.TestCase):
    """整轮 pass 视角：一篇环境类不可读的 note 不烧 5 次额度就判死。"""

    def setUp(self):
        config.ensure_state_dirs()
        self._cleanup()
        self.tmp = tempfile.TemporaryDirectory(prefix="radar-vault-")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._cleanup)
        self.raw = Path(self.tmp.name) / "2 - raw"
        self.raw.mkdir(parents=True)
        config.CONFIG_PATH.write_text(
            f'sources:\n  obsidian_raw: "{self.raw.as_posix()}"\n', encoding="utf-8")
        self._backoff = radar.NOTE_READ_BACKOFF_S
        radar.NOTE_READ_BACKOFF_S = 0
        self.addCleanup(setattr, radar, "NOTE_READ_BACKOFF_S", self._backoff)

    @staticmethod
    def _cleanup():
        if config.CONFIG_PATH.exists():
            config.CONFIG_PATH.unlink()
        for p in (config.STATE_DIR / radar.MARKER_PATH_NAME,
                  config.STATE_DIR / radar.FAILED_QUEUE_NAME):
            if p.exists():
                p.unlink()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()

    def _note(self, name, text, mtime):
        p = self.raw / name
        p.write_text(text, encoding="utf-8")
        os.utime(p, (mtime, mtime))
        return p

    def _dataless(self, name):
        """``name`` 那篇永远 EDEADLK，其余照常读。"""
        code = errno.EDEADLK
        real = Path.read_text

        def read_text(this, *a, **kw):
            if this.name == name:
                raise OSError(code, os.strerror(code))
            return real(this, *a, **kw)

        return read_text

    def test_five_dataless_rounds_no_longer_give_up_and_a_readable_round_clears_it(self):
        cloudy = self._note("cloudy.md", "ask: ship the thing", BASE)
        self._note("good.md", "fine", BASE + 10)   # 部分失败 -> 不判 systemic
        with mock.patch.object(Path, "read_text", self._dataless("cloudy.md")):
            for _ in range(radar.FAILED_MAX_ATTEMPTS):
                radar.scan(runner=lambda t: "[]")
        entry = radar._load_failed_queue()[str(cloudy)]
        self.assertEqual(entry["attempts"], radar.FAILED_MAX_ATTEMPTS)
        self.assertFalse(entry["gave_up"])         # 旧代码在这里判死
        self.assertIn(radar.TRANSIENT_READ_MARK, entry["last_error"])
        # 没判死 = 没铸 §40 诊断卡
        self.assertFalse(radar._has_source_ref(radar.GIVE_UP_CHANNEL, str(cloudy)))

        # 云端把文件放回来了：下一轮照常读，案底销号
        radar.scan(runner=lambda t: "[]")
        self.assertNotIn(str(cloudy), radar._load_failed_queue())

    def test_a_stuck_give_up_is_re_armed_and_then_processed(self):
        cloudy = self._note("cloudy.md", "ask: ship the thing", BASE)
        radar._save_failed_queue({str(cloudy): {
            "mtime": BASE, "attempts": 5, "gave_up": True,
            "last_error": ("unreadable note cloudy.md: [Errno 11] "
                           "Resource deadlock avoided")}})
        radar._write_marker(BASE + 100)            # marker 早已越过它
        summary = radar.scan(runner=lambda t: "[]")
        self.assertEqual(summary["files_scanned"], 1)   # 旧代码：0（永久跳过）
        self.assertNotIn(str(cloudy), radar._load_failed_queue())


if __name__ == "__main__":
    unittest.main()

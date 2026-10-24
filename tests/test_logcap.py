"""act/lib/logcap.py — append-only 日志的自压缩（1MB 模式，防腐 #4）。

live 事故（2026-08）：v1 的 syncd.log 涨到 74MB——进程内 _log() 逐行 append、从不轮转。
v2.0.0 的唯一调用方是 act/recap.py 的 state/recap.log。钉住的契约：

- 不超限的文件一字不动（幂等，无谓 rewrite 会翻 mtime）；
- 超限后只保留最近半数行（尾部 = 最新），atomic tmp+replace；
- 文件不存在 / IO 失败绝不 raise（日志护理绝不反噬调用方）；
- recap._log 的接线真的触发压缩。

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import recap
from act.lib import config, logcap, recap_store


def _big_log(path, n_lines=12_000, width=100):
    """写一个 > 1MB（且半数行 < 1MB，一次压缩即达标）的行日志：行号打头，
    方便断言留下的是最新的行。"""
    lines = [f"line-{i:06d} " + "x" * width for i in range(n_lines)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


class CapTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        self.path = config.STATE_DIR / "logcap-test.log"
        self.addCleanup(lambda: self.path.unlink(missing_ok=True))

    def test_under_the_cap_is_left_untouched(self):
        self.path.write_text("a\nb\nc\n", encoding="utf-8")
        logcap.cap(self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "a\nb\nc\n")

    def test_over_the_cap_keeps_the_newest_half(self):
        lines = _big_log(self.path)
        self.assertGreater(self.path.stat().st_size, logcap.MAX_BYTES)
        logcap.cap(self.path)
        kept = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(kept, lines[len(lines) // 2:])   # 尾部 = 最新，保留
        self.assertLess(self.path.stat().st_size, logcap.MAX_BYTES)
        # tmp 中间文件不残留
        self.assertFalse(self.path.with_suffix(".log.tmp").exists())

    def test_missing_file_never_raises(self):
        logcap.cap(config.STATE_DIR / "no-such.log")      # no raise

    def test_bad_bytes_never_raise(self):
        # 日志内容可能来自任意外部文本——坏字节只被替换，绝不崩
        self.path.write_bytes(b"\xff\xfe bad bytes\n" * 100_000)
        logcap.cap(self.path)
        self.assertLess(self.path.stat().st_size, logcap.MAX_BYTES + 1)

    def test_single_giant_line_byte_truncated_tail_kept(self):
        # PR #106 终审 MINOR-10：单行 > cap 时行减半缩不动（旧实现 = 永远
        # no-op，超限文件每轮被原样重写）；退化为按字节保尾 + 截断标记行。
        giant = "x" * (2 * logcap.MAX_BYTES) + "TAIL-SENTINEL"
        self.path.write_text(giant + "\n", encoding="utf-8")
        logcap.cap(self.path)
        self.assertLessEqual(self.path.stat().st_size, logcap.MAX_BYTES)
        out = self.path.read_text(encoding="utf-8")
        self.assertTrue(out.startswith(logcap._TRUNCATION_MARKER))
        self.assertIn("TAIL-SENTINEL", out)               # 尾部（最新）保留


class RecapWiringTestCase(unittest.TestCase):
    """recap._log append 后自压缩真的触发（接线判例；v1 的 actd / syncd 接线随 Act 退役）。"""

    def test_recap_log_self_compacts(self):
        path = recap_store.log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        _big_log(path)
        self.assertGreater(path.stat().st_size, logcap.MAX_BYTES)
        recap._log("one more line after the cap")
        self.assertLess(path.stat().st_size, logcap.MAX_BYTES)
        tail = path.read_text(encoding="utf-8").splitlines()[-1]
        self.assertIn("one more line after the cap", tail)  # 新行幸存在尾部

if __name__ == "__main__":
    unittest.main()

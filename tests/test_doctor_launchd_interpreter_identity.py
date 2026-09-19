"""doctor `launchd interpreter identity` 行（CONTRACT §55 追记 2026-09-19；issue #423）。

live 2026-09-18：server 进程（launchd 起的 `/usr/bin/python3 -m server`）13 小时读不到
外置卷上的 repo，`GET /api/board` 连续 404；tccd 日志里那段时间每次查「完全磁盘访问」
的 subject 都是 `/usr/bin/git`（authValue=0），表里只有 `/usr/bin/python3` 那行。两者是
同一个 Apple xcode-select 工具 shim 的签名身份（`com.apple.dt.xcode_select.tool-shim-public`），
TCC 按「最近见到的路径」查表。这一行只把这个形状说出来（WARN + 修法），永不 FAIL。
全部经 Probes 注入（plist 原文、run）——绝不读开发者的 ~/Library/LaunchAgents，绝不起
真 codesign。
"""
import sys
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401

from act import doctor
from act.lib import fresh_install
from act.lib.checks import launchd
from server import permissions

_WIN = sys.platform.startswith("win")

SHIM = "/usr/bin/python3"
REAL = ("/Applications/Xcode.app/Contents/Developer/Library/Frameworks/"
        "Python3.framework/Versions/3.9/bin/python3.9")
LABELS = ["com.zelin.aiassistant.actd", "com.zelin.aiassistant.server",
          "com.zelin.aiassistant.syncd"]

CODESIGN_SHIM = ("Executable=/usr/bin/python3\n"
                 "Identifier=com.apple.dt.xcode_select.tool-shim-public\n"
                 "Format=Mach-O universal (x86_64 arm64e)\n")
CODESIGN_REAL = ("Executable=%s\nIdentifier=com.apple.python3\n"
                 "TeamIdentifier=59GAB85EFG\n" % REAL)


def _plist(interp):
    return ("<plist><dict><key>Label</key><string>x</string>"
            "<key>ProgramArguments</key><array><string>%s</string><string>-m</string>"
            "<string>act.actd</string></array></dict></plist>" % interp)


class FakeRun:
    """probes.run：按程序名答题，记下每次调用（判例断言子进程只经这一道缝）。"""

    def __init__(self, codesign=None, real=(0, REAL + "\n")):
        self.codesign = codesign or {}
        self.real = real
        self.calls = []

    def __call__(self, cmd, env=None, timeout=None):
        self.calls.append(list(cmd))
        if cmd[0] == "codesign":
            return self.codesign.get(cmd[-1], (1, "%s: No such file or directory" % cmd[-1]))
        if len(cmd) >= 3 and cmd[1] == "-c":
            return self.real
        return (0, "")


@unittest.skipIf(_WIN, "launchd rows model a macOS install")
class InterpreterIdentityRowTestCase(unittest.TestCase):
    def setUp(self):
        # 沙箱 HOME 住在 /var/folders 或 /tmp 下——本身就是「repo 在 $HOME 之外」
        # 的形状；「之内」的分支显式打桩。
        p = mock.patch.object(fresh_install, "repo_outside_home", return_value=True)
        p.start()
        self.addCleanup(p.stop)

    def _probes(self, plists, run):
        return doctor.Probes(
            run=run,
            launchd_labels=LABELS,
            installed_plist_text=lambda label: plists.get(label),
        )

    def _rows(self, plists, run):
        res = doctor._check_launchd_interpreter_identity(self._probes(plists, run))
        return res if isinstance(res, list) else [res]

    # -- no row ------------------------------------------------------------- #

    def test_nothing_installed_means_no_row(self):
        run = FakeRun(codesign={SHIM: (0, CODESIGN_SHIM)})
        self.assertEqual(self._rows({}, run), [])
        self.assertEqual(run.calls, [])

    def test_repo_inside_home_is_not_tcc_gated_so_no_row(self):
        with mock.patch.object(fresh_install, "repo_outside_home", return_value=False):
            run = FakeRun(codesign={SHIM: (0, CODESIGN_SHIM)})
            self.assertEqual(self._rows({LABELS[0]: _plist(SHIM)}, run), [])
        self.assertEqual(run.calls, [])

    def test_unreadable_identity_is_not_guessed(self):
        # codesign 读不出（非 darwin 的假 runner / 未签名）→ 不出行，不猜
        run = FakeRun(codesign={})
        self.assertEqual(self._rows({LABELS[0]: _plist("/opt/x/python3")}, run), [])

    # -- the shim ----------------------------------------------------------- #

    def test_shim_interpreter_warns_and_names_agents_identity_and_git(self):
        run = FakeRun(codesign={SHIM: (0, CODESIGN_SHIM)})
        plists = {LABELS[0]: _plist(SHIM), LABELS[1]: _plist(SHIM)}
        (r,) = self._rows(plists, run)
        self.assertEqual(r.name, "launchd interpreter identity")
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn(SHIM, r.detail)
        self.assertIn("actd, server", r.detail)
        self.assertNotIn("syncd", r.detail)          # 没装的 agent 不点名
        self.assertIn(launchd.SHIM_IDENTIFIER, r.detail)
        self.assertIn("/usr/bin/git", r.detail)
        self.assertIn("2026-09-18", r.detail)        # 说清这是哪次事故的形状
        # 修法：钉本体 + 给本体授 FDA，权宜 = 顺手给 git 也授
        self.assertIn("AIASSISTANT_PYTHON=%s bash install.sh" % REAL, r.fix)
        self.assertIn("Full Disk Access", r.fix)
        self.assertIn("/usr/bin/git", r.fix)
        # 无 §25 id（§55 2026-09-14 追记的先例：新 id 要同 PR 改 Swift 镜像表）
        self.assertEqual(r.failure_id, "")
        self.assertEqual(r.row_class, "")

    def test_never_fail(self):
        run = FakeRun(codesign={SHIM: (0, CODESIGN_SHIM)})
        (r,) = self._rows({LABELS[0]: _plist(SHIM)}, run)
        self.assertNotEqual(r.status, doctor.FAIL)

    def test_hard_link_count_is_named_when_the_shim_has_many_names(self):
        # /usr/bin/python3 与 /usr/bin/git 是同一个 inode（ls -li：78 个名字）——
        # 这就是「按路径授权」为什么盖不住它；stat 读得到就说出来
        run = FakeRun(codesign={SHIM: (0, CODESIGN_SHIM)})
        with mock.patch.object(launchd, "_link_count", return_value=78):
            (r,) = self._rows({LABELS[0]: _plist(SHIM)}, run)
        self.assertIn("78", r.detail)
        self.assertIn("inode", r.detail)

    def test_unknown_or_single_link_count_stays_quiet(self):
        run = FakeRun(codesign={SHIM: (0, CODESIGN_SHIM)})
        for count in (None, 1):
            with mock.patch.object(launchd, "_link_count", return_value=count):
                (r,) = self._rows({LABELS[0]: _plist(SHIM)}, run)
            self.assertEqual(r.status, doctor.WARN)
            self.assertNotIn("inode", r.detail)

    def test_link_count_helper_never_raises(self):
        self.assertIsNone(launchd._link_count("/nonexistent/binary"))

    def test_real_interpreter_unresolvable_falls_back_to_the_resolution_command(self):
        run = FakeRun(codesign={SHIM: (0, CODESIGN_SHIM)}, real=(1, "boom"))
        (r,) = self._rows({LABELS[0]: _plist(SHIM)}, run)
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn("AIASSISTANT_PYTHON=$(%s -c" % SHIM, r.fix)
        self.assertIn("realpath(sys.executable)", r.fix)

    def test_codesign_runs_once_per_distinct_interpreter_and_only_via_probes_run(self):
        run = FakeRun(codesign={SHIM: (0, CODESIGN_SHIM)})
        plists = {label: _plist(SHIM) for label in LABELS}
        self._rows(plists, run)
        codesigns = [c for c in run.calls if c[0] == "codesign"]
        self.assertEqual(codesigns, [["codesign", "-dv", SHIM]])

    def test_mixed_interpreters_name_only_the_shim_agents(self):
        run = FakeRun(codesign={SHIM: (0, CODESIGN_SHIM), REAL: (0, CODESIGN_REAL)})
        plists = {LABELS[0]: _plist(REAL), LABELS[1]: _plist(SHIM)}
        (r,) = self._rows(plists, run)
        self.assertEqual(r.status, doctor.WARN)
        self.assertIn("%s (server)" % SHIM, r.detail)
        self.assertNotIn("actd", r.detail)

    # -- a real interpreter ------------------------------------------------- #

    def test_real_interpreter_is_ok_and_named(self):
        run = FakeRun(codesign={REAL: (0, CODESIGN_REAL)})
        (r,) = self._rows({LABELS[0]: _plist(REAL), LABELS[1]: _plist(REAL)}, run)
        self.assertEqual(r.status, doctor.OK)
        self.assertIn("com.apple.python3", r.detail)
        self.assertIn(REAL, r.detail)
        self.assertEqual(r.fix, "")
        # OK 路径不需要问本体是谁
        self.assertFalse([c for c in run.calls if len(c) >= 2 and c[1] == "-c"])

    # -- helpers ------------------------------------------------------------ #

    def test_codesign_identifier_parses_the_identifier_line(self):
        run = FakeRun(codesign={SHIM: (0, CODESIGN_SHIM)})
        self.assertEqual(launchd.codesign_identifier(run, SHIM), launchd.SHIM_IDENTIFIER)
        self.assertIsNone(launchd.codesign_identifier(run, "/nonexistent"))
        self.assertIsNone(launchd.codesign_identifier(lambda *a, **k: (0, "Format=Mach-O\n"), SHIM))

    def test_real_interpreter_takes_the_last_absolute_line(self):
        self.assertEqual(launchd.real_interpreter(lambda *a, **k: (0, "warn\n%s\n" % REAL), SHIM), REAL)
        self.assertEqual(launchd.real_interpreter(lambda *a, **k: (1, REAL), SHIM), "")
        self.assertEqual(launchd.real_interpreter(lambda *a, **k: (0, "relative/python"), SHIM), "")
        self.assertEqual(launchd.real_interpreter(lambda *a, **k: (0, ""), SHIM), "")

    # -- wiring ------------------------------------------------------------- #

    def test_row_rides_right_behind_launchd_paths_on_darwin_only(self):
        with mock.patch("sys.platform", "darwin"):
            checks = doctor._checks_for_platform()
        self.assertIn(doctor._check_launchd_interpreter_identity, checks)
        self.assertEqual(checks.index(doctor._check_launchd_interpreter_identity),
                         checks.index(doctor._check_launchd_paths) + 1)
        with mock.patch("sys.platform", "linux"):
            self.assertNotIn(doctor._check_launchd_interpreter_identity,
                             doctor._checks_for_platform())

    def test_safe_derives_the_same_row_name_as_the_check(self):
        # _safe 的「diagnostic crashed」行名从函数名派生——两边必须逐字一致
        derived = doctor._check_launchd_interpreter_identity.__name__.replace(
            "_check_", "").replace("_", " ")
        self.assertEqual(derived, "launchd interpreter identity")

    def test_row_is_a_tcc_row_for_the_web_permissions_page_and_a_human_row(self):
        self.assertIn("launchd interpreter identity", permissions.TCC_ROW_NAMES)
        self.assertIn("launchd interpreter identity", fresh_install.HUMAN_ROW_NAMES)


if __name__ == "__main__":
    unittest.main()

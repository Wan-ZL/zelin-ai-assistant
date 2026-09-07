"""一键修复对**未加载**的 actd 走 install.sh --reinstall-agent（CONTRACT §68.8 D50 追记 / §48.7 / §55）：

- 未加载 → argv 恰为 ``["bash", <repo>/install.sh, "--reinstall-agent", "com.zelin.aiassistant.actd"]``
  （label / 路径全是 server 常量，payload 只认 ``{}``）；不 kickstart；
- install.sh 退出 0 → ``{"ok", "label", "action": "reinstall", "loaded"}``（loaded 再问一次 launchctl）；
- 退出 4（没 pinned 解释器）→ 409 CONFLICT，``details.fix`` = ``bash install.sh``、``details.command`` = 可复制的
  ``bash <repo>/install.sh``（路径 shlex.quote，含空格照贴）；install.sh 文件不在 → 同款 409；
- runner 超时（rc 124）→ 500 带「timed out」（整句只说一遍）；其余非零 → 500 带输出尾巴 + rc + command；
- 已加载仍 kickstart、绝不碰 install.sh；非 darwin 501；多余字段 400；
- 路由：``POST /api/repair/actd`` 走默认 install runner 的注入替身，envelope 形状与 details 落到 HTTP。
真 launchctl / 真 install.sh 一律不跑（runner 注入）。
"""
import os
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import assert_envelope, post_json, start_server

from server import paths, repair
from server.errors import ApiError, ConflictError, NotImplementedError501, UnknownFieldError


def _launchctl(loaded_states):
    """launchctl 替身：``print`` 按 loaded_states 依次回答（列表用尽重复最后一个）；其它子命令记账后回 0。"""
    calls = []
    answers = list(loaded_states)

    def run(argv):
        calls.append(list(argv))
        if argv[1] == "print":
            state = answers.pop(0) if len(answers) > 1 else answers[0]
            return (0 if state else 113), ""
        return 0, "kicked"
    return run, calls


def _install(rc=0, out="reinstalled com.zelin.aiassistant.actd"):
    calls = []

    def run(argv):
        calls.append(list(argv))
        return rc, out
    return run, calls


class ReinstallBranchTestCase(unittest.TestCase):
    def test_unloaded_runs_install_sh_reinstall_agent_with_server_constants(self):
        run, launchctl_calls = _launchctl([False, True])
        install, install_calls = _install()
        out = repair.kickstart_actd({}, runner=run, install_runner=install, platform="darwin")
        self.assertEqual(install_calls, [["bash", str(paths.repo_root() / "install.sh"),
                                          "--reinstall-agent", "com.zelin.aiassistant.actd"]])
        self.assertEqual(out, {"ok": True, "label": repair.ACTD_LABEL, "action": "reinstall", "loaded": True})
        # 第二次 print = 装完再问一次；从头到尾没有 kickstart
        self.assertEqual([c[1] for c in launchctl_calls], ["print", "print"])

    def test_success_reports_loaded_false_honestly_when_launchd_still_says_no(self):
        run, _calls = _launchctl([False, False])
        install, _ = _install()
        out = repair.kickstart_actd({}, runner=run, install_runner=install, platform="darwin")
        self.assertEqual(out["action"], "reinstall")
        self.assertFalse(out["loaded"])

    def test_exit_4_is_409_with_the_manual_command(self):
        run, _calls = _launchctl([False])
        install, _ = _install(rc=4, out="install.sh: no pinned daemon interpreter (config/runtime.json) — run: bash install.sh")
        with self.assertRaises(ConflictError) as ctx:
            repair.kickstart_actd({}, runner=run, install_runner=install, platform="darwin")
        details = ctx.exception.details
        self.assertEqual(details["fix"], "bash install.sh")
        self.assertEqual(details["command"], "bash %s" % (paths.repo_root() / "install.sh"))
        self.assertEqual(details["rc"], 4)
        self.assertEqual(details["label"], repair.ACTD_LABEL)
        self.assertIn("bash install.sh", ctx.exception.message)

    def test_missing_install_sh_is_409_without_running_anything(self):
        run, _calls = _launchctl([False])
        install, install_calls = _install()
        missing = Path(tempfile.mkdtemp(prefix="zai-no-install-"))
        self.addCleanup(lambda: os.rmdir(missing))
        with mock.patch.object(paths, "repo_root", lambda: missing):
            with self.assertRaises(ConflictError) as ctx:
                repair.kickstart_actd({}, runner=run, install_runner=install, platform="darwin")
        self.assertEqual(install_calls, [])
        self.assertEqual(ctx.exception.details["fix"], "bash install.sh")
        self.assertEqual(ctx.exception.details["command"], "bash %s" % (missing / "install.sh"))

    def test_timeout_is_500_with_a_timed_out_sentence(self):
        run, _calls = _launchctl([False])
        install, _ = _install(rc=124, out="install.sh --reinstall-agent timed out after 120s")
        with self.assertRaises(ApiError) as ctx:
            repair.kickstart_actd({}, runner=run, install_runner=install, platform="darwin")
        self.assertNotIsInstance(ctx.exception, ConflictError)
        self.assertIn("timed out", ctx.exception.message)
        self.assertEqual(ctx.exception.details["rc"], 124)
        self.assertIn("command", ctx.exception.details)

    def test_timeout_sentence_is_said_once_not_twice(self):
        # 默认 runner 的 124 尾巴已是整句；envelope 照用，横幅失败行不读成「timed out …: timed out …」
        run, _calls = _launchctl([False])
        install, _ = _install(rc=124, out="install.sh --reinstall-agent timed out after 120s")
        with self.assertRaises(ApiError) as ctx:
            repair.kickstart_actd({}, runner=run, install_runner=install, platform="darwin")
        self.assertEqual(ctx.exception.message, "install.sh --reinstall-agent timed out after 120s")
        self.assertEqual(ctx.exception.message.count("timed out"), 1)
        # 注入 runner 给空尾巴 → server 自己补一句，仍只说一遍
        install_silent, _ = _install(rc=124, out="")
        with self.assertRaises(ApiError) as ctx2:
            repair.kickstart_actd({}, runner=run, install_runner=install_silent, platform="darwin")
        self.assertEqual(ctx2.exception.message,
                         "install.sh --reinstall-agent timed out after %ds" % repair.INSTALL_TIMEOUT_S)

    def test_other_nonzero_is_500_with_the_output_tail(self):
        run, _calls = _launchctl([False])
        install, _ = _install(rc=1, out="x" * 1000 + "\n  [ERR ] failed to load com.zelin.aiassistant.actd (may need TCC/Full Disk Access approval)")
        with self.assertRaises(ApiError) as ctx:
            repair.kickstart_actd({}, runner=run, install_runner=install, platform="darwin")
        self.assertNotIsInstance(ctx.exception, ConflictError)
        self.assertIn("exited 1", ctx.exception.message)
        self.assertIn("failed to load", ctx.exception.message)
        self.assertLess(len(ctx.exception.message), 600)   # 尾巴有帽
        self.assertEqual(ctx.exception.details["rc"], 1)

    def test_loaded_agent_never_touches_install_sh(self):
        run, launchctl_calls = _launchctl([True])
        install, install_calls = _install()
        out = repair.kickstart_actd({}, runner=run, install_runner=install, platform="darwin")
        self.assertEqual(out, {"ok": True, "label": repair.ACTD_LABEL, "action": "kickstart"})
        self.assertEqual(install_calls, [])
        self.assertEqual(launchctl_calls[1][:3], ["/bin/launchctl", "kickstart", "-k"])

    def test_gates_still_apply_before_any_subprocess(self):
        run, launchctl_calls = _launchctl([False])
        install, install_calls = _install()
        with self.assertRaises(UnknownFieldError):
            repair.kickstart_actd({"label": "evil"}, runner=run, install_runner=install, platform="darwin")
        with self.assertRaises(NotImplementedError501):
            repair.kickstart_actd({}, runner=run, install_runner=install, platform="linux")
        self.assertEqual((launchctl_calls, install_calls), ([], []))

    def test_manual_command_is_the_full_install_at_this_checkout(self):
        self.assertEqual(repair.manual_command(), "bash %s" % shlex.quote(str(repair.install_sh_path())))
        self.assertEqual(repair.install_sh_path(), paths.repo_root() / "install.sh")
        self.assertTrue(repair.install_sh_path().is_file())
        # 模板在：--reinstall-agent 对这个 label 不会退出 2
        self.assertTrue((paths.repo_root() / "act" / "launchd" / (repair.ACTD_LABEL + ".plist")).is_file())

    def test_manual_command_shell_quotes_a_checkout_path_with_spaces(self):
        # 「手动命令：」是给人贴进终端的：路径含空格必须引起来，否则 bash 断在空格上；
        # 不含空格的路径 shlex.quote 原样不动（上一条判例的等式因此仍成立）
        spaced = Path(tempfile.mkdtemp(prefix="zai spaced "))
        self.addCleanup(lambda: os.rmdir(spaced))
        with mock.patch.object(paths, "repo_root", lambda: spaced):
            command = repair.manual_command()
        script = str(spaced / "install.sh")
        self.assertEqual(command, "bash '%s'" % script)
        self.assertEqual(shlex.split(command), ["bash", script])
        # 409 envelope 里的 details.command 就是这一条（install.sh 不在 → 同款 409，零子进程）
        run, _calls = _launchctl([False])
        install, install_calls = _install()
        with mock.patch.object(paths, "repo_root", lambda: spaced):
            with self.assertRaises(ConflictError) as ctx:
                repair.kickstart_actd({}, runner=run, install_runner=install, platform="darwin")
        self.assertEqual(ctx.exception.details["command"], command)
        self.assertEqual(install_calls, [])


class ReinstallRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-repair-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        _httpd, self.port = start_server(self, self.home)

    def _post(self, launchctl_states, install_rc, install_out=""):
        run, launchctl_calls = _launchctl(launchctl_states)
        install, install_calls = _install(rc=install_rc, out=install_out)
        with mock.patch.object(repair, "default_runner", run), \
                mock.patch.object(repair, "default_install_runner", install), \
                mock.patch.object(repair.sys, "platform", "darwin"):
            status, obj = post_json(self.port, "/api/repair/actd", {})
        return status, obj, install_calls

    def test_route_reinstalls_through_the_default_install_runner(self):
        status, obj, install_calls = self._post([False, True], 0)
        self.assertEqual(status, 200)
        self.assertEqual(obj, {"ok": True, "label": repair.ACTD_LABEL, "action": "reinstall", "loaded": True})
        self.assertEqual(install_calls[0][2:], ["--reinstall-agent", repair.ACTD_LABEL])

    def test_route_exit_4_is_http_409_with_command_in_details(self):
        status, obj, _ = self._post([False], 4, "no pinned daemon interpreter")
        self.assertEqual(status, 409)
        assert_envelope(self, obj, "CONFLICT")
        self.assertEqual(obj["error"]["details"]["fix"], "bash install.sh")
        self.assertTrue(obj["error"]["details"]["command"].endswith("/install.sh"))

    def test_route_other_failure_is_http_500(self):
        status, obj, _ = self._post([False], 1, "launchctl load failed")
        self.assertEqual(status, 500)
        self.assertIn("launchctl load failed", obj["error"]["message"])


if __name__ == "__main__":
    unittest.main()

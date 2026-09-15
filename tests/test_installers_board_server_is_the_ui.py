"""CONTRACT §49 追记 (2026-09-14, owner 决策 D67): the board server is the UI on
all three platforms, and the old ``act/webui.py`` service wiring is retired.

No Windows box exists here, so ``install.ps1`` can never be executed in CI —
its wiring is pinned as TEXT instead, the same trick tests/test_recap_cron_hook
uses on the shell installers. What must not silently drift:

  * both installers start the board server resident (Windows ``$ResidentLeaves``
    = actd + server; Linux ``ENABLE_UNITS`` carries zelin-server.service);
  * neither registers/enables a ``webui`` task or unit ever again, and no
    template for one is left on disk (the tombstoned files of §49 追记);
  * both ACTIVELY retire the already-installed wiring (§55 ``launchd_retire``
    discipline: remove + ask again + shout). Deleting a template does not
    unregister anything on a machine that already has it — the Windows task
    keeps its LogonTrigger and the Linux unit stays enabled with
    ``Restart=always``, which is the 51-day imessageradar pathology and would
    leave every EXISTING install with two boards / two ports / two tokens;
  * install.ps1 renders the §54 port through ``--zai-port`` (a hard-coded 47820
    would ignore ``config.yaml`` ``server.port``) and probes ``/api/health``
    afterwards — a registered task proves nothing bound;
  * both print the ``npm ci`` + ``npm run build`` hint, because neither
    installer builds ``web/dist`` for you (宪法第 3 条: say the gap out loud).
"""
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PS1 = REPO / "install.ps1"
LINUX = REPO / "install-linux.sh"


class WindowsInstallerTestCase(unittest.TestCase):
    def setUp(self):
        self.text = PS1.read_text(encoding="utf-8")

    def test_resident_leaves_are_actd_and_server(self):
        self.assertIn("$ResidentLeaves = @('actd', 'server')", self.text)

    def test_no_webui_task_is_registered_or_started(self):
        # webui 只许出现在「退役它」的代码里：注册 / 启动的行一律不许提它。
        for line in self.text.splitlines():
            if "webui" not in line or line.lstrip().startswith("#"):
                continue
            for verb in ("Register-ScheduledTask", "Start-ScheduledTask"):
                self.assertNotIn(verb, line,
                                 "install.ps1 still %s a webui task: %s" % (verb, line))
        self.assertNotIn("'webui'", self.text.split("$RetiredLeaves")[0],
                         "webui must not appear before the retirement list")

    def test_the_already_registered_webui_task_is_unregistered(self):
        # §55：删模板不会注销任何东西——旧任务带着 LogonTrigger 继续拉起 webui
        self.assertIn("$RetiredLeaves = @('webui')", self.text)
        self.assertIn("Unregister-ScheduledTask", self.text)
        self.assertIn("-TaskName $leaf -Confirm:$false", self.text)

    def test_the_retirement_proves_the_task_is_gone(self):
        # 注销之后必须再问一次 Get-ScheduledTask 并大声报（launchd_retire 的形状）
        unregister = self.text.index("Unregister-ScheduledTask")
        tail = self.text[unregister:]
        verify = tail.index("Get-ScheduledTask")
        self.assertLess(verify, tail.index("$ResidentLeaves"),
                        "the survival re-check must follow the unregister")
        self.assertIn("Write-Err2", tail[:tail.index("$ResidentLeaves")])

    def test_retirement_runs_before_the_registration_loop(self):
        self.assertLess(self.text.index("$RetiredLeaves"),
                        self.text.index("$ResidentLeaves"))

    def test_board_port_is_rendered_not_hard_coded(self):
        self.assertIn("--zai-port $ServerPort", self.text)
        self.assertIn("config.load_config().server_port", self.text)

    def test_port_is_probed_after_registration(self):
        self.assertIn("/api/health", self.text)
        probe = self.text.index("/api/health")
        leaves = self.text.index("$ResidentLeaves = @('actd', 'server')")
        self.assertLess(leaves, probe, "the probe must come after the tasks start")

    def test_web_build_hint_is_printed(self):
        self.assertIn("npm ci", self.text)
        self.assertIn("npm run build", self.text)


class LinuxInstallerTestCase(unittest.TestCase):
    def setUp(self):
        self.text = LINUX.read_text(encoding="utf-8")

    def _enable_units(self):
        block = self.text.split("ENABLE_UNITS=(", 1)[1]
        return block.split(")", 1)[0]

    def test_enables_the_board_server_and_never_the_webui_unit(self):
        self.assertIn('"zelin-server.service"', self.text)
        self.assertIn('"zelin-actd.service"', self._enable_units())
        self.assertNotIn("webui", self._enable_units())

    def test_the_already_enabled_webui_unit_is_retired(self):
        # §55：模板删了，unit 仍 enable + Restart=always —— 必须显式 disable + rm
        retired = self.text.split("RETIRED_UNITS=(", 1)[1].split(")", 1)[0]
        self.assertIn('"zelin-webui.service"', retired)
        self.assertIn("systemctl --user disable --now", self.text)
        self.assertIn('rm -f "$UNIT_DIR/$unit"', self.text)

    def test_the_retirement_proves_the_unit_is_gone(self):
        # disable + rm 之后再问一次（systemd_unit_known 两个面都查），还在就 err
        self.assertIn("systemd_unit_known()", self.text)
        body = self.text.split("RETIRED_UNITS=(", 1)[1]
        body = body[:body.index("ENABLE_UNITS=(")]
        self.assertEqual(2, body.count("systemd_unit_known"),
                         "retire = ask, act, ask again")
        self.assertIn("is STILL there", body)

    def test_retirement_runs_before_the_enable_loop(self):
        self.assertLess(self.text.index("RETIRED_UNITS=("),
                        self.text.index("ENABLE_UNITS=("))

    def test_web_build_hint_is_printed(self):
        self.assertIn("npm ci", self.text)
        self.assertIn("npm run build", self.text)


class RetiredTemplatesTestCase(unittest.TestCase):
    """The tombstoned files of §49 追记 are gone from every template dir."""

    def test_no_webui_template_survives(self):
        self.assertFalse((REPO / "act" / "systemd" / "zelin-webui.service").exists())
        self.assertFalse((REPO / "act" / "tasksched" / "zelin-webui.xml").exists())
        # macOS never had one (it dropped webui first) — assert it stays that way
        self.assertEqual(
            sorted(p.name for p in (REPO / "act" / "launchd").glob("*webui*")), [])

    def test_the_board_server_template_exists_on_all_three_platforms(self):
        self.assertTrue((REPO / "act" / "tasksched" / "zelin-server.xml").exists())
        self.assertTrue((REPO / "act" / "systemd" / "zelin-server.service").exists())
        self.assertTrue((REPO / "act" / "launchd"
                         / "com.zelin.aiassistant.server.plist").exists())


if __name__ == "__main__":
    unittest.main()

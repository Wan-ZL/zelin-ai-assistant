"""CONTRACT §49 追记 (2026-09-14, owner 决策 D67): the board server is the UI on
all three platforms, and the old ``act/webui.py`` service wiring is retired.

No Windows box exists here, so ``install.ps1`` can never be executed in CI —
its wiring is pinned as TEXT instead, the same trick tests/test_recap_cron_hook
uses on the shell installers. What must not silently drift:

  * both installers start the board server resident (Windows ``$ResidentLeaves``
    = actd + server; Linux ``ENABLE_UNITS`` carries zelin-server.service);
  * neither registers/enables a ``webui`` task or unit ever again, and no
    template for one is left on disk (the tombstoned files of §49 追记);
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
        # webui 只许出现在解释退役的注释行里（§49 追记的 tombstone）
        for line in self.text.splitlines():
            if "webui" in line:
                self.assertTrue(line.lstrip().startswith("#"),
                                "live install.ps1 line still mentions webui: %s" % line)

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

    def test_enables_the_board_server_and_never_the_webui_unit(self):
        self.assertIn('"zelin-server.service"', self.text)
        self.assertNotIn('"zelin-webui.service"', self.text)

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

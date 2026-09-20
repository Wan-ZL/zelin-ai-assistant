"""act/lib/taskscheduler.py — rendering the Windows Task Scheduler XML templates.

install.ps1 renders act/tasksched/*.xml by calling this module, so the
substitution is a single pure-string source of truth (no drift between "what
install registers" and "what CI validated"). These tests are pure string ops —
green on every CI runner (macOS / ubuntu / windows) — and are the CI-validated
proof that the Windows task set is well-formed; the REAL Register-ScheduledTask
load + AtLogOn/repetition/restart behavior is friend-tested on a Windows box
(docs/WINDOWS.md).

Pinned facts the port depends on:
  * every @TOKEN@ placeholder is substituted (no leftovers);
  * every rendered file is well-formed XML in the Task Scheduler namespace;
  * the resident actd/server tasks carry LogonTrigger + RestartOnFailure and no
    repetition (KeepAlive/Restart=always equivalent) — `server` is the Windows
    mirror of zelin-server.service and carries ZAI_PORT, and the retired
    `webui` task must never come back (CONTRACT §49 追记 / owner 决策 D67:
    the board server is the one UI on all three platforms);
  * the periodic radars/digest carry LogonTrigger + Repetition (no restart);
  * the login-shell claude dir is prepended FIRST on the task PATH (the guard);
  * AIASSISTANT_HOME + WorkingDirectory point at the repo root.
"""
import xml.etree.ElementTree as ET

import unittest

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first
from tests.scratch_testkit import scratch_dir

from act.lib import taskscheduler as ts

# Windows-shaped fixture paths (the mirror of the Linux paths in test_systemd_render).
PY = r"C:\Program Files\Python312\python.exe"
REPO = r"C:\Users\Friend\Projects\zelin-ai-assistant"
CLAUDE_DIR = r"C:\Users\Friend\.local\bin"

NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"

RESIDENT = ("zelin-actd.xml", "zelin-server.xml")
PORT = "51820"  # deliberately NOT the default, so a hard-coded 47820 fails
PERIODIC = {
    "zelin-gmail-radar.xml": ("act.radar_gmail", "PT5M"),
    "zelin-slack-radar.xml": ("act.radar_slack", "PT3M"),
    "zelin-obsidian-radar.xml": ("act.radar", "PT30M"),
    "zelin-weekly-digest.xml": ("act.weekly_digest", "PT1H"),
}


def _args(text: str) -> str:
    root = ET.fromstring(text)
    return root.find(f"{NS}Actions/{NS}Exec/{NS}Arguments").text


class RenderPrimitiveTestCase(unittest.TestCase):
    def test_all_tokens_substituted(self):
        tmpl = ("<Arguments>&amp; '@PYTHON@' -m act.actd</Arguments>"
                "<WorkingDirectory>@REPO_ROOT@</WorkingDirectory>"
                "<X>@CLAUDE_BIN_DIR@;@ZAI_PORT@</X>")
        out = ts.render(tmpl, PY, REPO, CLAUDE_DIR, PORT)
        self.assertIn(PY, out)
        self.assertIn(REPO, out)
        self.assertIn(CLAUDE_DIR, out)
        self.assertIn(PORT, out)
        for token in ts._TOKENS:
            self.assertNotIn(token, out)

    def test_port_defaults_to_the_server_default(self):
        # act never imports server/, so the default is mirrored here — the same
        # deal act/lib/systemd.py makes (§54).
        self.assertEqual(ts.DEFAULT_ZAI_PORT, "47820")
        out = ts.render("<X>@ZAI_PORT@</X>", PY, REPO, CLAUDE_DIR)
        self.assertIn(ts.DEFAULT_ZAI_PORT, out)

    def test_task_name_helpers(self):
        self.assertEqual(ts.task_leaf("zelin-gmail-radar.xml"), "gmail-radar")
        self.assertEqual(ts.task_leaf("zelin-actd.xml"), "actd")
        self.assertEqual(ts.full_task_name("zelin-actd.xml"),
                         "\\ZelinAIAssistant\\actd")


class RenderTemplatesTestCase(unittest.TestCase):
    """Render the real on-disk templates and assert the task contract."""

    def setUp(self):
        self.rendered = ts.render_all(PY, REPO, CLAUDE_DIR, zai_port=PORT)

    def test_expected_task_set_present(self):
        names = set(self.rendered)
        self.assertIn("zelin-actd.xml", names)
        self.assertIn("zelin-server.xml", names)
        # retired 2026-09-14 (§49 追记 / D67): one board, one port, one token
        self.assertNotIn("zelin-webui.xml", names)
        for base in PERIODIC:
            self.assertIn(base, names)

    def test_no_placeholder_survives_any_task(self):
        for name, text in self.rendered.items():
            for token in ts._TOKENS:
                self.assertNotIn(token, text, "%s in %s" % (token, name))
            self.assertNotIn("YOURUSERNAME", text, name)

    def test_every_rendered_file_is_well_formed_xml(self):
        for name, text in self.rendered.items():
            try:
                root = ET.fromstring(text)
            except ET.ParseError as exc:
                self.fail("%s is not well-formed XML: %s" % (name, exc))
            self.assertTrue(root.tag.endswith("}Task") or root.tag == "Task", name)

    def test_resident_tasks_have_logon_trigger_and_restart_no_repetition(self):
        for name in RESIDENT:
            root = ET.fromstring(self.rendered[name])
            trig = root.find(f"{NS}Triggers/{NS}LogonTrigger")
            self.assertIsNotNone(trig, name)
            self.assertIsNone(trig.find(f"{NS}Repetition"), name)
            self.assertIsNotNone(root.find(f"{NS}Settings/{NS}RestartOnFailure"),
                                 name)
            # no execution time limit on a resident daemon
            etl = root.find(f"{NS}Settings/{NS}ExecutionTimeLimit")
            self.assertEqual(etl.text, "PT0S", name)

    def test_resident_tasks_exec_the_right_modules(self):
        self.assertIn("-m act.actd", _args(self.rendered["zelin-actd.xml"]))
        self.assertIn("-m server", _args(self.rendered["zelin-server.xml"]))
        # no task anywhere still starts the retired dashboard (§49 追记 / D67)
        for name, text in self.rendered.items():
            self.assertNotIn("-m act.webui", text, name)

    def test_server_task_is_registered_under_the_server_leaf(self):
        # the leaf is the canonical slug shared by the launchd label / systemd
        # unit / schtasks name (防腐 #9); act.doctor derives its expected set
        # from this very filename.
        self.assertEqual(ts.full_task_name("zelin-server.xml"),
                         "\\ZelinAIAssistant\\server")
        root = ET.fromstring(self.rendered["zelin-server.xml"])
        uri = root.find(f"{NS}RegistrationInfo/{NS}URI").text
        self.assertEqual(uri, "\\ZelinAIAssistant\\server")

    def test_only_the_server_task_carries_the_board_port(self):
        # ZAI_PORT is the board server's env (§49 / §54) — it has no business
        # in actd's or a radar's minimal environment.
        self.assertIn("$env:ZAI_PORT='%s'" % PORT,
                      _args(self.rendered["zelin-server.xml"]))
        for name, text in self.rendered.items():
            if name == "zelin-server.xml":
                continue
            self.assertNotIn("ZAI_PORT", text, name)

    def test_periodic_tasks_repeat_at_the_right_interval_no_restart(self):
        for name, (module, interval) in PERIODIC.items():
            root = ET.fromstring(self.rendered[name])
            rep = root.find(f"{NS}Triggers/{NS}LogonTrigger/{NS}Repetition")
            self.assertIsNotNone(rep, name)
            self.assertEqual(rep.find(f"{NS}Interval").text, interval, name)
            self.assertIsNone(root.find(f"{NS}Settings/{NS}RestartOnFailure"),
                              name)
            self.assertIn("-m %s" % module, _args(self.rendered[name]), name)

    def test_periodic_radars_pass_once(self):
        for name in ("zelin-gmail-radar.xml", "zelin-slack-radar.xml",
                     "zelin-obsidian-radar.xml"):
            self.assertIn("--once", _args(self.rendered[name]), name)

    def test_multiple_instances_ignore_new_everywhere(self):
        # the Windows substitute for the radar fcntl pass-lock + resident dedup
        for name, text in self.rendered.items():
            root = ET.fromstring(text)
            mip = root.find(f"{NS}Settings/{NS}MultipleInstancesPolicy")
            self.assertEqual(mip.text, "IgnoreNew", name)

    def test_claude_dir_prepended_first_on_every_task_path(self):
        for name, text in self.rendered.items():
            args = _args(text)
            self.assertIn("$env:PATH='%s;'+$env:PATH" % CLAUDE_DIR, args, name)

    def test_every_task_sets_home_and_workdir(self):
        for name, text in self.rendered.items():
            root = ET.fromstring(text)
            args = _args(text)
            self.assertIn("$env:AIASSISTANT_HOME='%s'" % REPO, args, name)
            wd = root.find(f"{NS}Actions/{NS}Exec/{NS}WorkingDirectory").text
            self.assertEqual(wd, REPO, name)


class RenderCliTestCase(unittest.TestCase):
    def test_main_writes_rendered_xml_into_out_dir(self):
        from pathlib import Path
        out = Path(scratch_dir(self, prefix="tasksched-out-"))
        rc = ts.main(["--python", PY, "--repo-root", REPO,
                      "--claude-bin-dir", CLAUDE_DIR, "--zai-port", PORT,
                      "--out", str(out)])
        self.assertEqual(rc, 0)
        written = sorted(p.name for p in out.glob("*.xml"))
        self.assertEqual(written, sorted(ts.render_all(PY, REPO, CLAUDE_DIR)))
        # install.ps1 passes the config.yaml port through this flag (§54)
        server_xml = (out / "zelin-server.xml").read_text(encoding="utf-8")
        self.assertIn("$env:ZAI_PORT='%s'" % PORT, server_xml)
        # and each written file is well-formed with no leftover tokens
        for p in out.glob("*.xml"):
            text = p.read_text(encoding="utf-8")
            ET.fromstring(text)
            for token in ts._TOKENS:
                self.assertNotIn(token, text, p.name)


if __name__ == "__main__":
    unittest.main()

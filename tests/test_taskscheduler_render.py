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
  * the resident actd/webui tasks carry LogonTrigger + RestartOnFailure and no
    repetition (KeepAlive/Restart=always equivalent);
  * the periodic radars/digest carry LogonTrigger + Repetition (no restart);
  * the login-shell claude dir is prepended FIRST on the task PATH (the guard);
  * AIASSISTANT_HOME + WorkingDirectory point at the repo root.
"""
import xml.etree.ElementTree as ET

import unittest

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act.lib import taskscheduler as ts

# Windows-shaped fixture paths (the mirror of the Linux paths in test_systemd_render).
PY = r"C:\Program Files\Python312\python.exe"
REPO = r"C:\Users\Friend\Projects\zelin-ai-assistant"
CLAUDE_DIR = r"C:\Users\Friend\.local\bin"

NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"

RESIDENT = ("zelin-actd.xml", "zelin-webui.xml")
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
                "<X>@CLAUDE_BIN_DIR@;</X>")
        out = ts.render(tmpl, PY, REPO, CLAUDE_DIR)
        self.assertIn(PY, out)
        self.assertIn(REPO, out)
        self.assertIn(CLAUDE_DIR, out)
        for token in ts._TOKENS:
            self.assertNotIn(token, out)

    def test_task_name_helpers(self):
        self.assertEqual(ts.task_leaf("zelin-gmail-radar.xml"), "gmail-radar")
        self.assertEqual(ts.task_leaf("zelin-actd.xml"), "actd")
        self.assertEqual(ts.full_task_name("zelin-actd.xml"),
                         "\\ZelinAIAssistant\\actd")


class RenderTemplatesTestCase(unittest.TestCase):
    """Render the real on-disk templates and assert the task contract."""

    def setUp(self):
        self.rendered = ts.render_all(PY, REPO, CLAUDE_DIR)

    def test_expected_task_set_present(self):
        names = set(self.rendered)
        self.assertIn("zelin-actd.xml", names)
        self.assertIn("zelin-webui.xml", names)
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
        self.assertIn("-m act.webui", _args(self.rendered["zelin-webui.xml"]))

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
        import tempfile
        from pathlib import Path
        out = Path(tempfile.mkdtemp(prefix="tasksched-out-"))
        rc = ts.main(["--python", PY, "--repo-root", REPO,
                      "--claude-bin-dir", CLAUDE_DIR, "--out", str(out)])
        self.assertEqual(rc, 0)
        written = sorted(p.name for p in out.glob("*.xml"))
        self.assertEqual(written, sorted(ts.render_all(PY, REPO, CLAUDE_DIR)))
        # and each written file is well-formed with no leftover tokens
        for p in out.glob("*.xml"):
            text = p.read_text(encoding="utf-8")
            ET.fromstring(text)
            for token in ts._TOKENS:
                self.assertNotIn(token, text, p.name)


if __name__ == "__main__":
    unittest.main()

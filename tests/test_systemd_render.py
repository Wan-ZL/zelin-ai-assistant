"""act/lib/systemd.py — rendering the systemd user-unit templates.

install-linux.sh renders act/systemd/*.service|*.timer by calling this module,
so the substitution is a single pure-string source of truth (no sed/install
drift). These tests are pure string ops — green on every CI runner — and are
the CI-validated proof that the Linux unit set is well-formed; the REAL
`systemctl --user enable --now` load + Restart=always behavior is friend-tested
on a Linux box (docs/LINUX.md).

Pinned facts the port depends on:
  * every @TOKEN@ placeholder is substituted (no leftovers);
  * the resident actd/webui units carry Restart=always (KeepAlive equivalent);
  * the periodic radars/digest are timer-driven (Type=oneshot + a .timer);
  * the login-shell claude dir is FIRST on the unit PATH (the 2026-07-08 guard);
  * AIASSISTANT_HOME + WorkingDirectory point at the repo root.
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act.lib import systemd

PY = "/home/friend/miniconda3/bin/python3"
REPO = "/home/friend/Projects/zelin-ai-assistant"
CLAUDE_DIR = "/home/friend/.local/bin"


class RenderPrimitiveTestCase(unittest.TestCase):
    def test_all_tokens_substituted(self):
        tmpl = ("ExecStart=@PYTHON@ -m act.actd\n"
                "WorkingDirectory=@REPO_ROOT@\n"
                "Environment=PATH=@CLAUDE_BIN_DIR@:%h/.local/bin\n")
        out = systemd.render(tmpl, PY, REPO, CLAUDE_DIR)
        self.assertIn("ExecStart=%s -m act.actd" % PY, out)
        self.assertIn("WorkingDirectory=%s" % REPO, out)
        self.assertIn("Environment=PATH=" + CLAUDE_DIR + ":%h/.local/bin", out)
        for token in systemd._TOKENS:
            self.assertNotIn(token, out)

    def test_systemd_home_specifier_is_left_untouched(self):
        # %h is systemd's own $HOME specifier — the renderer must NOT touch it
        out = systemd.render("PATH=@CLAUDE_BIN_DIR@:%h/.local/bin",
                             PY, REPO, CLAUDE_DIR)
        self.assertIn("%h/.local/bin", out)


class RenderTemplatesTestCase(unittest.TestCase):
    """Render the real on-disk templates and assert the unit contract."""

    def setUp(self):
        self.rendered = systemd.render_all(PY, REPO, CLAUDE_DIR)

    def test_expected_unit_set_present(self):
        names = set(self.rendered)
        # resident services
        self.assertIn("zelin-actd.service", names)
        self.assertIn("zelin-webui.service", names)
        # a timer+service pair per periodic scan + the weekly digest
        for base in ("zelin-gmail-radar", "zelin-slack-radar",
                     "zelin-obsidian-radar", "zelin-weekly-digest"):
            self.assertIn(base + ".service", names, base)
            self.assertIn(base + ".timer", names, base)

    def test_no_placeholder_survives_any_unit(self):
        for name, text in self.rendered.items():
            for token in systemd._TOKENS:
                self.assertNotIn(token, text, "%s in %s" % (token, name))
            # and no other stray @...@ install placeholder
            self.assertNotIn("YOURUSERNAME", text, name)

    def test_resident_units_have_restart_always(self):
        for name in ("zelin-actd.service", "zelin-webui.service"):
            text = self.rendered[name]
            self.assertIn("Restart=always", text, name)
            self.assertIn("Type=simple", text, name)
            self.assertIn("[Install]", text, name)
            self.assertIn("WantedBy=default.target", text, name)

    def test_actd_and_webui_exec_the_right_modules(self):
        self.assertIn("ExecStart=%s -m act.actd" % PY,
                      self.rendered["zelin-actd.service"])
        self.assertIn("ExecStart=%s -m act.webui" % PY,
                      self.rendered["zelin-webui.service"])

    def test_radar_services_are_oneshot(self):
        pairs = {
            "zelin-gmail-radar": "act.radar_gmail",
            "zelin-slack-radar": "act.radar_slack",
            "zelin-obsidian-radar": "act.radar",
            "zelin-weekly-digest": "act.weekly_digest",
        }
        for base, module in pairs.items():
            svc = self.rendered[base + ".service"]
            self.assertIn("Type=oneshot", svc, base)
            self.assertIn("-m %s" % module, svc, base)
            # oneshots are timer-driven, not enabled on their own
            self.assertNotIn("[Install]", svc, base)

    def test_timers_install_into_timers_target_and_point_at_service(self):
        for base in ("zelin-gmail-radar", "zelin-slack-radar",
                     "zelin-obsidian-radar", "zelin-weekly-digest"):
            timer = self.rendered[base + ".timer"]
            self.assertIn("[Timer]", timer, base)
            self.assertIn("Unit=%s.service" % base, timer, base)
            self.assertIn("WantedBy=timers.target", timer, base)

    def test_weekly_digest_timer_is_calendar_and_persistent(self):
        timer = self.rendered["zelin-weekly-digest.timer"]
        self.assertIn("OnCalendar=", timer)
        self.assertIn("Persistent=true", timer)

    def test_claude_dir_is_first_on_every_unit_path(self):
        for name, text in self.rendered.items():
            for line in text.splitlines():
                if line.startswith("Environment=PATH="):
                    path_val = line[len("Environment=PATH="):]
                    self.assertTrue(
                        path_val.startswith(CLAUDE_DIR + ":"),
                        "%s: claude dir not first on PATH: %s" % (name, path_val))

    def test_every_service_sets_home_and_workdir(self):
        # only .service units run a process; .timer units carry no exec/env
        for name, text in self.rendered.items():
            if not name.endswith(".service"):
                continue
            self.assertIn("Environment=AIASSISTANT_HOME=%s" % REPO, text, name)
            self.assertIn("WorkingDirectory=%s" % REPO, text, name)


if __name__ == "__main__":
    unittest.main()

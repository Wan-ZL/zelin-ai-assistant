"""copy_cmd starts with the worker's OWN claude binary (CONTRACT §55 第五幕
追记 2026-09-06; §2 / §6 / §68.7 copy_cmd).

Live 2026-09-04 / 09-07: the board handed the owner ``claude --resume <sid>``;
his terminal resolved that word to the login shell's Claude Code (2.1.261)
while actd had launched the worker from the stable daemon copy (2.1.259,
``config.resolve_claude_bin``) — the newer client attached to the older worker
and printed ``[worker crashed (exit 143) — respawning…] Session … has exited
(exit 1 before init)``. The takeover must run the file the worker runs, so
``act/lib/dashboard._takeover_claude`` spells the same resolution dispatch used
(pin → stable copy → PATH), quoted as ONE shell word, and every copy_cmd form
(``attach`` / ``--resume`` / ``cd … && --resume``) starts with it. Bare
``claude`` survives only as the fallback when that path is not an executable
on this disk. Same file, second bare-``claude`` site: the roster query
(``claude agents --json --all``) now resolves the same way — on the live
machine it had spawned Claude Code's per-user daemon from the login shell's
binary while dispatch used the copy.

The wire key is unchanged (add-only: same key, better value); the server's
terminal takeover (server/terminal_launch.command_for) passes copy_cmd through
verbatim and therefore inherits the fix without a change of its own.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import config, dashboard
from act.lib.registry import Requirement

SID = "feedc0de-0000-0000-0000-000000000000"


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="copycmd-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # $HOME → empty sandbox: no ~/.claude/projects transcript, no
        # ~/.local/bin/claude; the stable copy path carries a SPACE on purpose
        # (the real one lives under ~/Library/Application Support).
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.stable = self.tmp / "Application Support" / "bin" / "claude"
        env = mock.patch.dict(os.environ, {"HOME": str(self.home),
                                           "AIASSISTANT_STABLE_CLAUDE": str(self.stable)})
        env.start()
        self.addCleanup(env.stop)
        self.cfg = config.Config()

    def _make_exec(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    @staticmethod
    def _executing(with_agent: bool):
        req = Requirement.from_dict({
            "id": "R-500", "title": "接管同源 binary", "status": "executing",
            "execution": {"session_id": "feedc0de"},
        })
        agents = [{"id": "feedc0de", "sessionId": SID, "state": "working",
                   "cwd": "/tmp/worktree", **({"pid": 4242} if with_agent else {})}]
        return req, agents

    def _copy_cmd(self, with_agent: bool):
        req, agents = self._executing(with_agent)
        dash = dashboard.build_dashboard(reqs=[req], agents=agents, cfg=self.cfg)
        return dash["running"][0]["copy_cmd"]


class TakeoverUsesTheWorkerBinaryTestCase(_Base):
    def test_stable_copy_present_attach_form_is_quoted_path(self):
        self._make_exec(self.stable)
        self.assertEqual(self._copy_cmd(with_agent=True),
                         "'%s' attach feedc0de" % self.stable)

    def test_stable_copy_present_resume_form_is_quoted_path(self):
        self._make_exec(self.stable)
        # no live pid, no transcript on disk → plain --resume with the full sid
        self.assertEqual(self._copy_cmd(with_agent=False),
                         "'%s' --resume %s" % (self.stable, SID))

    def test_stable_copy_present_cd_resume_form_keeps_cd_and_quotes_the_binary(self):
        self._make_exec(self.stable)
        proj = self.home / ".claude" / "projects" / "-tmp-worktree"
        proj.mkdir(parents=True)
        (proj / f"{SID}.jsonl").write_text('{"type": "user", "cwd": "/tmp/agent worktree"}\n',
                                           encoding="utf-8")
        self.assertEqual(self._copy_cmd(with_agent=False),
                         "cd '/tmp/agent worktree' && '%s' --resume %s" % (self.stable, SID))

    def test_pin_beats_the_stable_copy_and_is_one_shell_word(self):
        self._make_exec(self.stable)
        pin = self._make_exec(self.tmp / "pinned dir" / "claude")
        self.cfg.claude_bin = str(pin)
        self.assertEqual(self._copy_cmd(with_agent=True), "'%s' attach feedc0de" % pin)

    def test_path_without_special_chars_is_left_bare_but_still_one_word(self):
        pin = self._make_exec(self.tmp / "plain" / "claude")
        self.cfg.claude_bin = str(pin)
        self.assertEqual(self._copy_cmd(with_agent=True), "%s attach feedc0de" % pin)

    def test_takeover_word_is_the_dispatch_argv0(self):
        # the invariant itself: whatever llm.dispatch_argv would exec, the
        # takeover command names the same file
        from act import llm
        self._make_exec(self.stable)
        argv0 = llm.dispatch_argv(self.cfg)[0]
        self.assertEqual(argv0, str(self.stable))
        self.assertEqual(dashboard._takeover_claude(self.cfg), "'%s'" % argv0)


class BareClaudeFallbackTestCase(_Base):
    def test_pinned_path_that_does_not_exist_falls_back_to_bare_claude(self):
        self.cfg.claude_bin = "/nonexistent/claude"
        self.assertEqual(self._copy_cmd(with_agent=True), "claude attach feedc0de")
        self.assertEqual(self._copy_cmd(with_agent=False), "claude --resume %s" % SID)

    def test_no_copy_no_pin_nothing_on_path_falls_back_to_bare_claude(self):
        with mock.patch.object(dashboard.config.shutil, "which", return_value=None):
            self.assertEqual(self._copy_cmd(with_agent=True), "claude attach feedc0de")

    def test_path_claude_is_named_when_that_is_what_dispatch_launched(self):
        # no copy, no pin: dispatch launches whatever PATH resolves — so does
        # the takeover (same file by construction, even without the copy)
        on_path = self._make_exec(self.tmp / "opt" / "claude")
        with mock.patch.object(dashboard.config.shutil, "which", return_value=str(on_path)):
            self.assertEqual(self._copy_cmd(with_agent=True), "%s attach feedc0de" % on_path)

    def test_stable_copy_that_is_not_executable_does_not_count(self):
        self.stable.parent.mkdir(parents=True)
        self.stable.write_text("not a binary", encoding="utf-8")   # mode 0644
        with mock.patch.object(dashboard.config.shutil, "which", return_value=None):
            self.assertEqual(self._copy_cmd(with_agent=True), "claude attach feedc0de")

    def test_no_session_id_still_emits_no_command(self):
        # the empty-sid guard (EmptySidNoGlobBindTestCase) is untouched by the
        # binary word: nothing to attach to → None, never "'<bin>' --resume "
        self._make_exec(self.stable)
        req = Requirement.from_dict({"id": "R-501", "title": "无会话", "status": "review",
                                     "execution": {}})
        dash = dashboard.build_dashboard(reqs=[req], agents=[], cfg=self.cfg)
        self.assertIsNone(dash["review"][0]["copy_cmd"])


class RosterQueryUsesTheSameBinaryTestCase(_Base):
    def _seen_argv(self, call):
        seen = []

        def run(argv, **kw):
            seen.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, stdout="[]", stderr="")
        with mock.patch.object(dashboard.subprocess, "run", run):
            call()
        return seen

    def test_run_claude_agents_resolves_like_dispatch(self):
        self._make_exec(self.stable)
        seen = self._seen_argv(lambda: dashboard._run_claude_agents(self.cfg))
        self.assertEqual(seen, [[str(self.stable), "agents", "--json", "--all"]])

    def test_build_dashboard_passes_its_cfg_to_the_roster_query(self):
        pin = self._make_exec(self.tmp / "pinned" / "claude")
        self.cfg.claude_bin = str(pin)
        seen = self._seen_argv(lambda: dashboard.build_dashboard(reqs=[], agents=None, cfg=self.cfg))
        self.assertEqual(seen, [[str(pin), "agents", "--json", "--all"]])

    def test_without_copy_or_pin_the_query_still_resolves_via_path(self):
        on_path = self._make_exec(self.tmp / "opt" / "claude")
        with mock.patch.object(dashboard.config.shutil, "which", return_value=str(on_path)):
            seen = self._seen_argv(lambda: dashboard._run_claude_agents(self.cfg))
        self.assertEqual(seen[0][0], str(on_path))

    def test_no_bare_claude_argv_is_left_in_act_or_server(self):
        # the one bare ["claude", …] site was this module's roster query;
        # every claude we spawn must be the same file (§55 第五幕 追记)
        repo = Path(__file__).resolve().parents[1]
        offenders = []
        for sub in ("act", "server"):
            for py in (repo / sub).rglob("*.py"):
                for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                    code = line.split("#", 1)[0]
                    if '["claude",' in code or "['claude'," in code:
                        offenders.append("%s:%d" % (py.relative_to(repo), n))
        self.assertEqual(offenders, [])


class ServerTakeoverInheritsTestCase(unittest.TestCase):
    def test_terminal_launch_passes_the_quoted_binary_through(self):
        # server/terminal_launch derives the .command from copy_cmd verbatim
        # (§68.7) — the fix reaches the terminal button with no server change
        from server import terminal_launch
        cmd = "'/Users/o/Library/Application Support/ZelinAIAssistant/bin/claude' attach feedc0de"
        self.assertEqual(terminal_launch.command_for({"copy_cmd": cmd}), cmd)


if __name__ == "__main__":
    unittest.main()

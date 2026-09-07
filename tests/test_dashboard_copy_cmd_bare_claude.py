"""copy_cmd starts with a bare ``claude`` even when the stable daemon copy or
an ``execution.claude_bin`` pin exists (CONTRACT §55 第五幕 追记 2026-09-07;
§2 / §6 / §68.7 copy_cmd).

Pins a decision, not an accident. PR #281's first cut wrote the dispatch
binary (``config.resolve_claude_bin``: pin → stable copy → PATH) into every
copy_cmd on the theory that the worker runs the file dispatch launched it
with. The live machine's ``~/.claude/daemon.log`` + ``ps`` say otherwise: a
``--bg`` worker is a spare pre-spawned by Claude Code's per-user daemon and
runs the DAEMON's binary; the daemon watches the path it was spawned from
(``~/.local/bin/claude`` in every recorded upgrade) and self-restarts when
that changes — so the worker's version follows the login shell, and the
stable copy is the party that lags between deploys. A copy_cmd naming the
copy would attach an OLDER client to a NEWER worker for the whole lag window.
Hence the takeover word stays the terminal's own ``claude``: the login shell
resolves it to the same file the daemon runs.

The server's terminal takeover (server/terminal_launch.command_for) passes
copy_cmd through verbatim, so the same word reaches the shell line.
"""
import os
import shutil
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
        # $HOME → empty sandbox (no ~/.claude/projects transcript); the stable
        # copy path carries a SPACE like the real one under
        # ~/Library/Application Support — a quoted path would show up as such.
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
            "id": "R-500", "title": "接管命令的 claude 词", "status": "executing",
            "execution": {"session_id": "feedc0de"},
        })
        agents = [{"id": "feedc0de", "sessionId": SID, "state": "working",
                   "cwd": "/tmp/worktree", **({"pid": 4242} if with_agent else {})}]
        return req, agents

    def _copy_cmd(self, with_agent: bool):
        req, agents = self._executing(with_agent)
        dash = dashboard.build_dashboard(reqs=[req], agents=agents, cfg=self.cfg)
        return dash["running"][0]["copy_cmd"]


class CopyCmdStaysBareClaudeTestCase(_Base):
    def test_stable_copy_present_attach_form_is_bare_claude(self):
        self._make_exec(self.stable)
        self.assertEqual(config.resolve_claude_bin(self.cfg), str(self.stable))  # dispatch WOULD use the copy
        self.assertEqual(self._copy_cmd(with_agent=True), "claude attach feedc0de")

    def test_stable_copy_present_resume_form_is_bare_claude(self):
        self._make_exec(self.stable)
        # no live pid, no transcript on disk → plain --resume with the full sid
        self.assertEqual(self._copy_cmd(with_agent=False), "claude --resume %s" % SID)

    def test_stable_copy_present_cd_resume_form_keeps_cd_and_bare_claude(self):
        self._make_exec(self.stable)
        proj = self.home / ".claude" / "projects" / "-tmp-worktree"
        proj.mkdir(parents=True)
        (proj / f"{SID}.jsonl").write_text('{"type": "user", "cwd": "/tmp/agent worktree"}\n',
                                           encoding="utf-8")
        self.assertEqual(self._copy_cmd(with_agent=False),
                         "cd '/tmp/agent worktree' && claude --resume %s" % SID)

    def test_pin_does_not_reach_copy_cmd_either(self):
        pin = self._make_exec(self.tmp / "pinned dir" / "claude")
        self.cfg.claude_bin = str(pin)
        self.assertEqual(self._copy_cmd(with_agent=True), "claude attach feedc0de")
        self.assertEqual(self._copy_cmd(with_agent=False), "claude --resume %s" % SID)

    def test_the_word_is_one_constant(self):
        # one spelling for all three forms — whoever changes the decision
        # changes TAKEOVER_CLAUDE and this file, not a format string
        self.assertEqual(dashboard.TAKEOVER_CLAUDE, "claude")
        self.assertEqual(dashboard._copy_cmd({"pid": 1}, "abcd1234", None), "claude attach abcd1234")
        self.assertEqual(dashboard._copy_cmd({}, "abcd1234", SID), "claude --resume %s" % SID)

    def test_no_session_id_still_emits_no_command(self):
        # the empty-sid guard (EmptySidNoGlobBindTestCase) is untouched:
        # nothing to attach to → None, never "claude --resume "
        req = Requirement.from_dict({"id": "R-501", "title": "无会话", "status": "review",
                                     "execution": {}})
        dash = dashboard.build_dashboard(reqs=[req], agents=[], cfg=self.cfg)
        self.assertIsNone(dash["review"][0]["copy_cmd"])


class ServerTakeoverPassesTheWordThroughTestCase(unittest.TestCase):
    def test_terminal_launch_uses_copy_cmd_verbatim(self):
        # server/terminal_launch derives the shell line from copy_cmd verbatim
        # (§68.7); the login shell (`zsh -lc`) then resolves the bare word
        from server import terminal_launch
        self.assertEqual(terminal_launch.command_for({"copy_cmd": "claude attach feedc0de"}),
                         "claude attach feedc0de")


if __name__ == "__main__":
    unittest.main()

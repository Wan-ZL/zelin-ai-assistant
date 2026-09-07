"""The projection's roster query (``claude agents --json --all``) spawns the
claude every other subprocess site of ours spawns (CONTRACT §55 第五幕 追记
2026-09-07): ``config.resolve_claude_bin`` (pin → stable daemon copy → PATH)
with ``DISABLE_AUTOUPDATER=1`` in its env.

It was the last bare ``["claude", …]`` argv in act/ + server/ — the executor's
``_roster_query`` and ``stop_session`` had resolved through the same function
since §55 第五幕. Nothing about the WORKER binary hangs on this (verified on
the live machine: the query does not spawn Claude Code's per-user daemon; see
tests/test_dashboard_copy_cmd_bare_claude.py for that side); this is the one
resolution rule for every claude we run, and actd's launchd PATH is hostile
(§55: a second, outdated claude ranked first on it once broke every dispatch).
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


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="roster-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
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

    def _seen(self, call):
        seen = []

        def run(argv, **kw):
            seen.append((list(argv), kw))
            return subprocess.CompletedProcess(argv, 0, stdout="[]", stderr="")
        with mock.patch.object(dashboard.subprocess, "run", run):
            call()
        return seen


class RosterQueryResolvesLikeDispatchTestCase(_Base):
    def test_stable_copy_present_query_runs_the_copy(self):
        self._make_exec(self.stable)
        seen = self._seen(lambda: dashboard._run_claude_agents(self.cfg))
        self.assertEqual([argv for argv, _ in seen],
                         [[str(self.stable), "agents", "--json", "--all"]])

    def test_pin_beats_the_copy(self):
        self._make_exec(self.stable)
        pin = self._make_exec(self.tmp / "pinned" / "claude")
        self.cfg.claude_bin = str(pin)
        seen = self._seen(lambda: dashboard._run_claude_agents(self.cfg))
        self.assertEqual(seen[0][0][0], str(pin))

    def test_without_copy_or_pin_the_query_resolves_via_path(self):
        on_path = self._make_exec(self.tmp / "opt" / "claude")
        with mock.patch.object(dashboard.config.shutil, "which", return_value=str(on_path)):
            seen = self._seen(lambda: dashboard._run_claude_agents(self.cfg))
        self.assertEqual(seen[0][0][0], str(on_path))

    def test_build_dashboard_passes_its_cfg_to_the_roster_query(self):
        pin = self._make_exec(self.tmp / "pinned" / "claude")
        self.cfg.claude_bin = str(pin)
        seen = self._seen(lambda: dashboard.build_dashboard(reqs=[], agents=None, cfg=self.cfg))
        self.assertEqual([argv for argv, _ in seen], [[str(pin), "agents", "--json", "--all"]])

    def test_env_carries_disable_autoupdater_and_the_rest_of_the_environment(self):
        self._make_exec(self.stable)
        with mock.patch.dict(os.environ, {"ROSTER_PROBE_MARKER": "kept"}):
            seen = self._seen(lambda: dashboard._run_claude_agents(self.cfg))
        env = seen[0][1]["env"]
        self.assertEqual(env["DISABLE_AUTOUPDATER"], "1")
        self.assertEqual(env["ROSTER_PROBE_MARKER"], "kept")

    def test_disable_autoupdater_is_forced_not_defaulted(self):
        self._make_exec(self.stable)
        with mock.patch.dict(os.environ, {"DISABLE_AUTOUPDATER": "0"}):
            seen = self._seen(lambda: dashboard._run_claude_agents(self.cfg))
        self.assertEqual(seen[0][1]["env"]["DISABLE_AUTOUPDATER"], "1")

    def test_unspawnable_binary_yields_an_empty_roster(self):
        self.cfg.claude_bin = str(self.tmp / "nope" / "claude")
        self.assertEqual(dashboard._run_claude_agents(self.cfg), [])


class NoBareClaudeArgvLeftTestCase(unittest.TestCase):
    def test_act_and_server_carry_no_bare_claude_argv(self):
        repo = Path(__file__).resolve().parents[1]
        offenders = []
        for sub in ("act", "server"):
            for py in (repo / sub).rglob("*.py"):
                for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                    code = line.split("#", 1)[0]
                    if '["claude",' in code or "['claude'," in code:
                        offenders.append("%s:%d" % (py.relative_to(repo), n))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()

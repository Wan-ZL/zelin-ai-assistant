"""executor.dispatch — how the launch cwd is chosen and the launch is logged
(§4, §7 target_kind, §33 chat delivery).

- default runner (``runner=None``): ``_default_runner`` with the session name,
  i.e. ``<dispatch argv> --name <name> <scrubbed prompt>`` in the target cwd;
- ``cfg=None`` loads the sandbox config;
- chat delivery never bootstraps a repo; a missing chat target falls back to
  the default workbench (created if needed) and the prompt names THAT cwd;
- a new / empty repo target runs ``ensure_repo`` once; an existing one does
  not; ``target_kind`` is computed only when unset;
- the launch log is best-effort: an unwritable log path still dispatches.
subprocess / ensure_repo / has_remote are patched — no git, no claude.
"""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import executor, llm
from act.lib import config, registry
from act.lib.registry import Requirement, State

SECRET = "sk-ant-api03-" + "b" * 40


def _proc(rc=0, stdout="backgrounded · e88561e5\n", stderr=""):
    return subprocess.CompletedProcess(["claude"], rc, stdout=stdout, stderr=stderr)


class _Base(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.root = Path(tempfile.mkdtemp(prefix="dispatch-target-"))
        self.existing = self.root / "existing"
        self.existing.mkdir()
        (self.existing / "keep.txt").write_text("x", encoding="utf-8")
        self.cfg = config.Config()
        self.cfg.memory_inject = False
        self.cfg.default_target_repo = str(self.root / "workbench")
        self.runs = []
        for patcher in (
            mock.patch.object(executor, "has_remote", return_value=False),
            mock.patch.object(executor.notify, "notify", mock.Mock(return_value=True)),
            mock.patch.object(executor, "ensure_repo", mock.Mock()),
            mock.patch.object(executor.subprocess, "run", self._run),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run(self, argv, **kw):
        self.runs.append((list(argv), kw))
        return _proc()

    def _req(self, **kw):
        base = dict(id="R-500", title=f"派发 目标 {SECRET}", status=State.APPROVED.value,
                    target_repo=str(self.existing))
        base.update(kw)
        req = Requirement(**base)
        registry.save(req)
        return req


class DefaultRunnerTestCase(_Base):
    def test_default_runner_argv_and_cwd(self):
        req = self._req()
        executor.dispatch(req, self.cfg)
        argv, kw = self.runs[-1]
        base = llm.dispatch_argv(self.cfg)
        self.assertEqual(argv[:len(base)], base)
        self.assertEqual(argv[len(base):len(base) + 2],
                         ["--name", executor.session_name(req)])
        self.assertTrue(argv[-1].startswith("# Requirement R-500:"))
        self.assertNotIn(SECRET, argv[-1])            # scrubbed outbound copy
        self.assertEqual(kw["cwd"], str(self.existing))
        self.assertEqual(kw["timeout"], 120)
        self.assertEqual(registry.load("R-500").status, State.EXECUTING.value)

    def test_cfg_none_loads_config(self):
        req = self._req()
        executor.dispatch(req)
        self.assertEqual(len(self.runs), 1)
        self.assertEqual(registry.load("R-500").status, State.EXECUTING.value)


class TargetResolutionTestCase(_Base):
    def test_existing_target_skips_bootstrap_and_records_kind(self):
        req = self._req()
        executor.dispatch(req, self.cfg)
        executor.ensure_repo.assert_not_called()
        self.assertEqual(registry.load("R-500").target_kind, "existing")

    def test_missing_repo_target_is_bootstrapped(self):
        target = self.root / "brand-new"
        req = self._req(target_repo=str(target))
        executor.dispatch(req, self.cfg)
        executor.ensure_repo.assert_called_once_with(target, self.cfg)
        self.assertEqual(registry.load("R-500").target_kind, "new")
        self.assertEqual(self.runs[-1][1]["cwd"], str(target))

    def test_recorded_kind_is_not_recomputed_but_emptiness_still_bootstraps(self):
        empty = self.root / "emptied"
        empty.mkdir()
        req = self._req(target_repo=str(empty), target_kind="existing")
        executor.dispatch(req, self.cfg)
        executor.ensure_repo.assert_called_once_with(empty, self.cfg)   # dir is empty now
        self.assertEqual(registry.load("R-500").target_kind, "existing")   # never rewritten

    def test_chat_delivery_never_bootstraps(self):
        req = self._req(delivery_mode="chat", target_repo=str(self.existing))
        executor.dispatch(req, self.cfg)
        executor.ensure_repo.assert_not_called()
        self.assertEqual(self.runs[-1][1]["cwd"], str(self.existing))

    def test_chat_delivery_with_missing_target_falls_back_to_the_workbench(self):
        req = self._req(delivery_mode="chat", target_repo=str(self.root / "gone"))
        workbench = self.cfg.target_repo_path
        self.assertFalse(workbench.exists())
        executor.dispatch(req, self.cfg)
        argv, kw = self.runs[-1]
        self.assertTrue(workbench.is_dir())               # created for claude's cwd
        self.assertEqual(kw["cwd"], str(workbench))
        self.assertIn(f"Work from the directory at {workbench}.", argv[-1])
        executor.ensure_repo.assert_not_called()

    def test_chat_fallback_survives_an_uncreatable_workbench(self):
        blocker = self.root / "blocker"
        blocker.write_text("x", encoding="utf-8")
        self.cfg.default_target_repo = str(blocker / "child")   # mkdir → NotADirectoryError
        req = self._req(delivery_mode="chat", target_repo=str(self.root / "gone"))
        executor.dispatch(req, self.cfg)                 # still launches
        self.assertEqual(self.runs[-1][1]["cwd"], str(blocker / "child"))

    def test_no_target_repo_uses_the_workbench(self):
        req = self._req(target_repo=None)
        executor.dispatch(req, self.cfg)
        self.assertEqual(self.runs[-1][1]["cwd"], str(self.cfg.target_repo_path))


class RetryWindowTestCase(_Base):
    def test_attempts_without_a_timestamp_do_not_back_off(self):
        # a hand-edited / partially written card: attempts recorded, no
        # last_dispatch_attempt_at → nothing to measure against → launch
        req = self._req(execution={"dispatch_attempts": 2})
        executor.dispatch(req, self.cfg)
        self.assertEqual(registry.load("R-500").status, State.EXECUTING.value)


class LaunchLogTestCase(_Base):
    def test_log_records_streams_and_cwd(self):
        req = self._req()
        executor.dispatch(req, self.cfg)
        log = Path(registry.load("R-500").execution["log"])
        text = log.read_text(encoding="utf-8")
        self.assertIn("# dispatch R-500 (R-500) @ ", text)
        self.assertIn(f"# cwd={self.existing}", text)
        self.assertIn("=== STDOUT ===\nbackgrounded · e88561e5", text)

    def test_unwritable_log_path_does_not_block_the_dispatch(self):
        logs = self.root / "logs"
        (logs / "R-500.log").mkdir(parents=True)        # a DIRECTORY where the log goes
        req = self._req()
        with mock.patch.object(config, "LOG_DIR", logs):
            executor.dispatch(req, self.cfg)
        saved = registry.load("R-500")
        self.assertEqual(saved.status, State.EXECUTING.value)
        self.assertEqual(saved.execution["log"], str(logs / "R-500.log"))


if __name__ == "__main__":
    unittest.main()

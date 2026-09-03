"""TELEMETRY 红线：analytics 事件里没有原始报错文本（issue #37；docs/TELEMETRY.md、CONTRACT §15）。

``dispatch_failed`` / ``rework_failed`` / ``stop_failed`` / ``telemetry_sync`` used to
upload ``error=<raw stderr excerpt>[:120]`` at level=basic — free text that can carry
paths, hostnames or values. They now carry only the §25 classification id
(``failure_id``, whole key absent when honestly unknown) or an exception class name.

Pinned here:
  - behavior: a launch that dies with an auth error emits ``dispatch_failed`` with
    ``failure_id == "claude_auth_failed"`` and no ``error`` key; the raw text still
    lands in the LOCAL ledger (``execution.last_error``) and the dispatch log;
  - ``rework_failed`` (both launch sites share ``_rework_abort``'s shape) the same;
  - ``telemetry_sync`` carries ``error_type`` (exception class) + ``failure_id``,
    never ``str(exc)``;
  - **privacy lint**: no ``log_event`` / ``log_first`` call anywhere under act/ or
    server/ passes an ``error=`` keyword — the AST scan is the machine-checked half
    of the TELEMETRY.md promise (plan §5.3 A「privacy lint」).
"""
import ast
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import executor
from act.lib import analytics, config, registry, secrets
from act.lib import analytics_sync as sync
from act.lib.registry import Requirement, State

REPO = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("act", "server")
EMITTERS = {"log_event", "log_first"}


def _events(req_id):
    return [e for e in analytics.read_events() if e.get("req") == req_id]


def _proc(rc, stdout="", stderr=""):
    return subprocess.CompletedProcess(["claude"], rc, stdout=stdout, stderr=stderr)


class NoRawErrorInTelemetryTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        self.target = Path(tempfile.mkdtemp(prefix="telemetry-err-"))
        (self.target / "keep.txt").write_text("x", encoding="utf-8")
        self.cfg = config.Config()
        self.cfg.memory_inject = False
        for patcher in (
            mock.patch.object(executor, "has_remote", return_value=False),
            mock.patch.object(executor.notify, "notify", new=mock.Mock(return_value=True)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_dispatch_failed_carries_failure_id_not_stderr(self):
        req = Requirement(id="R-9371", title="telemetry #37", status=State.APPROVED.value,
                          target_repo=str(self.target))
        registry.save(req)
        stderr = "Invalid API key · /Users/someone/.config/anthropic-key.txt"
        with self.assertRaises(executor.DispatchError):
            executor.dispatch(req, self.cfg, runner=mock.Mock(return_value=_proc(1, stderr=stderr)))
        (ev,) = [e for e in _events("R-9371") if e.get("event") == "dispatch_failed"]
        self.assertEqual(ev.get("failure_id"), "claude_auth_failed")
        self.assertNotIn("error", ev)
        self.assertNotIn("/Users/", str(ev))
        # the full text stays on the machine where it is useful
        self.assertIn("Invalid API key", registry.load("R-9371").execution.get("last_error", ""))

    def test_unclassified_dispatch_failure_omits_failure_id_entirely(self):
        req = Requirement(id="R-9372", title="telemetry #37", status=State.APPROVED.value,
                          target_repo=str(self.target))
        registry.save(req)
        with self.assertRaises(executor.DispatchError):
            executor.dispatch(req, self.cfg,
                              runner=mock.Mock(return_value=_proc(1, stderr="something odd happened")))
        (ev,) = [e for e in _events("R-9372") if e.get("event") == "dispatch_failed"]
        self.assertNotIn("failure_id", ev)      # honestly unknown → key absent
        self.assertNotIn("error", ev)

    def test_rework_failed_carries_failure_id_not_text(self):
        req = Requirement(id="R-9373", title="telemetry #37", status=State.REVIEW.value)
        registry.save(req)
        executor._rework_abort(req, {}, "connection refused while reaching api.example.com")
        (ev,) = [e for e in _events("R-9373") if e.get("event") == "rework_failed"]
        self.assertEqual(ev.get("failure_id"), "network_error")
        self.assertNotIn("error", ev)
        self.assertNotIn("example.com", str(ev))

    def test_telemetry_sync_event_has_class_name_not_message(self):
        # same fixture shape as tests/test_analytics_sync.py: consent marker
        # present, a service key pinned, one pending event, a transport that dies
        secrets.write_secret(sync.SUPABASE_SERVICE_KEY_FILE, "test-service-key")
        sync.CONSENT_MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
        sync.CONSENT_MARKER_PATH.write_text("2026-07-09T00:00:00Z\n", encoding="utf-8")
        for path in (analytics.EVENTS_PATH, sync.CURSOR_PATH):
            if path.exists():
                path.unlink()
        analytics.reset_feature_gate_cache()
        self.addCleanup(analytics.reset_feature_gate_cache)
        cfg = config.Config()
        cfg.telemetry_enabled = True
        cfg.telemetry_supabase_url = "https://example.supabase.co"
        analytics.log_event("probe", req="R-9374")

        def boom(rows):
            raise OSError("[Errno 61] Connection refused: https://abc.supabase.co/rest/v1")

        stats = sync.sync_once(cfg=cfg, transport=boom)
        self.assertFalse(stats["ok"])
        self.assertIn("Connection refused", stats["error"])   # local stats keep the text
        (ev,) = [e for e in analytics.read_events() if e.get("event") == "telemetry_sync"]
        self.assertNotIn("error", ev)
        self.assertEqual(ev.get("error_type"), "OSError")
        self.assertEqual(ev.get("failure_id"), "network_error")
        self.assertNotIn("supabase.co", str(ev))

    def test_privacy_lint_no_emitter_passes_error_keyword(self):
        offenders = []
        for d in SCAN_DIRS:
            for path in sorted((REPO / d).rglob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    fn = node.func
                    name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
                    if name in EMITTERS and any(k.arg == "error" for k in node.keywords):
                        offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")
        self.assertEqual(offenders, [],
                         "analytics emitters must not upload raw error text — use failure_id "
                         "(failures.classify) or an identifier-shaped field; see docs/TELEMETRY.md")


if __name__ == "__main__":
    unittest.main()

"""dashboard helper edge paths (CONTRACT §2 / §40 / §48 / §51) — the branches
the lane fixtures never reach: the ``claude agents --json --all`` roster
reader's every failure shape, the atomic ``write_dashboard`` rename, trash
``purge_at`` for retention-off / non-ISO-but-strptime-able timestamps, the
dependency-shaped ``queued_reason`` (blocked_by, T-26 not yet legislated but
the wire shape is), and ``radar_sources`` when config / health / the enabled
probe themselves blow up (never raises, falls back honestly).

Characterization net for the P3a CRAP refactor: every assertion here was
recorded against the pre-refactor projection.
"""
import datetime as _dt
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import config, dashboard, radar_health, secrets, sources
from act.lib.registry import Requirement


def _proc(rc=0, stdout=""):
    return subprocess.CompletedProcess(["claude"], rc, stdout=stdout, stderr="")


class RunClaudeAgentsTestCase(unittest.TestCase):
    def _run(self, **kw):
        with mock.patch.object(dashboard.subprocess, "run", **kw) as run:
            out = dashboard._run_claude_agents()
        run.assert_called_once()
        return out

    def test_oserror_and_subprocess_error_give_empty(self):
        self.assertEqual(self._run(side_effect=OSError("no claude")), [])
        self.assertEqual(self._run(side_effect=subprocess.TimeoutExpired("c", 30)), [])

    def test_nonzero_or_blank_stdout_gives_empty(self):
        self.assertEqual(self._run(return_value=_proc(1, "[]")), [])
        self.assertEqual(self._run(return_value=_proc(0, "   \n")), [])

    def test_bad_json_gives_empty(self):
        self.assertEqual(self._run(return_value=_proc(0, "{not json")), [])

    def test_dict_wrappers_are_unwrapped_in_key_order(self):
        payload = {"sessions": [{"id": "b"}], "agents": [{"id": "a"}]}
        self.assertEqual(self._run(return_value=_proc(0, json.dumps(payload))),
                         [{"id": "a"}])
        self.assertEqual(self._run(return_value=_proc(0, '{"items": [1]}')), [1])
        self.assertEqual(self._run(return_value=_proc(0, '{"data": [2]}')), [2])

    def test_dict_without_list_key_gives_empty(self):
        self.assertEqual(self._run(return_value=_proc(0, '{"agents": {}}')), [])

    def test_list_passthrough_and_scalar_rejected(self):
        self.assertEqual(self._run(return_value=_proc(0, '[{"id": "x"}]')), [{"id": "x"}])
        self.assertEqual(self._run(return_value=_proc(0, '"str"')), [])


class WriteDashboardTestCase(unittest.TestCase):
    def test_writes_atomically_and_returns_dict(self):
        target = Path(tempfile.mkdtemp(prefix="dash-write-")) / "sub" / "dashboard.json"
        dash = {"generated_at": "x", "when": _dt.date(2026, 9, 2),
                "other": Path("/p")}
        out = dashboard.write_dashboard(dash, path=target)
        self.assertIs(out, dash)
        self.assertFalse(target.with_suffix(".json.tmp").exists())
        loaded = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(loaded["when"], "2026-09-02")   # date -> ISO string
        self.assertEqual(loaded["other"], "/p")           # anything else -> str

    def test_builds_when_no_dash_given(self):
        target = Path(tempfile.mkdtemp(prefix="dash-write-")) / "dashboard.json"
        with mock.patch.object(dashboard, "build_dashboard",
                               return_value={"counts": {}}) as build:
            out = dashboard.write_dashboard(path=target)
        build.assert_called_once_with()
        self.assertEqual(out, {"counts": {}})
        self.assertTrue(target.exists())


class PurgeAtTestCase(unittest.TestCase):
    def _req(self, **kw):
        return Requirement.from_dict({"id": "P-1", "title": "t", "status": "trashed", **kw})

    def test_retention_off_or_pinned_or_missing_is_none(self):
        cfg = config.Config()
        cfg.trash_retention_days = 0
        self.assertIsNone(dashboard._purge_at(
            self._req(trashed_at="2026-08-01T10:00:00Z"), cfg))
        cfg.trash_retention_days = 30
        self.assertIsNone(dashboard._purge_at(
            self._req(trashed_at="2026-08-01T10:00:00Z", permanent=True), cfg))
        self.assertIsNone(dashboard._purge_at(self._req(trashed_at=None), cfg))

    def test_strptime_fallback_accepts_unpadded_fields(self):
        cfg = config.Config()
        cfg.trash_retention_days = 1
        # fromisoformat rejects unpadded month/day; the strptime fallback
        # (mirroring actd._parse_iso) still reads it, as UTC.
        self.assertEqual(dashboard._purge_at(self._req(trashed_at="2026-8-1T10:00:00Z"), cfg),
                         "2026-08-02T10:00:00Z")
        self.assertIsNone(dashboard._purge_at(self._req(trashed_at="garbage"), cfg))

    def test_offset_aware_is_normalised_to_utc(self):
        cfg = config.Config()
        cfg.trash_retention_days = 1
        self.assertEqual(dashboard._purge_at(
            self._req(trashed_at="2026-08-01T10:00:00+02:00"), cfg),
            "2026-08-02T08:00:00Z")


class QueuedReasonViewTestCase(unittest.TestCase):
    def setUp(self):
        self.req = Requirement.from_dict({"id": "R-1", "title": "t", "status": "approved"})

    def test_dependency_with_and_without_blocking_id(self):
        self.assertEqual(
            dashboard._queued_reason_view(self.req, {"blocked_by": ["R-9", "R-8"]}),
            {"kind": "waiting_card", "blocking_id": "R-9"})
        self.assertEqual(
            dashboard._queued_reason_view(self.req, {"blocked_by": "R-9"}),
            {"kind": "waiting_card"})

    def test_concurrency_and_none(self):
        self.assertEqual(
            dashboard._queued_reason_view(self.req, {"running": 2, "max_concurrent": 2}),
            {"kind": "concurrency"})
        self.assertIsNone(
            dashboard._queued_reason_view(self.req, {"running": 1, "max_concurrent": 2}))


class RadarSourcesFailureTestCase(unittest.TestCase):
    def test_bad_config_falls_back_to_passed_cfg(self):
        cfg = config.Config()
        with mock.patch.object(dashboard.config, "load_config",
                               side_effect=RuntimeError("bad yaml")), \
                mock.patch.object(radar_health, "load_radar_health", return_value={}):
            out = dashboard._radar_sources(cfg)
        self.assertEqual(set(out), set(sources.SOURCES))
        for entry in out.values():
            self.assertEqual(set(entry), {"enabled", "last_ok", "skip_reason", "stale",
                                          "last_attempt", "test_round",
                                          "intent", "secret_present"})   # §48.7 / §48.4 add-only

    def test_bad_health_file_and_enabled_probe_never_raise(self):
        cfg = config.Config()
        # §48.4 意愿信号读 overrides / secrets：钉到空目录，别吃共享沙箱里其它
        # 判例落下的凭证（判例 tests/test_dashboard_source_intent.py 管信号本身）
        empty = Path(tempfile.mkdtemp(prefix="dash-edge-"))
        with mock.patch.object(dashboard.config, "load_config", return_value=cfg), \
                mock.patch.object(dashboard.config, "SETTINGS_OVERRIDES_PATH",
                                  empty / "settings_overrides.json"), \
                mock.patch.object(secrets, "SECRETS_DIR", empty / "secrets"), \
                mock.patch.object(radar_health, "load_radar_health",
                                  side_effect=ValueError("corrupt")), \
                mock.patch.object(sources, "enabled", side_effect=KeyError("x")):
            out = dashboard._radar_sources(cfg)
        for entry in out.values():
            self.assertEqual(entry, {"enabled": False, "last_ok": None,
                                     "skip_reason": None, "stale": False,
                                     "last_attempt": None, "test_round": None,
                                     "intent": False, "secret_present": False})

    def test_non_dict_health_payload_is_ignored(self):
        cfg = config.Config()
        with mock.patch.object(dashboard.config, "load_config", return_value=cfg), \
                mock.patch.object(radar_health, "load_radar_health", return_value=["x"]):
            out = dashboard._radar_sources(cfg)
        self.assertEqual(out["gmail"]["last_ok"], None)


class SmallHelpersTestCase(unittest.TestCase):
    def test_today_is_a_date(self):
        self.assertIsInstance(dashboard._today(), _dt.date)

    def test_days_left(self):
        with mock.patch.object(dashboard, "_today", return_value=_dt.date(2026, 9, 2)):
            self.assertEqual(dashboard.days_left("2026-09-05"), 3)
            self.assertIsNone(dashboard.days_left(None))
            self.assertIsNone(dashboard.days_left("nope"))

    def test_dir_is_nonempty_oserror(self):
        class Boom(type(Path())):
            def exists(self):
                raise OSError("denied")
        self.assertFalse(dashboard._dir_is_nonempty(Boom("/nowhere")))

    def test_index_agents_skips_non_dicts(self):
        idx = dashboard._index_agents(["x", {"id": "a1"}])
        self.assertEqual(set(idx), {"a1"})

    def test_transcript_sig_oserror_returns_none(self):
        with mock.patch.object(dashboard.Path, "glob", side_effect=OSError("io")):
            self.assertIsNone(dashboard._transcript_sig("abcdefgh-1234"))

    def test_merge_suggestions_unreadable_dir(self):
        with mock.patch.object(dashboard.Path, "glob", side_effect=OSError("io")):
            self.assertEqual(dashboard._merge_suggestions(Path("/nowhere")), [])

    def test_fold_receipts_registry_failure_leaves_title_blank(self):
        from act.lib import fold_receipts, registry
        with mock.patch.object(fold_receipts, "load_recent",
                               return_value=[{"req": "R-1", "channel": "radar"}]), \
                mock.patch.object(registry, "load", side_effect=RuntimeError("db")):
            out = dashboard._fold_receipts()
        self.assertEqual(out, [{"req": "R-1", "channel": "radar", "title": ""}])

    def test_steers_view_skips_pending_without_ts(self):
        from act.lib import steer
        req = Requirement.from_dict({"id": "R-1", "title": "t", "status": "executing"})
        with mock.patch.object(steer, "delivered_entries", return_value=[]), \
                mock.patch.object(steer, "pending_steers",
                                  return_value=[{"text": "a"}, {"text": "b", "ts": "T"}]):
            out = dashboard._steers_view(req)
        self.assertEqual(out, [{"text": "b", "ts": "T", "status": "queued",
                                "delivered_at": None}])

    def test_build_dashboard_defaults_load_from_registry(self):
        with mock.patch.object(dashboard, "load_all", return_value=[]) as la, \
                mock.patch.object(dashboard, "load_archived", return_value=[]) as lr, \
                mock.patch.object(dashboard, "_run_claude_agents", return_value=[]) as ra:
            dash = dashboard.build_dashboard()
        la.assert_called_once()
        lr.assert_called_once()
        ra.assert_called_once()
        self.assertEqual(dash["counts"]["running"], 0)


if __name__ == "__main__":
    unittest.main()

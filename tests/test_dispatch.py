"""executor.dispatch — launch failures must never enter EXECUTING (P0-6).

Same injectable-runner pattern as test_harvest_delivery / test_inverse_actions:
every test runs inside the sandbox AIASSISTANT_HOME (tests/__init__.py), the
runner is a stub CompletedProcess factory, and notify/has_remote/roster lookups
are mocked so nothing real is launched, queried, or notified.

Contract covered:
- clean launch -> status=executing, session_id captured, dispatch event;
- non-zero exit / runner exception / rc=0-but-no-session-id -> DispatchError,
  status STAYS approved, execution.last_error/last_error_at recorded (rework()
  shape -> queued card's dispatch_error), dispatch_failed event, notify once
  per failure streak;
- retry backoff (30s·2^attempts, cap 600s) skips the relaunch while open and
  survives actd's last_error clearing via dispatch_attempts/
  last_dispatch_attempt_at; a later success clears all failure bookkeeping;
- _newest_session_for_cwd(after=...) only claims sessions started AFTER the
  dispatch stamp — unknown-age or older same-cwd sessions are never adopted;
- execution.skip_permissions (P0-10) toggles --dangerously-skip-permissions in
  the argv of all three launch sites (shared _bg_base_cmd).
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, executor
from act.lib import analytics, config, registry
from act.lib.registry import Requirement, State


def _proc(rc: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["claude"], rc, stdout=stdout, stderr=stderr)


def _events(req_id: str) -> list:
    return [e for e in analytics.read_events() if e.get("req") == req_id]


class DispatchBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        # existing non-empty target dir -> target_kind=existing, ensure_repo skipped
        self.target = Path(tempfile.mkdtemp(prefix="dispatch-target-"))
        (self.target / "keep.txt").write_text("x", encoding="utf-8")
        self.cfg = config.Config()
        self.cfg.memory_inject = False  # don't read the real MEMORY.md
        for patcher in (
            mock.patch.object(executor, "has_remote", return_value=False),
            mock.patch.object(executor.notify, "notify",
                              new=mock.Mock(return_value=True)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.notified = executor.notify.notify

    def _mk_req(self, req_id: str) -> Requirement:
        req = Requirement(id=req_id, title="派发失败路径测试",
                          status=State.APPROVED.value,
                          target_repo=str(self.target))
        registry.save(req)
        return req


# --------------------------------------------------------------------------- #
# success — unchanged happy path
# --------------------------------------------------------------------------- #
class DispatchSuccessTestCase(DispatchBase):
    def test_clean_launch_sets_executing_and_session_id(self):
        req = self._mk_req("R-950")
        runner = mock.Mock(return_value=_proc(0, stdout="backgrounded · e88561e5\n"))
        out = executor.dispatch(req, self.cfg, runner=runner)
        self.assertEqual(out.status, State.EXECUTING.value)

        saved = registry.load("R-950")
        ex = saved.execution or {}
        self.assertEqual(saved.status, State.EXECUTING.value)
        self.assertEqual(ex.get("session_id"), "e88561e5")
        self.assertTrue(ex.get("dispatched_at"))
        self.assertNotIn("last_error", ex)
        log = Path(ex["log"])
        self.assertTrue(log.exists())
        self.assertIn("backgrounded", log.read_text(encoding="utf-8"))
        self.notified.assert_not_called()
        self.assertTrue(any(e.get("event") == "dispatch"
                            for e in _events("R-950")))


# --------------------------------------------------------------------------- #
# launch failure — stays approved, error recorded, DispatchError raised
# --------------------------------------------------------------------------- #
class DispatchFailureTestCase(DispatchBase):
    def _assert_failed_shape(self, req_id: str, err_fragment: str, reason: str):
        saved = registry.load(req_id)
        ex = saved.execution or {}
        self.assertEqual(saved.status, State.APPROVED.value)  # NOT executing
        self.assertNotIn("session_id", ex)
        self.assertIn(err_fragment, ex.get("last_error", ""))
        self.assertTrue(ex.get("last_error_at"))              # rework() shape
        self.assertEqual(int(ex.get("dispatch_attempts", 0)), 1)
        self.assertTrue(ex.get("last_dispatch_attempt_at"))
        self.assertTrue(any(e.get("event") == "dispatch_failed"
                            and e.get("reason") == reason
                            for e in _events(req_id)))
        self.notified.assert_called_once()

    def test_nonzero_exit_stays_approved(self):
        req = self._mk_req("R-951")
        runner = mock.Mock(return_value=_proc(1, stderr="Invalid API key"))
        with self.assertRaises(executor.DispatchError) as cm:
            executor.dispatch(req, self.cfg, runner=runner)
        self.assertIn("Invalid API key", str(cm.exception))
        self._assert_failed_shape("R-951", "Invalid API key", "launch_failed")
        # the dispatch log still captured the output for debugging
        log_text = (config.LOG_DIR / "R-951.log").read_text(encoding="utf-8")
        self.assertIn("Invalid API key", log_text)

    def test_runner_oserror_stays_approved(self):
        req = self._mk_req("R-952")
        runner = mock.Mock(side_effect=FileNotFoundError("claude not on PATH"))
        with self.assertRaises(executor.DispatchError):
            executor.dispatch(req, self.cfg, runner=runner)
        self._assert_failed_shape("R-952", "claude not on PATH", "launch_failed")

    def test_no_session_id_captured_stays_approved(self):
        req = self._mk_req("R-953")
        runner = mock.Mock(return_value=_proc(0, stdout="launched, no id printed"))
        roster = mock.Mock(return_value=None)
        with mock.patch.object(executor, "_newest_session_for_cwd", roster):
            with self.assertRaises(executor.DispatchError):
                executor.dispatch(req, self.cfg, runner=runner)
        self._assert_failed_shape("R-953", "no session id", "no_session_id")
        # the roster fallback was consulted, gated on the dispatch timestamp
        args, kwargs = roster.call_args
        self.assertEqual(args[0], str(self.target))
        self.assertIsNotNone(kwargs.get("after"))


# --------------------------------------------------------------------------- #
# retry backoff — no relaunch while the window is open; success clears it
# --------------------------------------------------------------------------- #
class DispatchBackoffTestCase(DispatchBase):
    def test_backoff_skips_relaunch_then_success_clears_bookkeeping(self):
        req = self._mk_req("R-954")
        with self.assertRaises(executor.DispatchError):
            executor.dispatch(req, self.cfg,
                              runner=mock.Mock(return_value=_proc(1, stderr="boom")))

        # next daemon pass (~10s later, inside the 60s window): launch skipped,
        # the STORED error is re-raised verbatim, no second notification.
        runner2 = mock.Mock()
        with self.assertRaises(executor.DispatchError) as cm:
            executor.dispatch(registry.load("R-954"), self.cfg, runner=runner2)
        runner2.assert_not_called()
        self.assertIn("boom", str(cm.exception))
        self.assertEqual(self.notified.call_count, 1)  # streak notifies once

        # window elapsed -> the retry really launches and succeeds, and every
        # piece of failure bookkeeping is gone from execution.
        stale = registry.load("R-954")
        ex = dict(stale.execution or {})
        ex["last_dispatch_attempt_at"] = "2026-01-01T00:00:00Z"
        stale.execution = ex
        registry.save(stale)
        runner3 = mock.Mock(return_value=_proc(0, stdout="backgrounded · deadbeef"))
        out = executor.dispatch(registry.load("R-954"), self.cfg, runner=runner3)
        runner3.assert_called_once()
        self.assertEqual(out.status, State.EXECUTING.value)
        final = registry.load("R-954").execution or {}
        self.assertEqual(final.get("session_id"), "deadbeef")
        for key in ("last_error", "last_error_at",
                    "dispatch_attempts", "last_dispatch_attempt_at"):
            self.assertNotIn(key, final)


# --------------------------------------------------------------------------- #
# _newest_session_for_cwd — only sessions started AFTER the dispatch stamp
# --------------------------------------------------------------------------- #
class NewestSessionTimeGateTestCase(unittest.TestCase):
    CWD = "/tmp/gate-target"

    def _lookup(self, entries: list, after=None):
        proc = _proc(0, stdout=json.dumps(entries))
        with mock.patch.object(executor.subprocess, "run", return_value=proc):
            return executor._newest_session_for_cwd(self.CWD, after=after)

    def test_pre_dispatch_session_is_never_claimed(self):
        entries = [{"cwd": self.CWD, "session_id": "old00001",
                    "started_at": "2026-07-08T00:00:00Z"}]
        after = executor._parse_when("2026-07-08T12:00:00Z")
        self.assertIsNone(self._lookup(entries, after=after))
        # without the gate the same roster IS claimed (legacy behaviour)
        self.assertEqual(self._lookup(entries), "old00001")

    def test_post_dispatch_session_is_claimed(self):
        entries = [
            {"cwd": self.CWD, "session_id": "old00001",
             "started_at": "2026-07-08T00:00:00Z"},
            {"cwd": self.CWD, "session_id": "new00001",
             "started_at": "2026-07-08T12:00:05Z"},
        ]
        after = executor._parse_when("2026-07-08T12:00:00Z")
        self.assertEqual(self._lookup(entries, after=after), "new00001")

    def test_unknown_age_session_is_rejected_when_gated(self):
        entries = [{"cwd": self.CWD, "session_id": "noage001"}]
        after = executor._parse_when("2026-07-08T12:00:00Z")
        self.assertIsNone(self._lookup(entries, after=after))
        self.assertEqual(self._lookup(entries), "noage001")

    def test_epoch_started_at_is_parsed(self):
        after = executor._parse_when("2026-07-08T12:00:00Z")
        late = after.timestamp() + 30
        entries = [{"cwd": self.CWD, "session_id": "epoch001",
                    "started_at": late}]
        self.assertEqual(self._lookup(entries, after=after), "epoch001")

    def test_mixed_timestamp_formats_sort_by_parsed_time_not_lexically(self):
        # roster started_at 混用 epoch 秒和 ISO 时，str 字典序把 "17…" 排在
        # "2026-…" 前面而选错"最新"会话（wrong-session binding, P0-6）——
        # 必须按 _parse_when 归一化后的 datetime 排序。
        after = executor._parse_when("2026-07-08T12:00:00Z")
        entries = [
            {"cwd": self.CWD, "session_id": "epochnew",
             "started_at": after.timestamp() + 3},          # 真正更新的会话
            {"cwd": self.CWD, "session_id": "isoolder",
             "started_at": "2026-07-08T12:00:01Z"},
        ]
        self.assertEqual(self._lookup(entries, after=after), "epochnew")
        # 无 after 门控（legacy 路径）时同样按解析后的时间取最新
        self.assertEqual(self._lookup(entries), "epochnew")


# --------------------------------------------------------------------------- #
# _parse_session_id — id 只匹配 UUID/短 hex 形态，且容忍 ANSI 色码
# --------------------------------------------------------------------------- #
class ParseSessionIdTestCase(unittest.TestCase):
    def test_short_hex_and_full_uuid_still_parse(self):
        self.assertEqual(executor._parse_session_id("backgrounded · e88561e5"),
                         "e88561e5")
        full = "e88561e5-1234-4abc-8def-0123456789ab"
        self.assertEqual(executor._parse_session_id(f"session_id: {full}"), full)
        self.assertEqual(executor._parse_session_id(f"--resume {full}"), full)

    def test_date_after_keyword_is_not_a_session_id(self):
        # 旧字符类 [0-9a-fA-F-]{5,} 会把日期吞成假 sid（"2026-07-08"），写进
        # execution 后 resume 因 short<8 拒绝、transcript 永远找不到——应返回
        # None 让 roster fallback 兜底。
        self.assertIsNone(
            executor._parse_session_id("task backgrounded: 2026-07-08 12:00:01"))

    def test_hyphenated_tail_is_not_sucked_into_the_id(self):
        # 紧跟 id 的连字符文本不再被吸进来（旧行为: "e88561e5-abc-de"）
        self.assertEqual(
            executor._parse_session_id("backgrounded · e88561e5-abc-de rest"),
            "e88561e5")

    def test_ansi_codes_between_keyword_and_id_are_tolerated(self):
        # FORCE_COLOR 下 claude 会给 keyword 和 id 分别包色码；剥掉再匹配，
        # 否则成功 launch 被判 no_session_id → 下轮重试出重复 agent。
        colored = "\x1b[1mbackgrounded\x1b[0m · \x1b[36me88561e5\x1b[0m"
        self.assertEqual(executor._parse_session_id(colored), "e88561e5")
        wrapped = "\x1b[32mbackgrounded · e88561e5\x1b[0m"
        self.assertEqual(executor._parse_session_id(wrapped), "e88561e5")

    def test_empty_or_no_match_returns_none(self):
        self.assertIsNone(executor._parse_session_id(""))
        self.assertIsNone(executor._parse_session_id("launched, no id printed"))


# --------------------------------------------------------------------------- #
# session_name — title 清洗：换行/路径分隔符/控制字符不进 --name
# (agent name 会被 claude 用作 <target>/.claude/worktrees/<name> 和分支名)
# --------------------------------------------------------------------------- #
class SessionNameSanitizeTestCase(unittest.TestCase):
    def test_newlines_and_path_separators_collapse_to_spaces(self):
        req = Requirement(id="R-X", title="fix bug\nrm -rf /tmp/x\\evil")
        name = executor.session_name(req)
        self.assertEqual(name, "R-X · fix bug rm -rf tmp x evil")
        for bad in ("\n", "/", "\\", "\t"):
            self.assertNotIn(bad, name)

    def test_traversal_title_loses_its_separators(self):
        req = Requirement(id="R-X", title="a/b/../../x")
        self.assertEqual(executor.session_name(req), "R-X · a b .. .. x")

    def test_plain_title_and_truncation_unchanged(self):
        req = Requirement(id="R-1", title="正常标题 with spaces")
        self.assertEqual(executor.session_name(req), "R-1 · 正常标题 with spaces")
        long = Requirement(id="R-2", title="t" * 100)
        self.assertEqual(executor.session_name(long), "R-2 · " + "t" * 48)
        self.assertEqual(executor.session_name(Requirement(id="R-3", title="")),
                         "R-3")


# --------------------------------------------------------------------------- #
# execution.skip_permissions (P0-10) — flag on/off toggles the argv
# --------------------------------------------------------------------------- #
class SkipPermissionsTestCase(unittest.TestCase):
    FLAG = "--dangerously-skip-permissions"

    def _default_runner_argv(self, cfg) -> list:
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return _proc(0, stdout="backgrounded · abc123ff")

        with mock.patch.object(executor.subprocess, "run", fake_run):
            executor._default_runner("prompt text", Path("/tmp"),
                                     name="R-1 · t", cfg=cfg)
        return captured["cmd"]

    def test_default_on_includes_flag(self):
        cfg = config.Config()
        self.assertTrue(cfg.skip_permissions)
        self.assertIn(self.FLAG, executor._bg_base_cmd(cfg))
        argv = self._default_runner_argv(cfg)
        self.assertIn(self.FLAG, argv)
        self.assertEqual(argv[-1], "prompt text")
        # cfg omitted entirely (resume/rework legacy callers) -> flag stays on
        self.assertIn(self.FLAG, executor._bg_base_cmd(None))

    def test_off_omits_flag(self):
        cfg = config.Config()
        cfg.skip_permissions = False
        self.assertNotIn(self.FLAG, executor._bg_base_cmd(cfg))
        argv = self._default_runner_argv(cfg)
        self.assertNotIn(self.FLAG, argv)
        # argv[0] is the RESOLVED claude (path or bare fallback), never assumed
        # to be the literal "claude" (2026-07-08 PATH-shadowing incident)
        self.assertEqual(Path(argv[0]).name, "claude")
        self.assertEqual(argv[1], "--bg")
        self.assertIn("--name", argv)

    def test_yaml_plumbing_execution_skip_permissions(self):
        config.CONFIG_PATH.write_text(
            "execution:\n  skip_permissions: false\n", encoding="utf-8")
        try:
            cfg = config.load_config()
        finally:
            config.CONFIG_PATH.unlink()  # never leak into other sandbox tests
        self.assertFalse(cfg.skip_permissions)
        self.assertNotIn(self.FLAG, executor._bg_base_cmd(cfg))


# --------------------------------------------------------------------------- #
# claude binary resolution — execution.claude_bin pin -> PATH -> ~/.local/bin
# (2026-07-08: launchd PATH ranked an outdated second install first and every
# dispatch died on "unknown option '--bg'"; a bare "claude" argv trusts PATH)
# --------------------------------------------------------------------------- #
class ClaudeBinResolutionTestCase(unittest.TestCase):
    def test_pinned_claude_bin_wins(self):
        cfg = config.Config()
        cfg.claude_bin = "/opt/pinned/claude"
        self.assertEqual(executor._bg_base_cmd(cfg)[0], "/opt/pinned/claude")

    def test_pin_expands_tilde(self):
        cfg = config.Config()
        cfg.claude_bin = "~/.local/bin/claude"
        self.assertEqual(executor._bg_base_cmd(cfg)[0],
                         str(Path.home() / ".local" / "bin" / "claude"))

    def test_default_resolves_via_path(self):
        cfg = config.Config()
        with mock.patch("act.lib.config.shutil.which",
                        return_value="/resolved/claude"):
            self.assertEqual(executor._bg_base_cmd(cfg)[0], "/resolved/claude")

    def test_missing_from_path_falls_back_to_local_bin(self):
        cfg = config.Config()
        with mock.patch("act.lib.config.shutil.which", return_value=None):
            self.assertEqual(executor._bg_base_cmd(cfg)[0],
                             str(Path.home() / ".local" / "bin" / "claude"))

    def test_yaml_plumbing_execution_claude_bin(self):
        config.CONFIG_PATH.write_text(
            "execution:\n  claude_bin: /opt/pinned/claude\n", encoding="utf-8")
        try:
            cfg = config.load_config()
        finally:
            config.CONFIG_PATH.unlink()  # never leak into other sandbox tests
        self.assertEqual(cfg.claude_bin, "/opt/pinned/claude")
        self.assertEqual(config.resolve_claude_bin(cfg), "/opt/pinned/claude")


# --------------------------------------------------------------------------- #
# actd.dispatch_approved — success-path error clearing is gated on session_id
# --------------------------------------------------------------------------- #
class DispatchApprovedClearingTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.cfg = config.Config()

    def _approved(self, req_id="R-960"):
        req = Requirement(id=req_id, title="清理条件测试",
                          status=State.APPROVED.value)
        registry.save(req)
        return req

    def test_success_with_session_clears_stale_error(self):
        # the belt-and-braces branch: a real launch happened, so a stale
        # last_error from a previous attempt must not linger on the live run
        self._approved()

        def fake_dispatch(req, cfg):
            req.execution = {"session_id": "e88561e5",
                             "last_error": "stale from a previous attempt",
                             "last_error_at": "2026-07-08T00:00:00Z"}
            req.set_status(State.EXECUTING)
            registry.save(req)
            return req

        with mock.patch.object(actd.executor, "dispatch", fake_dispatch):
            n = actd.dispatch_approved(self.cfg)
        self.assertEqual(n, 1)
        ex = registry.load("R-960").execution or {}
        self.assertEqual(ex.get("session_id"), "e88561e5")
        self.assertNotIn("last_error", ex)
        self.assertNotIn("last_error_at", ex)

    def test_non_raising_failure_without_session_keeps_error(self):
        # a dispatch that signals failure by RETURNING (no session_id, error
        # recorded) must keep its trace — the session_id gate is what frees
        # dispatch() from having to raise to protect last_error
        self._approved()

        def fake_dispatch(req, cfg):
            req.execution = {"last_error": "claude --bg exited 1",
                             "last_error_at": "2026-07-08T00:00:00Z"}
            registry.save(req)  # status untouched — stays approved
            return req

        with mock.patch.object(actd.executor, "dispatch", fake_dispatch):
            actd.dispatch_approved(self.cfg)
        saved = registry.load("R-960")
        self.assertEqual(saved.status, State.APPROVED.value)
        ex = saved.execution or {}
        self.assertEqual(ex.get("last_error"), "claude --bg exited 1")
        self.assertEqual(ex.get("last_error_at"), "2026-07-08T00:00:00Z")

    def test_raising_failure_records_error_and_stays_approved(self):
        # DispatchError: actd re-records last_error for the dashboard and keeps
        # APPROVED for retry, but does NOT emit a second dispatch_failed event
        # (executor already logged the rich one with reason/attempt).
        self._approved("R-970")
        boom = mock.Mock(side_effect=executor.DispatchError("Invalid API key"))
        with mock.patch.object(actd.executor, "dispatch", boom):
            n = actd.dispatch_approved(self.cfg)
        self.assertEqual(n, 0)
        saved = registry.load("R-970")
        self.assertEqual(saved.status, State.APPROVED.value)
        ex = saved.execution or {}
        self.assertIn("Invalid API key", ex.get("last_error", ""))
        self.assertTrue(ex.get("last_error_at"))
        self.assertFalse(any(e.get("event") == "dispatch_failed"
                             for e in _events("R-970")))

    def test_unexpected_dispatch_exception_logs_once(self):
        # Non-DispatchError crashes still get a single analytics event.
        self._approved("R-971")
        boom = mock.Mock(side_effect=RuntimeError("boom outside dispatch"))
        with mock.patch.object(actd.executor, "dispatch", boom):
            n = actd.dispatch_approved(self.cfg)
        self.assertEqual(n, 0)
        events = [e for e in _events("R-971") if e.get("event") == "dispatch_failed"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].get("reason"), "dispatch_crashed")

    def test_executor_failure_through_actd_logs_exactly_once(self):
        # End-to-end: real executor.dispatch failure emits one event; actd does not double-log.
        self._approved("R-972")
        runner = mock.Mock(return_value=_proc(1, stderr="Invalid API key"))
        real_dispatch = executor.dispatch

        def wrap(req, cfg):
            return real_dispatch(req, cfg, runner=runner)

        with mock.patch.object(actd.executor, "dispatch", wrap):
            n = actd.dispatch_approved(self.cfg)
        self.assertEqual(n, 0)
        events = [e for e in _events("R-972") if e.get("event") == "dispatch_failed"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].get("reason"), "launch_failed")


if __name__ == "__main__":
    unittest.main()

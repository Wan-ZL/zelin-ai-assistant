"""act/lib/feedback_sync.py — 建议公开跟踪表 (opted-in feedback -> GitHub issues).

Covers the feature's whole surface:
(a) publish opt-in plumbing: record_feedback stores ``publish`` add-only and
    the actd inbox action forwards the checkbox value (only an explicit JSON
    true counts) — PublishFlagTestCase;
(b) sweep no-ops silently without a token file (the gmail no-credential
    philosophy) and when features.feedback_sync is off — NoTokenTestCase;
(c) with a token (fake transport) the issue is created, number/url are written
    back atomically, and a second sweep is idempotent — SyncTestCase;
(d) issue payload: 60-char title, body carries the full text + LOCAL-timezone
    submit time + app_version + the fixed origin line + the feedback-id
    marker (storage stays UTC) — PayloadTestCase;
(e) the duplicate guard is effectively once-only — AtMostOnceTestCase:
    预写计数 (a failed pre-write sends ZERO network requests), 重试先对账
    (a lost POST response is reconciled by body marker on the next pass, no
    second POST), GET failure skips the pass instead of blindly re-posting,
    and the fresh-record fast path never lists;
(f) a 422 on create (label missing on the target repo) retries ONCE without
    the labels field — LabelRetryTestCase;
(g) failures bump sync_attempts and the record is left alone for good after
    MAX_SYNC_ATTEMPTS (API burn guard) — GiveUpTestCase;
(h) the actd run_once hook calls sweep best-effort and an exploding sweep
    never leaks out of the pass — ActdHookTestCase;
plus the config wiring for feedback_sync.repo / token_path and the
override-ONLY feedback_publish_default key (the yaml/nested spellings must
stay dead: no documented-but-unread privacy switch) — ConfigWiringTestCase.

Transports are injected/stubbed — no test ever touches the network.
Everything lives under the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import json
import tempfile
import unittest
import urllib.error
import uuid
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act import actd
from act.lib import analytics, config, feedback, feedback_sync, secrets

TOKEN_PATH = secrets.SECRETS_DIR / "github-feedback-token.txt"


def _cfg(**features) -> config.Config:
    c = config.Config()
    for name, value in features.items():
        c.features[name] = value
    return c


def _clear_dir(path) -> None:
    if path.exists():
        for p in path.glob("*"):
            p.unlink()


def _records() -> list:
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(feedback.FEEDBACK_DIR.glob("*.json"))]


def _mk_record(text: str = "把建议表公开出去", publish=True, **extra) -> dict:
    """A minimal on-disk feedback record (bypasses the upload path)."""
    record = {
        "id": uuid.uuid4().hex,
        "ts": "2026-07-20T10:11:12Z",
        "ids": [],
        "cards": [],
        "text": text,
        "app_version": "0.45.0",
        "publish": publish,
        "uploaded": True,
        "upload_attempts": 1,
    }
    record.update(extra)
    feedback._write_record(record)
    return record


class _FakeGitHub:
    """Injectable Transport double: a tiny in-memory "repo".

    POST creates an issue (kept in self.issues so a later GET lists it);
    ``lose_response = True`` simulates the half-success shape — the issue IS
    created server-side but the response comes back unparseable ({}).
    """

    def __init__(self):
        self.calls: list = []        # (method, url, payload-or-None)
        self.issues: list = []       # what GET /issues returns
        self.next_number = 101
        self.fail_with = None        # exception every POST raises
        self.fail_get_with = None    # exception every GET raises
        self.reject_labels = False   # 422 while the payload carries "labels"
        self.lose_response = False   # POST creates the issue but returns {}

    def __call__(self, method, url, payload=None):
        self.calls.append((method, url,
                           dict(payload) if payload is not None else None))
        if method == "GET":
            if self.fail_get_with is not None:
                raise self.fail_get_with
            return list(self.issues)
        if self.fail_with is not None:
            raise self.fail_with
        if self.reject_labels and "labels" in payload:
            raise urllib.error.HTTPError(url, 422, "Validation Failed",
                                         None, None)
        n = self.next_number
        self.next_number += 1
        issue = {"number": n,
                 "html_url": f"https://github.com/x/y/issues/{n}",
                 "body": str(payload.get("body") or "")}
        self.issues.append(issue)
        if self.lose_response:
            return {}   # 201 landed, body lost — what _make_transport degrades to
        return {"number": n, "html_url": issue["html_url"]}

    @property
    def posts(self) -> list:
        return [c for c in self.calls if c[0] == "POST"]

    @property
    def gets(self) -> list:
        return [c for c in self.calls if c[0] == "GET"]


class _SweepBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        feedback.FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
        _clear_dir(feedback.FEEDBACK_DIR)
        _clear_dir(config.INBOX_DIR)
        if TOKEN_PATH.exists():
            TOKEN_PATH.unlink()
        self.github = _FakeGitHub()

    def tearDown(self):
        _clear_dir(feedback.FEEDBACK_DIR)
        if TOKEN_PATH.exists():
            TOKEN_PATH.unlink()


class PublishFlagTestCase(_SweepBase):
    """(a) the publish opt-in reaches the record — add-only, explicit true."""

    def test_record_feedback_defaults_to_private(self):
        rec = feedback.record_feedback([], "老样子的建议", cfg=_cfg(),
                                       transport=lambda row: None)
        self.assertIs(rec["publish"], False)

    def test_record_feedback_stores_explicit_opt_in(self):
        rec = feedback.record_feedback([], "公开这条", cfg=_cfg(),
                                       transport=lambda row: None,
                                       publish=True)
        self.assertIs(rec["publish"], True)
        (on_disk,) = _records()
        self.assertIs(on_disk["publish"], True)

    def test_inbox_action_forwards_publish_true(self):
        (config.INBOX_DIR / "fb-pub.json").write_text(
            json.dumps({"action": "feedback", "ids": [],
                        "text": "从 App 勾选公开", "publish": True}),
            encoding="utf-8")
        with mock.patch.object(feedback, "_default_transport",
                               lambda cfg: lambda row: None):
            actd.process_inbox()
        (rec,) = _records()
        self.assertIs(rec["publish"], True)

    def test_inbox_action_only_accepts_json_true(self):
        # absent key (older app) and garbage values all stay private
        for i, payload in enumerate((
                {"action": "feedback", "text": "老 App 没这个键"},
                {"action": "feedback", "text": "字符串不算数", "publish": "true"},
                {"action": "feedback", "text": "1 也不算数", "publish": 1})):
            (config.INBOX_DIR / f"fb-{i}.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with mock.patch.object(feedback, "_default_transport",
                               lambda cfg: lambda row: None):
            actd.process_inbox()
        recs = _records()
        self.assertEqual(len(recs), 3)
        self.assertTrue(all(r["publish"] is False for r in recs))

    def test_publish_flag_never_rides_the_supabase_row(self):
        # upload bookkeeping/props stay as before — publish is local-only
        rows: list = []
        feedback.record_feedback([], "props 不带 publish", cfg=_cfg(),
                                 transport=lambda row: rows.append(row),
                                 publish=True)
        (row,) = rows
        self.assertNotIn("publish", row["props"])


class NoTokenTestCase(_SweepBase):
    """(b) no token file / feature off => silent no-op, record untouched."""

    def test_pending_record_without_token_is_left_alone(self):
        _mk_record(publish=True)
        self.assertEqual(feedback_sync.sweep(cfg=_cfg()), 0)
        (rec,) = _records()
        self.assertNotIn("issue_number", rec)
        self.assertNotIn("sync_attempts", rec)   # not even counted as a try

    def test_empty_token_file_counts_as_missing(self):
        secrets.SECRETS_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text("\n", encoding="utf-8")
        _mk_record(publish=True)
        self.assertEqual(feedback_sync.sweep(cfg=_cfg()), 0)
        (rec,) = _records()
        self.assertNotIn("issue_number", rec)

    def test_feature_flag_off_is_a_no_op_even_with_transport(self):
        _mk_record(publish=True)
        n = feedback_sync.sweep(cfg=_cfg(feedback_sync=False),
                                transport=self.github)
        self.assertEqual(n, 0)
        self.assertEqual(self.github.calls, [])

    def test_no_pending_returns_before_any_config_or_token_work(self):
        # 无 pending 零成本: with only private/synced records around, sweep
        # must not even resolve config (cfg=None would otherwise load it).
        # NB: sweep swallows everything, so a side_effect probe would pass
        # vacuously — assert on the mock's call record instead.
        _mk_record(publish=False)
        _mk_record(publish=True, issue_number=7)
        with mock.patch.object(feedback_sync.config, "load_config") as lc:
            self.assertEqual(feedback_sync.sweep(cfg=None,
                                                 transport=self.github), 0)
        lc.assert_not_called()
        self.assertEqual(self.github.calls, [])


class SyncTestCase(_SweepBase):
    """(c) create + write-back + idempotence; publish=false never syncs."""

    def test_creates_issue_and_writes_number_back(self):
        _mk_record(publish=True)
        n = feedback_sync.sweep(cfg=_cfg(), transport=self.github)
        self.assertEqual(n, 1)
        ((method, url, payload),) = self.github.calls
        self.assertEqual(method, "POST")
        self.assertEqual(
            url, "https://api.github.com/repos/Wan-ZL/zelin-ai-assistant/issues")
        self.assertEqual(payload["labels"], ["suggestion"])
        (rec,) = _records()
        self.assertEqual(rec["issue_number"], 101)
        self.assertEqual(rec["issue_url"], "https://github.com/x/y/issues/101")
        self.assertTrue(rec["issue_synced_at"])
        self.assertEqual(rec["sync_attempts"], 1)   # the 预写 counter
        self.assertNotIn("sync_error", rec)

    def test_second_sweep_is_idempotent(self):
        _mk_record(publish=True)
        feedback_sync.sweep(cfg=_cfg(), transport=self.github)
        n = feedback_sync.sweep(cfg=_cfg(), transport=self.github)
        self.assertEqual(n, 0)
        self.assertEqual(len(self.github.calls), 1)   # no duplicate issue
        (rec,) = _records()
        self.assertEqual(rec["issue_number"], 101)    # unchanged

    def test_private_records_never_sync(self):
        _mk_record(publish=False)
        _mk_record(publish=None)
        legacy = _mk_record(publish=True)
        del legacy["publish"]                     # pre-feature record shape
        feedback._write_record(legacy)
        self.assertEqual(feedback_sync.sweep(cfg=_cfg(),
                                             transport=self.github), 0)
        self.assertEqual(self.github.calls, [])

    def test_configured_repo_wins(self):
        cfg = _cfg()
        cfg.feedback_sync_repo = "someone/fork"
        _mk_record(publish=True)
        feedback_sync.sweep(cfg=cfg, transport=self.github)
        (_, url, _) = self.github.posts[0]
        self.assertEqual(url, "https://api.github.com/repos/someone/fork/issues")

    def test_default_transport_path_reads_the_token_file(self):
        # no injected transport: the token gates, _make_transport is built
        # with its content (the send itself is stubbed — no network).
        secrets.SECRETS_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text("ghp_dummy\n", encoding="utf-8")
        _mk_record(publish=True)
        made: dict = {}

        def fake_make(token):
            made["token"] = token
            return self.github

        with mock.patch.object(feedback_sync, "_make_transport", fake_make):
            n = feedback_sync.sweep(cfg=_cfg())
        self.assertEqual(n, 1)
        self.assertEqual(made["token"], "ghp_dummy")

    def test_missing_number_in_response_counts_as_failure(self):
        _mk_record(publish=True)
        n = feedback_sync.sweep(
            cfg=_cfg(), transport=lambda method, url, payload: {"ok": True})
        self.assertEqual(n, 0)
        (rec,) = _records()
        self.assertNotIn("issue_number", rec)
        self.assertEqual(rec["sync_attempts"], 1)
        self.assertIn("number", rec["sync_error"])


class PayloadTestCase(_SweepBase):
    """(d) issue title/body shape; display time is LOCAL, storage stays UTC."""

    def test_title_is_first_60_chars_collapsed(self):
        long_text = "这条建议开头有点长\n换行也要压平  多余空白合一 " + "字" * 80
        _mk_record(text=long_text, publish=True)
        feedback_sync.sweep(cfg=_cfg(), transport=self.github)
        (_, _, payload) = self.github.posts[0]
        collapsed = " ".join(long_text.split())
        self.assertEqual(payload["title"], collapsed[:60] + "…")

    def test_short_title_is_not_ellipsized(self):
        _mk_record(text="短建议", publish=True)
        feedback_sync.sweep(cfg=_cfg(), transport=self.github)
        (_, _, payload) = self.github.posts[0]
        self.assertEqual(payload["title"], "短建议")

    def test_body_carries_text_localtime_version_origin_and_marker(self):
        rec = _mk_record(text="正文要完整出现\n包括第二行", publish=True)
        feedback_sync.sweep(cfg=_cfg(), transport=self.github)
        (_, _, payload) = self.github.posts[0]
        body = payload["body"]
        self.assertIn("正文要完整出现\n包括第二行", body)
        # submit time rendered in the SYSTEM LOCAL timezone…
        local = analytics.parse_ts(rec["ts"]).astimezone() \
            .strftime("%Y-%m-%d %H:%M %Z")
        self.assertIn(local, body)
        self.assertIn("0.45.0", body)
        self.assertIn("来自 Zelin's AI Assistant 内置提建议入口", body)
        # duplicate-guard piece 2: the attribution marker rides every body
        self.assertIn(f"<!-- feedback-id: {rec['id']} -->", body)
        # …while the stored record keeps the UTC form untouched
        (on_disk,) = _records()
        self.assertEqual(on_disk["ts"], "2026-07-20T10:11:12Z")


class AtMostOnceTestCase(_SweepBase):
    """(e) duplicate guard: 预写计数 + 重试先对账 + GET 失败宁可晚发."""

    def test_prewrite_failure_sends_zero_network_requests(self):
        # scenario (b): the disk cannot even record the attempt (full/EPERM)
        # — an issue created now could never be remembered, so NOTHING may
        # be sent, or every later pass would mint another public duplicate.
        _mk_record(publish=True)
        with mock.patch.object(feedback, "_write_record",
                               side_effect=OSError("read-only fs")):
            self.assertEqual(feedback_sync.sweep(cfg=_cfg(),
                                                 transport=self.github), 0)
        self.assertEqual(self.github.calls, [])           # zero requests
        (rec,) = _records()
        self.assertNotIn("sync_attempts", rec)            # disk unchanged too

    def test_attempt_counter_is_persisted_before_the_post(self):
        # even a hard-crashing transport leaves the attempt already counted
        _mk_record(publish=True)

        def exploding(method, url, payload=None):
            (rec,) = _records()
            self.assertEqual(rec["sync_attempts"], 1)     # on disk BEFORE send
            raise OSError("boom mid-flight")

        feedback_sync.sweep(cfg=_cfg(), transport=exploding)
        (rec,) = _records()
        self.assertEqual(rec["sync_attempts"], 1)

    def test_lost_response_then_retry_reconciles_without_reposting(self):
        # scenario (a): the POST lands but its response is torn/times out —
        # pass 1 records a failure; pass 2 must find the issue by its body
        # marker and just write the number back, never POSTing again.
        rec = _mk_record(publish=True)
        self.github.lose_response = True
        self.assertEqual(feedback_sync.sweep(cfg=_cfg(),
                                             transport=self.github), 0)
        (on_disk,) = _records()
        self.assertNotIn("issue_number", on_disk)
        self.assertEqual(on_disk["sync_attempts"], 1)
        self.assertEqual(len(self.github.issues), 1)      # it DID get created

        self.github.lose_response = False
        self.assertEqual(feedback_sync.sweep(cfg=_cfg(),
                                             transport=self.github), 1)
        self.assertEqual(len(self.github.posts), 1)       # still just one POST
        self.assertEqual(len(self.github.gets), 1)        # reconciled via GET
        self.assertEqual(len(self.github.issues), 1)      # NO duplicate issue
        (on_disk,) = _records()
        self.assertEqual(on_disk["issue_number"], 101)
        self.assertEqual(on_disk["issue_url"],
                         "https://github.com/x/y/issues/101")
        self.assertNotIn("sync_error", on_disk)
        self.assertIn(f"<!-- feedback-id: {rec['id']} -->",
                      self.github.issues[0]["body"])

    def test_reconcile_get_failure_skips_the_pass_without_posting(self):
        # 宁可晚发不可重发: when the record may already have an issue and the
        # listing cannot be read, a blind POST is the one duplicating move.
        _mk_record(publish=True, sync_attempts=1)
        self.github.fail_get_with = OSError("list unreachable")
        self.assertEqual(feedback_sync.sweep(cfg=_cfg(),
                                             transport=self.github), 0)
        self.assertEqual(len(self.github.gets), 1)
        self.assertEqual(self.github.posts, [])           # never POSTed
        (rec,) = _records()
        self.assertEqual(rec["sync_attempts"], 2)
        self.assertIn("unreachable", rec["sync_error"])

    def test_fresh_record_goes_straight_to_post_without_listing(self):
        # 正常路径不受影响: no prior attempt => no reconcile round-trip
        _mk_record(publish=True)
        self.assertEqual(feedback_sync.sweep(cfg=_cfg(),
                                             transport=self.github), 1)
        self.assertEqual(self.github.gets, [])
        self.assertEqual(len(self.github.posts), 1)

    def test_reconcile_ignores_other_records_markers(self):
        # a marker hit must be THIS record's — someone else's feedback-id
        # (or a markerless issue) never satisfies the reconcile.
        self.github.issues.append(
            {"number": 7, "html_url": "https://github.com/x/y/issues/7",
             "body": "别的建议\n<!-- feedback-id: deadbeef -->"})
        _mk_record(publish=True, sync_attempts=1)
        self.assertEqual(feedback_sync.sweep(cfg=_cfg(),
                                             transport=self.github), 1)
        self.assertEqual(len(self.github.gets), 1)        # looked first
        self.assertEqual(len(self.github.posts), 1)       # then created anew
        (rec,) = _records()
        self.assertEqual(rec["issue_number"], 101)        # not 7


class LabelRetryTestCase(_SweepBase):
    """(f) 422 (label missing on the repo) => one retry without labels."""

    def test_422_retries_once_without_labels(self):
        self.github.reject_labels = True
        _mk_record(publish=True)
        n = feedback_sync.sweep(cfg=_cfg(), transport=self.github)
        self.assertEqual(n, 1)
        self.assertEqual(len(self.github.posts), 2)
        self.assertIn("labels", self.github.posts[0][2])
        self.assertNotIn("labels", self.github.posts[1][2])
        (rec,) = _records()
        self.assertEqual(rec["issue_number"], 101)

    def test_non_422_errors_do_not_trigger_the_label_retry(self):
        self.github.fail_with = urllib.error.HTTPError(
            "https://api.github.com", 401, "Bad credentials", None, None)
        _mk_record(publish=True)
        n = feedback_sync.sweep(cfg=_cfg(), transport=self.github)
        self.assertEqual(n, 0)
        self.assertEqual(len(self.github.posts), 1)   # no second attempt
        (rec,) = _records()
        self.assertEqual(rec["sync_attempts"], 1)
        self.assertIn("401", rec["sync_error"])


class GiveUpTestCase(_SweepBase):
    """(g) three failed sweeps => the record is never tried again."""

    def test_three_failures_then_hands_off(self):
        self.github.fail_with = OSError("github unreachable")
        _mk_record(publish=True)
        for expected in (1, 2, 3):
            self.assertEqual(feedback_sync.sweep(cfg=_cfg(),
                                                 transport=self.github), 0)
            (rec,) = _records()
            self.assertEqual(rec["sync_attempts"], expected)
        calls_after_three = len(self.github.calls)
        # sweep #4: at MAX_SYNC_ATTEMPTS the record is no longer pending —
        # even a now-healthy transport is not consulted (防死循环烧 API)
        self.github.fail_with = None
        self.assertEqual(feedback_sync.sweep(cfg=_cfg(),
                                             transport=self.github), 0)
        self.assertEqual(len(self.github.calls), calls_after_three)
        (rec,) = _records()
        self.assertEqual(rec["sync_attempts"], 3)
        self.assertNotIn("issue_number", rec)

    def test_success_after_a_failure_clears_the_error(self):
        self.github.fail_with = OSError("flaky")
        _mk_record(publish=True)
        feedback_sync.sweep(cfg=_cfg(), transport=self.github)
        self.github.fail_with = None
        self.assertEqual(feedback_sync.sweep(cfg=_cfg(),
                                             transport=self.github), 1)
        (rec,) = _records()
        self.assertEqual(rec["issue_number"], 101)
        self.assertEqual(rec["sync_attempts"], 2)
        self.assertNotIn("sync_error", rec)

    def test_one_bad_record_does_not_block_the_rest(self):
        # a torn json file + a good pending record: the good one still syncs
        (feedback.FEEDBACK_DIR / "torn.json").write_text("{{{ torn",
                                                         encoding="utf-8")
        _mk_record(publish=True)
        self.assertEqual(feedback_sync.sweep(cfg=_cfg(),
                                             transport=self.github), 1)


class ActdHookTestCase(_SweepBase):
    """(h) run_once calls sweep best-effort; a raising sweep never escapes."""

    def _run_once_stubbed(self):
        """actd.run_once with every heavy/networked stage stubbed out — only
        the housekeeping tail (where the feedback_sync hook lives) runs."""
        cfg = _cfg()
        with mock.patch.object(actd, "process_inbox", return_value=0), \
                mock.patch.object(actd, "dispatch_approved", return_value=0), \
                mock.patch.object(actd, "reconcile_executing"), \
                mock.patch.object(actd, "process_raising", return_value=0), \
                mock.patch.object(actd, "purge_trash"), \
                mock.patch.object(actd, "archive_stale"), \
                mock.patch.object(actd, "cleanup_merge_jobs"), \
                mock.patch.object(actd, "auto_merge", None), \
                mock.patch.object(actd, "update_check", None), \
                mock.patch.object(actd, "build_dashboard", return_value={}), \
                mock.patch.object(actd, "write_dashboard"), \
                mock.patch.object(actd, "detect_transitions", return_value=[]), \
                mock.patch.object(actd, "_check_auth_failures", return_value=[]):
            return actd.run_once(cfg, None, set(), set()), cfg

    def test_run_once_invokes_sweep_with_cfg(self):
        with mock.patch.object(feedback_sync, "sweep",
                               return_value=0) as sweep:
            _, cfg = self._run_once_stubbed()
        sweep.assert_called_once_with(cfg)

    def test_exploding_sweep_never_breaks_the_pass(self):
        with mock.patch.object(feedback_sync, "sweep",
                               side_effect=RuntimeError("boom")):
            dash, _ = self._run_once_stubbed()   # must not raise
        self.assertEqual(dash, {})

    def test_sweep_itself_swallows_internal_explosions(self):
        # defense in depth: even sweep's own guts blowing up returns 0
        _mk_record(publish=True)
        with mock.patch.object(feedback_sync, "_is_pending",
                               side_effect=RuntimeError("boom")):
            self.assertEqual(feedback_sync.sweep(cfg=_cfg(),
                                                 transport=self.github), 0)


class ConfigWiringTestCase(unittest.TestCase):
    """feedback_sync.* yaml keys + the app override keys.

    feedback_publish_default is override-ONLY (the App's checkbox memory):
    its only reader is the feedback dialog, which consults
    settings_overrides.json — so the yaml/nested spellings must stay
    non-keys. A documented-but-unread privacy switch is worse than none.
    """

    def _load(self, overrides: dict, yaml_body: str = "") -> config.Config:
        tmp = Path(tempfile.mkdtemp(prefix="cfg-fbsync-"))
        cfg_path = tmp / "config.yaml"
        cfg_path.write_text(yaml_body, encoding="utf-8")
        ov_path = tmp / "settings_overrides.json"
        ov_path.write_text(json.dumps(overrides), encoding="utf-8")
        with mock.patch.object(config, "CONFIG_PATH", cfg_path), \
                mock.patch.object(config, "SETTINGS_OVERRIDES_PATH", ov_path):
            return config.load_config()

    def test_defaults(self):
        cfg = self._load({})
        self.assertEqual(cfg.feedback_sync_repo, "Wan-ZL/zelin-ai-assistant")
        self.assertEqual(cfg.feedback_sync_token_path,
                         "config/secrets/github-feedback-token.txt")
        self.assertTrue(cfg.feedback_publish_default)
        self.assertTrue(cfg.feature("feedback_sync"))
        self.assertIn("feedback_sync", config.DEFAULT_FEATURES)

    def test_yaml_block_loads_repo_and_token_path_only(self):
        cfg = self._load({}, yaml_body=(
            "feedback_sync:\n"
            "  repo: someone/fork\n"
            "  token_path: ~/tokens/gh.txt\n"
            "  publish_default: false\n"))
        self.assertEqual(cfg.feedback_sync_repo, "someone/fork")
        self.assertEqual(cfg.feedback_sync_token_path, "~/tokens/gh.txt")
        # override-only key: the yaml spelling is deliberately NOT a knob —
        # nothing reads it, so accepting it would be a silently-dead switch
        self.assertTrue(cfg.feedback_publish_default)

    def test_flat_overrides_win_over_yaml(self):
        cfg = self._load(
            {"feedback_publish_default": False,
             "feedback_sync_repo": "override/repo",
             "feedback_sync_token_path": "elsewhere/tok.txt"},
            yaml_body="feedback_sync:\n  repo: yaml/repo\n")
        self.assertEqual(cfg.feedback_sync_repo, "override/repo")
        self.assertEqual(cfg.feedback_sync_token_path, "elsewhere/tok.txt")
        self.assertFalse(cfg.feedback_publish_default)

    def test_nested_override_form_repo_and_token_path_only(self):
        cfg = self._load({"feedback_sync": {
            "repo": "nested/repo",
            "token_path": "nested/tok.txt",
            "publish_default": False,   # non-key here too (flat key only)
        }})
        self.assertEqual(cfg.feedback_sync_repo, "nested/repo")
        self.assertEqual(cfg.feedback_sync_token_path, "nested/tok.txt")
        self.assertTrue(cfg.feedback_publish_default)

    def test_bad_values_keep_defaults(self):
        cfg = self._load({"feedback_publish_default": "maybe",
                          "feedback_sync": {"repo": "   "}})
        self.assertTrue(cfg.feedback_publish_default)   # bad value skipped
        self.assertEqual(cfg.feedback_sync_repo, "Wan-ZL/zelin-ai-assistant")

    def test_features_flag_override(self):
        cfg = self._load({"features": {"feedback_sync": False}})
        self.assertFalse(cfg.feature("feedback_sync"))

    def test_token_path_resolution_relative_and_absolute(self):
        cfg = config.Config()
        self.assertEqual(feedback_sync._token_path(cfg),
                         config.HOME / "config/secrets/github-feedback-token.txt")
        cfg.feedback_sync_token_path = "/abs/tok.txt"
        self.assertEqual(feedback_sync._token_path(cfg), Path("/abs/tok.txt"))
        cfg.feedback_sync_token_path = "~/tok.txt"
        self.assertEqual(feedback_sync._token_path(cfg),
                         Path("~/tok.txt").expanduser())


if __name__ == "__main__":
    unittest.main()

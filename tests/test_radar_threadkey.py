"""Thread-level matching enrichment (card lifecycle, work-unit B).

The radars attach an EXTERNAL thread ref to each candidate's source dict so
registry.derive_thread_key (work-unit A) can group by thread:

  - Gmail radar  -> source["gmail_thread_id"] = <X-GM-THRID>   (when available)
  - Slack radar  -> source["slack_thread_ts"] = <thread_ts>     (when threaded)

These tests pin the SPIKE result + the A↔B interface:
  - Gmail: X-GM-THRID is parsed out of the IMAP FETCH response envelope
    (added to the fetch item) and rides into the source dict.
  - Slack: thread_ts on a threaded message is captured by fetch_new_messages
    and mapped (by permalink) back into the source dict.
  - honest fallback: no external ref -> the key is OMITTED (derive_thread_key
    returns None -> title/LLM matching), never a fake/synthetic id.
  - radar._set_thread_key is guarded: it is a no-op until A's
    registry.derive_thread_key lands, so this branch's suite never depends on
    A's not-yet-merged function.

No real IMAP / Slack / claude is ever touched (fake conn + injected LLM),
running in the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import json
import shutil
import subprocess
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import radar, radar_gmail, radar_slack
from act.lib import config, registry


# --------------------------------------------------------------------------- #
# shared fakes
# --------------------------------------------------------------------------- #
def _proc(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr="")


class _FakeLLM:
    """One injectable for BOTH calls of a radar pass: extraction + triage.
    Triage prompts carry the shared gate's `入库把关` marker; everything else is
    an extraction prompt and gets the JSON-encoded extraction array."""

    def __init__(self, extraction=None, decision=None):
        self.extraction = extraction if extraction is not None else []
        self.decision = decision

    def __call__(self, prompt: str):
        if "入库把关" in prompt:
            return _proc(json.dumps(self.decision, ensure_ascii=False))
        return _proc(json.dumps(self.extraction, ensure_ascii=False))


def _clean_state():
    config.ensure_state_dirs()
    if config.REGISTRY_DIR.exists():
        shutil.rmtree(config.REGISTRY_DIR)
    if config.CONFIG_PATH.exists():
        config.CONFIG_PATH.unlink()
    for name in (radar_gmail.STATE_FILE, radar_slack.STATE_FILE):
        p = config.STATE_DIR / name
        if p.exists():
            p.unlink()


_RAW_EMAIL = (
    b"From: manager@corp.com\r\n"
    b"Subject: Q3 report\r\n"
    b"Date: Fri, 11 Jul 2026 09:00:00 +0000\r\n"
    b"Message-ID: <abc@mail>\r\n"
    b"References: <root@mail> <prev@mail>\r\n"
    b"\r\n"
    b"please send the Q3 report\r\n"
)
_THRID = "1795329334891234567"


class _FakeGmailConn:
    """Minimal IMAP conn: UID SEARCH returns one uid, UID FETCH returns the
    realistic (envelope-prefix, body-literal) tuple imaplib produces for Gmail.
    `where` places X-GM-THRID before BODY[] (prefix), after it (trailing bytes
    element), or omits it entirely (non-Gmail host)."""

    def __init__(self, where: str = "prefix"):
        self.where = where

    def uid(self, cmd, *args):
        if cmd == "search":
            return ("OK", [b"42"])
        if cmd == "fetch":
            size = len(_RAW_EMAIL)
            if self.where == "prefix":
                prefix = b"1 (X-GM-THRID %s UID 42 BODY[] {%d}" % (
                    _THRID.encode(), size)
                return ("OK", [(prefix, _RAW_EMAIL), b")"])
            if self.where == "trailing":
                prefix = b"1 (UID 42 BODY[] {%d}" % size
                trailing = b" X-GM-THRID %s)" % _THRID.encode()
                return ("OK", [(prefix, _RAW_EMAIL), trailing])
            # "none"
            prefix = b"1 (UID 42 BODY[] {%d}" % size
            return ("OK", [(prefix, _RAW_EMAIL), b")"])
        return ("NO", [])


# --------------------------------------------------------------------------- #
# Gmail — SPIKE: X-GM-THRID is retrievable from the IMAP fetch
# --------------------------------------------------------------------------- #
class GmailThreadRefTestCase(unittest.TestCase):
    def setUp(self):
        _clean_state()
        self.addCleanup(_clean_state)
        self.cfg = config.Config()
        self._orig_pw = radar_gmail.get_app_password
        radar_gmail.get_app_password = lambda cfg=None: "app-pw"
        self.addCleanup(setattr, radar_gmail, "get_app_password", self._orig_pw)

    def test_parse_gm_thrid_prefix_and_trailing_and_absent(self):
        self.assertEqual(
            radar_gmail._parse_gm_thrid([(b"1 (X-GM-THRID %s UID 42 BODY[] {5}"
                                          % _THRID.encode(), _RAW_EMAIL), b")"]),
            _THRID)
        self.assertEqual(
            radar_gmail._parse_gm_thrid([(b"1 (UID 42 BODY[] {5}", _RAW_EMAIL),
                                         b" X-GM-THRID %s)" % _THRID.encode()]),
            _THRID)
        self.assertIsNone(
            radar_gmail._parse_gm_thrid([(b"1 (UID 42 BODY[] {5}", _RAW_EMAIL),
                                         b")"]))

    def test_parse_gm_thrid_ignores_thrid_string_in_body(self):
        # a literal "X-GM-THRID 999" inside the message body must NOT be picked
        # up (we only scan the envelope, never item[1]).
        body = b"1 (UID 42 BODY[] {5}"
        self.assertIsNone(
            radar_gmail._parse_gm_thrid([(body, b"X-GM-THRID 999 in body"), b")"]))

    def test_fetch_new_messages_extracts_thrid(self):
        msgs, newest = radar_gmail.fetch_new_messages(_FakeGmailConn("prefix"), 0)
        self.assertEqual(newest, 42)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["gm_thrid"], _THRID)

    def test_fetch_new_messages_thrid_trailing_element(self):
        msgs, _ = radar_gmail.fetch_new_messages(_FakeGmailConn("trailing"), 0)
        self.assertEqual(msgs[0]["gm_thrid"], _THRID)

    def test_fetch_new_messages_no_thrid_non_gmail_host(self):
        msgs, _ = radar_gmail.fetch_new_messages(_FakeGmailConn("none"), 0)
        self.assertIsNone(msgs[0]["gm_thrid"])

    # -- scan-level source-dict population ---------------------------------- #
    def _extraction(self):
        return [{"summary": "回复 manager 的季度报告请求", "type": "comms",
                 "tier": "T1", "needs_reply": True, "plan": ["起草回复"],
                 "from": "manager@corp.com", "subject": "Q3 report",
                 "message_id": "<abc@mail>"}]

    def _scan(self, llm, gm_thrid):
        msg = {"uid": 42, "from": "manager@corp.com", "subject": "Q3 report",
               "date": "Fri, 11 Jul 2026 09:00:00 +0000",
               "message_id": "<abc@mail>", "gm_thrid": gm_thrid,
               "body": "please send the Q3 report"}
        return radar_gmail.scan(
            self.cfg, fetcher=lambda cfg, last_uid: ([msg], 42), extractor=llm)

    def test_scan_populates_gmail_thread_id(self):
        llm = _FakeLLM(extraction=self._extraction(),
                       decision={"action": "new_proposal", "confidence": "high"})
        self.assertEqual(self._scan(llm, _THRID), 1)
        (req,) = registry.load_all()
        self.assertEqual(req.sources[0]["gmail_thread_id"], _THRID)

    def test_scan_omits_gmail_thread_id_when_absent(self):
        llm = _FakeLLM(extraction=self._extraction(),
                       decision={"action": "new_proposal", "confidence": "high"})
        self.assertEqual(self._scan(llm, None), 1)
        (req,) = registry.load_all()
        self.assertNotIn("gmail_thread_id", req.sources[0])


# --------------------------------------------------------------------------- #
# Slack — SPIKE: thread_ts is retrievable on the native API path
# --------------------------------------------------------------------------- #
class SlackThreadRefTestCase(unittest.TestCase):
    def setUp(self):
        _clean_state()
        self.addCleanup(_clean_state)
        self.cfg = config.Config()
        # native path stubs: never touch the network
        self._stubs = {
            "get_token": radar_slack.get_token,
            "verify_token": radar_slack.verify_token,
            "find_self_dm": radar_slack.find_self_dm,
        }
        radar_slack.get_token = lambda cfg=None: "tok"
        radar_slack.verify_token = lambda t: {"ok": True, "user_id": "ME"}
        radar_slack.find_self_dm = lambda t, i: None
        self.addCleanup(self._restore)

    def _restore(self):
        for name, fn in self._stubs.items():
            setattr(radar_slack, name, fn)

    def test_fetch_new_messages_captures_thread_ts(self):
        def fake_api(method, token, params=None):
            if method == "conversations.list":
                return {"ok": True, "channels": [{"id": "D1", "is_mpim": False}]}
            if method == "conversations.history":
                return {"ok": True, "messages": [{
                    "ts": "1720000001.0002", "user": "U_OTHER",
                    "text": "帮我 review 一下 PR", "thread_ts": "1720000000.0001"}]}
            if method == "chat.getPermalink":
                return {"ok": True, "permalink": "https://slack/p1"}
            return {"ok": False}

        orig = radar_slack.slack_api
        radar_slack.slack_api = fake_api
        try:
            out = radar_slack.fetch_new_messages("tok", "ME", self.cfg, {},
                                                 self_dm=None)
        finally:
            radar_slack.slack_api = orig
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["thread_ts"], "1720000000.0001")
        self.assertEqual(out[0]["permalink"], "https://slack/p1")

    def _scan(self, llm, thread_ts):
        msg = {"channel": "D1", "channel_type": "im", "ts": "1720000001.0002",
               "user": "U_OTHER", "text": "帮我 review PR",
               "thread_ts": thread_ts, "permalink": "https://slack/p1"}
        return radar_slack.scan(
            self.cfg,
            fetcher=lambda token, my_id, cfg, markers: [msg],
            extractor=llm)

    def _extraction(self):
        return [{"summary": "帮 Zelin review PR", "type": "code", "tier": "T1",
                 "urgent": True, "needs_reply": False, "plan": [],
                 "permalink": "https://slack/p1"}]

    def test_scan_populates_slack_thread_ts(self):
        llm = _FakeLLM(extraction=self._extraction(),
                       decision={"action": "new_proposal", "confidence": "high"})
        self.assertEqual(self._scan(llm, "1720000000.0001"), 1)
        (req,) = registry.load_all()
        self.assertEqual(req.sources[0]["slack_thread_ts"], "1720000000.0001")

    def test_scan_omits_slack_thread_ts_when_not_threaded(self):
        llm = _FakeLLM(extraction=self._extraction(),
                       decision={"action": "new_proposal", "confidence": "high"})
        self.assertEqual(self._scan(llm, None), 1)
        (req,) = registry.load_all()
        self.assertNotIn("slack_thread_ts", req.sources[0])


# --------------------------------------------------------------------------- #
# radar._set_thread_key — guarded wiring to A's registry.derive_thread_key
# --------------------------------------------------------------------------- #
class SetThreadKeyGuardTestCase(unittest.TestCase):
    def test_calls_derive_when_present(self):
        req = registry.Requirement(id="R-1",
                                   sources=[{"gmail_thread_id": "123"}])
        orig = getattr(registry, "derive_thread_key", None)
        registry.derive_thread_key = lambda s: (
            f"gmail:{s['gmail_thread_id']}" if s.get("gmail_thread_id") else None)
        try:
            radar._set_thread_key(req)
            self.assertEqual(req.thread_key, "gmail:123")
        finally:
            if orig is None:
                del registry.derive_thread_key
            else:
                registry.derive_thread_key = orig

    def test_noop_when_absent(self):
        # When A's helper is absent, _set_thread_key must be a silent no-op:
        # no crash, thread_key left at its default (unpopulated). Post-A the
        # Requirement carries thread_key as a field defaulting to None, so the
        # "unpopulated" assertion is `is None` (before A landed it was the
        # absence of the attribute) — same intent, integrated reality.
        req = registry.Requirement(id="R-1",
                                   sources=[{"gmail_thread_id": "123"}])
        orig = getattr(registry, "derive_thread_key", None)
        if orig is not None:
            del registry.derive_thread_key
        try:
            radar._set_thread_key(req)  # must not raise
            self.assertIsNone(req.thread_key)
        finally:
            if orig is not None:
                registry.derive_thread_key = orig

    def test_never_raises_when_derive_throws(self):
        req = registry.Requirement(id="R-1", sources=[{"x": 1}])
        orig = getattr(registry, "derive_thread_key", None)

        def boom(_s):
            raise ValueError("boom")

        registry.derive_thread_key = boom
        try:
            radar._set_thread_key(req)  # swallowed
        finally:
            if orig is None:
                del registry.derive_thread_key
            else:
                registry.derive_thread_key = orig


if __name__ == "__main__":
    unittest.main()

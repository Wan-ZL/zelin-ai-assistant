"""act/radar_slack.fetch_new_messages — the native-API history walk (§13).

``slack_api`` is replaced by a scripted fake (no network). Pinned (P3
mutation net — the nested ``history`` closure had no direct test):
- DMs + group DMs come from conversations.list (mpim flag decides the type);
  the im whose counterpart is me is the self-DM inbox;
- messages at/below the channel marker are skipped; subtyped messages are
  noise EXCEPT a self-DM ``file_share``;
- my own messages: recorded as ``channel_type=self`` (with files) only in the
  self-DM, dropped elsewhere — but still advance the marker;
- in a plain channel only @mentions are kept (DMs always), non-mentions
  still advance the marker; kept messages carry thread_ts + a permalink;
- the marker per channel = newest ts seen (as a float compare, not lexical);
- watched channels accept dict ({id}) and bare-id entries, blanks skipped;
- a failed history call leaves that channel's marker untouched;
- a conversations.list failure skips the DM section entirely.
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import radar_slack
from act.lib import config

ME = "U_ME"


class _FakeSlack:
    def __init__(self, convs=None, history=None, fail_history=()):
        self.convs = convs if convs is not None else {"ok": True, "channels": []}
        self.history = history or {}
        self.fail_history = set(fail_history)
        self.calls = []

    def __call__(self, method, token, params=None):
        self.calls.append((method, dict(params or {})))
        if method == "conversations.list":
            return self.convs
        if method == "conversations.history":
            cid = params["channel"]
            if cid in self.fail_history:
                return {"ok": False, "error": "channel_not_found"}
            return {"ok": True, "messages": self.history.get(cid, [])}
        if method == "chat.getPermalink":
            return {"ok": True, "permalink": f"https://slack/{params['channel']}/p{params['message_ts']}"}
        return {"ok": False, "error": f"unexpected {method}"}


class FetchNewMessagesTestCase(unittest.TestCase):
    def _fetch(self, fake, cfg=None, markers=None):
        cfg = cfg or config.Config()
        markers = markers if markers is not None else {}
        with mock.patch.object(radar_slack, "slack_api", fake):
            out = radar_slack.fetch_new_messages("tok", ME, cfg, markers)
        return out, markers

    def test_dm_types_self_detection_and_records(self):
        fake = _FakeSlack(
            convs={"ok": True, "channels": [
                {"id": "D_SELF", "user": ME},
                {"id": "D_OTHER", "user": "U_X"},
                {"id": "G_1", "is_mpim": True},
            ]},
            history={
                "D_SELF": [
                    {"ts": "10.5", "user": ME, "text": "note to self",
                     "files": [{"id": "F1"}], "subtype": "file_share"},
                    {"ts": "10.6", "user": ME, "text": "plain self note"},
                    {"ts": "10.7", "user": ME, "subtype": "channel_join", "text": "noise"},
                ],
                "D_OTHER": [
                    {"ts": "20.0", "user": "U_X", "text": "can you review?", "thread_ts": "19.0"},
                    {"ts": "20.1", "user": ME, "text": "my reply — not captured"},
                ],
                "G_1": [
                    {"ts": "30.0", "user": "U_Y", "text": "group ask"},
                ],
            })
        out, markers = self._fetch(fake)
        self_recs = [m for m in out if m["channel_type"] == "self"]
        self.assertEqual([m["ts"] for m in self_recs], ["10.5", "10.6"])
        self.assertEqual(self_recs[0]["files"], [{"id": "F1"}])
        self.assertEqual(self_recs[1]["files"], [])
        self.assertIsNone(self_recs[0]["permalink"])
        self.assertEqual(self_recs[0]["user"], ME)
        others = [m for m in out if m["channel_type"] != "self"]
        self.assertEqual([(m["channel_type"], m["ts"]) for m in others],
                         [("im", "20.0"), ("mpim", "30.0")])
        self.assertEqual(others[0]["thread_ts"], "19.0")
        self.assertEqual(others[0]["permalink"], "https://slack/D_OTHER/p20.0")
        self.assertIsNone(others[1]["thread_ts"])
        # markers: my own + noise messages still advance (noise does not)
        self.assertEqual(markers, {"D_SELF": "10.6", "D_OTHER": "20.1", "G_1": "30.0"})

    def test_channel_mentions_only_and_marker_float_compare(self):
        cfg = config.Config()
        cfg.slack_channels = [{"id": "C_1", "name": "eng"}, "C_2", {"name": "no-id"}, ""]
        fake = _FakeSlack(history={
            "C_1": [
                {"ts": "9.0", "user": "U_A", "text": "old"},          # <= marker
                {"ts": "100.0", "user": "U_A", "text": "no mention"},
                {"ts": "99.5", "user": "U_B", "text": f"hey <@{ME}> look"},
            ],
            "C_2": [{"ts": "1.0", "user": "U_C", "text": "unrelated"}],
        })
        out, markers = self._fetch(fake, cfg=cfg, markers={"C_1": "9.5"})
        self.assertEqual([(m["channel"], m["ts"]) for m in out], [("C_1", "99.5")])
        self.assertEqual(out[0]["channel_type"], "channel")
        # newest by float value (100.0 > 99.5), not by string order
        self.assertEqual(markers["C_1"], "100.0")
        self.assertEqual(markers["C_2"], "1.0")
        history_calls = [p["channel"] for m, p in fake.calls if m == "conversations.history"]
        self.assertEqual(history_calls, ["C_1", "C_2"])   # blanks / id-less skipped
        self.assertEqual(fake.calls[0], ("conversations.list",
                                         {"types": "im,mpim", "limit": 200}))
        self.assertIn(("conversations.history", {"channel": "C_1", "oldest": "9.5", "limit": 50}),
                      fake.calls)

    def test_failed_history_leaves_marker_alone_and_failed_list_skips_dms(self):
        cfg = config.Config()
        cfg.slack_channels = ["C_BAD", "C_OK"]
        fake = _FakeSlack(convs={"ok": False, "error": "missing_scope"},
                          history={"C_OK": [{"ts": "5.0", "user": "U", "text": f"<@{ME}>"}]},
                          fail_history=["C_BAD"])
        out, markers = self._fetch(fake, cfg=cfg, markers={"C_BAD": "3.0"})
        self.assertEqual([m["channel"] for m in out], ["C_OK"])
        self.assertEqual(markers, {"C_BAD": "3.0", "C_OK": "5.0"})
        self.assertNotIn("D_", str(fake.calls))

    def test_helpers(self):
        self.assertTrue(radar_slack._is_noise({"subtype": "channel_join"}, is_self=False))
        self.assertTrue(radar_slack._is_noise({"subtype": "file_share"}, is_self=False))
        self.assertFalse(radar_slack._is_noise({"subtype": "file_share"}, is_self=True))
        self.assertFalse(radar_slack._is_noise({"text": "x"}, is_self=False))
        self.assertTrue(radar_slack._not_after("5.0", "5.0"))
        self.assertTrue(radar_slack._not_after("4.9", "5.0"))
        self.assertFalse(radar_slack._not_after("5.1", "5.0"))
        self.assertFalse(radar_slack._not_after("0.1", ""))
        self.assertEqual(radar_slack._newer("9.5", "100.0"), "100.0")
        self.assertEqual(radar_slack._newer("100.0", "9.5"), "100.0")
        convs = {"ok": True, "channels": [{"id": "D1", "user": ME},
                                          {"id": "D2", "user": "U"},
                                          {"id": "G1", "is_mpim": True, "user": ME}]}
        self.assertEqual(list(radar_slack._dm_channels(convs, ME)),
                         [("D1", "im", True), ("D2", "im", False), ("G1", "mpim", False)])
        self.assertEqual(list(radar_slack._dm_channels({"ok": False}, ME)), [])
        cfg = config.Config()
        cfg.slack_channels = None
        self.assertEqual(radar_slack._watched_channel_ids(cfg), [])


if __name__ == "__main__":
    unittest.main()

"""slack_setup — row builders, pagination helpers, token resolution, the users
error path, the CLI (§15.3 Slack in-app setup).

Pins the P3b split: channel/user row shaping on junk entries, the _MAX_PAGES
cap, the users.list failure surfacing after a good channels page, the error
table for every code family (both languages), token resolution through
radar_slack.get_token, and ``_main`` in-process (manifest + directory exits).
"""
import io
import json
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import slack_setup


class RowBuildersTestCase(unittest.TestCase):
    def test_channel_row(self):
        self.assertIsNone(slack_setup._channel_row("junk"))
        self.assertIsNone(slack_setup._channel_row({"name": "no id"}))
        self.assertEqual(slack_setup._channel_row({"id": 7}), {"id": "7", "name": "7"})
        self.assertEqual(slack_setup._channel_row({"id": "C1", "name": "x"}),
                         {"id": "C1", "name": "x"})

    def test_user_row_filters_and_shapes(self):
        self.assertIsNone(slack_setup._user_row(None))
        self.assertIsNone(slack_setup._user_row({"name": "noid"}))
        self.assertIsNone(slack_setup._user_row({"id": "U1", "deleted": True}))
        self.assertIsNone(slack_setup._user_row({"id": "U1", "is_bot": True}))
        self.assertIsNone(slack_setup._user_row({"id": "USLACKBOT"}))
        self.assertEqual(slack_setup._user_row({"id": "U9"}),
                         {"id": "U9", "name": "U9", "real_name": ""})
        self.assertEqual(slack_setup._user_row({"id": "U9", "name": "z", "real_name": "top"}),
                         {"id": "U9", "name": "z", "real_name": "top"})
        self.assertEqual(slack_setup._real_name({"profile": {"real_name": "P"}, "real_name": "T"}),
                         "P")
        self.assertEqual(slack_setup._real_name({"profile": None, "real_name": "T"}), "T")

    def test_page_params_and_next_cursor(self):
        self.assertEqual(slack_setup._page_params({"a": 1}, None),
                         {"a": 1, "limit": slack_setup._PAGE_LIMIT})
        self.assertEqual(slack_setup._page_params({}, "c2")["cursor"], "c2")
        self.assertIsNone(slack_setup._next_cursor({}))
        self.assertIsNone(slack_setup._next_cursor({"response_metadata": {"next_cursor": ""}}))
        self.assertEqual(slack_setup._next_cursor({"response_metadata": {"next_cursor": "n"}}), "n")
        self.assertEqual(slack_setup._error_code({}), "unknown_error")
        self.assertEqual(slack_setup._error_code({"error": "x"}), "x")


class PaginateTestCase(unittest.TestCase):
    def test_max_pages_cap_stops_an_endless_cursor(self):
        calls = []

        def api(method, token, params):
            calls.append(params.get("cursor"))
            return {"ok": True, "members": [{"id": f"U{len(calls)}"}],
                    "response_metadata": {"next_cursor": "more"}}

        items, err = slack_setup._paginate("users.list", "t", {}, "members", api)
        self.assertIsNone(err)
        self.assertEqual(len(items), slack_setup._MAX_PAGES)
        self.assertEqual(len(calls), slack_setup._MAX_PAGES)
        self.assertIsNone(calls[0])
        self.assertEqual(calls[1], "more")

    def test_missing_list_key_is_tolerated(self):
        items, err = slack_setup._paginate("users.list", "t", {}, "members",
                                           lambda *_a: {"ok": True})
        self.assertEqual((items, err), ([], None))

    def test_users_failure_after_channels_success(self):
        def api(method, token, params):
            if method == "conversations.list":
                return {"ok": True, "channels": [{"id": "C1", "name": "a"}]}
            return {"ok": False, "error": "invalid_auth"}

        result = slack_setup._fetch_directory("tok", api)
        self.assertEqual((result["ok"], result["error"]), (False, "invalid_auth"))
        users, err = slack_setup.list_users("tok", api=api)
        self.assertEqual((users, err), (None, "invalid_auth"))


class ErrorTableTestCase(unittest.TestCase):
    def test_every_family_in_both_languages(self):
        for code in ("missing_scope", "ratelimited", "no_token"):
            zh, en = slack_setup._EXACT_PAIRS[code]
            self.assertEqual(slack_setup.error_message(code, "zh"), zh)
            self.assertEqual(slack_setup.error_message(code, "en"), en)
        for code in slack_setup._AUTH_CODES:
            self.assertEqual(slack_setup.error_message(code, "en"), slack_setup._AUTH_PAIR[1])
        self.assertEqual(slack_setup.error_message("transport:timeout", "zh"),
                         slack_setup._TRANSPORT_PAIR[0])
        self.assertIn("weird_code", slack_setup.error_message("weird_code", "en"))
        self.assertIn("unknown_error", slack_setup.error_message(None, "zh"))
        self.assertIn("unknown_error", slack_setup.error_message("", "en"))


class TokenAndCliTestCase(unittest.TestCase):
    def setUp(self):
        if slack_setup.DIRECTORY_CACHE_PATH.exists():
            slack_setup.DIRECTORY_CACHE_PATH.unlink()

    tearDown = setUp

    def test_token_resolution(self):
        self.assertEqual(slack_setup._resolve_token("explicit"), "explicit")
        with mock.patch("act.radar_slack.get_token", return_value="from-store"):
            self.assertEqual(slack_setup._resolve_token(None), "from-store")

    def test_fresh_cache_helper(self):
        self.assertIsNone(slack_setup._fresh_cache())
        with mock.patch.object(slack_setup, "_read_cache", return_value={"ok": True}), \
                mock.patch.object(slack_setup, "_cache_fresh", return_value=False):
            self.assertIsNone(slack_setup._fresh_cache())
        with mock.patch.object(slack_setup, "_read_cache", return_value={"ok": True}), \
                mock.patch.object(slack_setup, "_cache_fresh", return_value=True):
            self.assertEqual(slack_setup._fresh_cache(), {"ok": True})

    def test_cli_manifest(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            self.assertEqual(slack_setup._main(["--manifest"]), 0)
        self.assertEqual(out.getvalue(), slack_setup.manifest_json())

    def test_cli_directory_exit_codes(self):
        out = io.StringIO()
        with mock.patch.object(slack_setup, "directory",
                               return_value={"ok": True, "channels": []}) as d, \
                mock.patch("sys.stdout", out):
            self.assertEqual(slack_setup._main(["--directory", "--refresh"]), 0)
        d.assert_called_once_with(refresh=True)
        self.assertEqual(json.loads(out.getvalue()), {"ok": True, "channels": []})
        with mock.patch.object(slack_setup, "directory",
                               return_value={"ok": False, "error": "no_token"}) as d, \
                mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(slack_setup._main(["--directory"]), 1)
        d.assert_called_once_with(refresh=False)


if __name__ == "__main__":
    unittest.main()

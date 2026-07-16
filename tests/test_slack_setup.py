"""act/lib/slack_setup.py — Slack in-app setup helpers (§15 Slack settings section).

Covers: manifest generation drift-guard against the committed manifest files,
paginated directory listing, cache behavior, and the bilingual error
classification. Everything network-shaped goes through an injected fake api.
Sandboxed under the tests/__init__.py AIASSISTANT_HOME.
"""
import json
import unittest
from pathlib import Path

from act.lib import slack_setup

REPO_ROOT = Path(__file__).resolve().parent.parent


def _fake_api_pages(pages_by_method):
    """api(method, token, params) replaying canned cursor pages per method."""
    state = {m: 0 for m in pages_by_method}
    calls = []

    def api(method, token, params=None):
        calls.append((method, dict(params or {})))
        pages = pages_by_method.get(method)
        if pages is None:
            return {"ok": False, "error": "unknown_method"}
        i = min(state[method], len(pages) - 1)
        state[method] += 1
        return pages[i]

    api.calls = calls
    return api


class ManifestTestCase(unittest.TestCase):
    def test_generator_matches_committed_json(self):
        """config/slack-app-manifest.json == the generator (drift guard)."""
        committed = json.loads(
            (REPO_ROOT / "config" / "slack-app-manifest.json")
            .read_text(encoding="utf-8"))
        self.assertEqual(committed, slack_setup.manifest_dict())

    def test_generator_matches_committed_yaml(self):
        import yaml
        committed = yaml.safe_load(
            (REPO_ROOT / "config" / "slack-app-manifest.yaml")
            .read_text(encoding="utf-8"))
        self.assertEqual(committed, slack_setup.manifest_dict())

    def test_scopes_cover_the_radar_and_pickers(self):
        scopes = set(slack_setup.REQUIRED_USER_SCOPES)
        # the radar's hard requirements (act/radar_slack.py docstring) —
        # reactions:write = the §40 capture receipt (emoji ack, fail-soft)
        radar = {"search:read", "im:history", "im:read", "mpim:history",
                 "mpim:read", "channels:history", "groups:history",
                 "users:read", "files:read", "chat:write", "reactions:read",
                 "reactions:write"}
        # the Settings pickers (conversations.list public+private)
        pickers = {"channels:read", "groups:read", "users:read"}
        self.assertTrue(radar <= scopes)
        self.assertTrue(pickers <= scopes)
        # minimality: nothing beyond the two documented sets
        self.assertEqual(scopes, radar | pickers)

    def test_manifest_json_round_trips(self):
        self.assertEqual(json.loads(slack_setup.manifest_json()),
                         slack_setup.manifest_dict())


class DirectoryTestCase(unittest.TestCase):
    def setUp(self):
        if slack_setup.DIRECTORY_CACHE_PATH.exists():
            slack_setup.DIRECTORY_CACHE_PATH.unlink()

    tearDown = setUp

    @staticmethod
    def _happy_api():
        return _fake_api_pages({
            "conversations.list": [
                {"ok": True,
                 "channels": [{"id": "C2", "name": "zeta"},
                              {"id": "C1", "name": "alpha"}],
                 "response_metadata": {"next_cursor": "p2"}},
                {"ok": True,
                 "channels": [{"id": "C3", "name": "mid"}],
                 "response_metadata": {"next_cursor": ""}},
            ],
            "users.list": [
                {"ok": True, "members": [
                    {"id": "U1", "name": "zelin",
                     "profile": {"real_name": "Zelin W"}},
                    {"id": "U2", "name": "botty", "is_bot": True},
                    {"id": "U3", "name": "gone", "deleted": True},
                    {"id": "USLACKBOT", "name": "slackbot"},
                    {"id": "U4", "name": "ada", "profile": {}},
                ]},
            ],
        })

    def test_pagination_and_filtering(self):
        api = self._happy_api()
        data = slack_setup.directory(refresh=True, token="xoxp-test", api=api)
        self.assertTrue(data["ok"])
        self.assertEqual([c["id"] for c in data["channels"]],
                         ["C1", "C3", "C2"])          # name-sorted
        self.assertEqual([u["name"] for u in data["users"]],
                         ["ada", "zelin"])            # bots/deleted dropped
        self.assertEqual(data["users"][1]["real_name"], "Zelin W")
        # cursor was passed on the second channels page
        chan_calls = [p for m, p in api.calls if m == "conversations.list"]
        self.assertEqual(len(chan_calls), 2)
        self.assertEqual(chan_calls[1].get("cursor"), "p2")

    def test_cache_written_and_reused(self):
        data = slack_setup.directory(refresh=True, token="xoxp-test",
                                     api=self._happy_api())
        self.assertTrue(slack_setup.DIRECTORY_CACHE_PATH.exists())

        def exploding_api(method, token, params=None):  # must not be called
            raise AssertionError("cache should have been used")

        cached = slack_setup.directory(token="xoxp-test", api=exploding_api)
        self.assertEqual(cached["channels"], data["channels"])
        self.assertEqual(cached["users"], data["users"])

    def test_refresh_bypasses_cache(self):
        slack_setup.directory(refresh=True, token="xoxp-test",
                              api=self._happy_api())
        bad = _fake_api_pages({
            "conversations.list": [{"ok": False, "error": "missing_scope"}]})
        result = slack_setup.directory(refresh=True, token="xoxp-test", api=bad)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "missing_scope")

    def test_stale_cache_ignored(self):
        slack_setup.DIRECTORY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        slack_setup.DIRECTORY_CACHE_PATH.write_text(json.dumps({
            "ok": True, "fetched_at": "2000-01-01T00:00:00Z",
            "channels": [], "users": []}), encoding="utf-8")
        data = slack_setup.directory(token="xoxp-test", api=self._happy_api())
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["channels"]), 3)    # re-fetched, not []

    def test_no_token(self):
        result = slack_setup.directory(refresh=True, token="")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "no_token")
        self.assertTrue(result["message"])

    def test_partial_page_failure_is_an_error(self):
        api = _fake_api_pages({
            "conversations.list": [
                {"ok": True, "channels": [{"id": "C1", "name": "a"}],
                 "response_metadata": {"next_cursor": "p2"}},
                {"ok": False, "error": "ratelimited"},
            ]})
        result = slack_setup.directory(refresh=True, token="xoxp-test", api=api)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "ratelimited")


class ErrorMessageTestCase(unittest.TestCase):
    def test_known_codes_have_next_action_sentences(self):
        for code in ("missing_scope", "invalid_auth", "token_revoked",
                     "ratelimited", "no_token", "transport:timeout"):
            for lang in ("zh", "en"):
                msg = slack_setup.error_message(code, lang=lang)
                self.assertTrue(msg and "unknown" not in msg.lower(),
                                f"{code}/{lang}: {msg}")

    def test_missing_scope_points_at_the_manifest(self):
        self.assertIn("Manifest", slack_setup.error_message("missing_scope",
                                                            lang="en"))

    def test_unknown_code_rides_along(self):
        msg = slack_setup.error_message("some_new_code", lang="en")
        self.assertIn("some_new_code", msg)


if __name__ == "__main__":
    unittest.main()

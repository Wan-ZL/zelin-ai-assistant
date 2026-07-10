"""act/lib/update_check.py — in-app update check (CONTRACT §26).

Covers (a) semver comparison incl. the edge cases (same version, v-prefix,
prerelease tags, numeric vs lexicographic components), (b) cache/ETag behavior
with an injected fetch stub — first run fetches, a fresh cache never fetches,
an expired cache revalidates with If-None-Match, 304 keeps the cached answer,
transport failure silently keeps the cache AND consumes the 24h budget,
(c) the dashboard projection: ``update_available`` present ONLY when a
strictly newer version is known and the check is enabled, (d) the manual
「立即检查」path: ``force=True`` / ``cli_status`` bypasses the 24h budget but
never the enabled switch, keeps ETag/304 semantics, and reports transport
failure honestly while preserving the cache.

No network ever: fetch is injected everywhere. Everything lives under the
sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import datetime as dt
import json
import unittest

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act import __version__
from act.lib import config, update_check as uc


def _cfg(enabled: bool = True) -> config.Config:
    c = config.Config()
    c.updates_check_enabled = enabled
    return c


def _release(tag="v9.9.9", assets=None, html_url=None) -> dict:
    return {
        "tag_name": tag,
        "html_url": html_url
        or f"https://github.com/Wan-ZL/zelin-ai-assistant/releases/tag/{tag}",
        "assets": assets if assets is not None else [
            {"name": "checksums.sha256",
             "browser_download_url": "https://example.com/checksums.sha256"},
            {"name": f"ZelinAIAssistant-{tag}.pkg",
             "browser_download_url": f"https://example.com/{tag}.pkg"},
        ],
    }


class FetchStub:
    """Records calls; returns a queued (status, etag, release) per call."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []  # etags seen

    def __call__(self, etag):
        self.calls.append(etag)
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


class SemverTestCase(unittest.TestCase):
    def test_newer_and_older(self):
        self.assertTrue(uc.is_newer("0.14.0", "0.13.0"))
        self.assertFalse(uc.is_newer("0.13.0", "0.14.0"))

    def test_same_version_is_not_newer(self):
        self.assertFalse(uc.is_newer("0.13.0", "0.13.0"))
        self.assertFalse(uc.is_newer("v0.13.0", "0.13.0"))

    def test_numeric_not_lexicographic(self):
        self.assertTrue(uc.is_newer("0.10.0", "0.9.9"))
        self.assertTrue(uc.is_newer("1.0.0", "0.99.99"))

    def test_v_prefix_and_missing_patch(self):
        self.assertTrue(uc.is_newer("v1.2.3", "1.2.2"))
        self.assertFalse(uc.is_newer("1.2", "1.2.0"))   # 1.2 == 1.2.0
        self.assertTrue(uc.is_newer("1.3", "1.2.9"))

    def test_prerelease_sorts_before_release(self):
        self.assertFalse(uc.is_newer("0.14.0-rc.1", "0.14.0"))
        self.assertTrue(uc.is_newer("0.14.0", "0.14.0-rc.1"))
        # but a prerelease of a NEWER core still wins
        self.assertTrue(uc.is_newer("0.14.0-rc.1", "0.13.0"))

    def test_prerelease_ordering(self):
        self.assertTrue(uc.is_newer("0.14.0-rc.2", "0.14.0-rc.1"))
        self.assertTrue(uc.is_newer("0.14.0-rc.10", "0.14.0-rc.9"))  # numeric
        self.assertTrue(uc.is_newer("0.14.0-beta", "0.14.0-alpha"))
        # numeric identifiers sort before alphanumeric ones (semver rule)
        self.assertTrue(uc.is_newer("0.14.0-alpha", "0.14.0-1"))

    def test_build_metadata_ignored(self):
        self.assertFalse(uc.is_newer("0.13.0+build5", "0.13.0"))

    def test_garbage_never_newer(self):
        self.assertFalse(uc.is_newer("latest", "0.13.0"))
        self.assertFalse(uc.is_newer("0.14.0", "not-a-version"))
        self.assertFalse(uc.is_newer(None, "0.13.0"))
        self.assertFalse(uc.is_newer("", ""))


class CacheTestCase(unittest.TestCase):
    def setUp(self):
        if uc.STATE_PATH.exists():
            uc.STATE_PATH.unlink()
        self.t0 = dt.datetime(2026, 7, 9, 12, 0, tzinfo=dt.timezone.utc)

    def test_disabled_never_fetches(self):
        fetch = FetchStub((200, 'W/"e1"', _release()))
        self.assertIsNone(uc.check(_cfg(enabled=False), fetch=fetch, now=self.t0))
        self.assertEqual(fetch.calls, [])
        self.assertFalse(uc.STATE_PATH.exists())

    def test_first_run_fetches_and_caches(self):
        fetch = FetchStub((200, 'W/"e1"', _release("v9.9.9")))
        info = uc.check(_cfg(), fetch=fetch, now=self.t0)
        self.assertEqual(fetch.calls, [None])  # no etag yet
        self.assertEqual(info["current"], __version__)
        self.assertEqual(info["latest"], "9.9.9")
        self.assertIn("/releases/tag/v9.9.9", info["url"])
        self.assertEqual(info["pkg_asset_url"], "https://example.com/v9.9.9.pkg")
        state = json.loads(uc.STATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(state["etag"], 'W/"e1"')
        self.assertEqual(state["latest"], "9.9.9")
        self.assertEqual(state["checked_at"], "2026-07-09T12:00:00Z")

    def test_fresh_cache_skips_network(self):
        fetch = FetchStub((200, 'W/"e1"', _release("v9.9.9")))
        uc.check(_cfg(), fetch=fetch, now=self.t0)

        def boom(_etag):  # any call would fail the test
            raise AssertionError("network attempted inside the 24h window")

        later = self.t0 + dt.timedelta(hours=23)
        info = uc.check(_cfg(), fetch=boom, now=later)
        self.assertEqual(info["latest"], "9.9.9")

    def test_expired_cache_revalidates_with_etag_304(self):
        fetch = FetchStub((200, 'W/"e1"', _release("v9.9.9")),
                          (304, 'W/"e1"', None))
        uc.check(_cfg(), fetch=fetch, now=self.t0)
        later = self.t0 + dt.timedelta(hours=25)
        info = uc.check(_cfg(), fetch=fetch, now=later)
        self.assertEqual(fetch.calls, [None, 'W/"e1"'])  # If-None-Match sent
        self.assertEqual(info["latest"], "9.9.9")        # cache kept
        state = json.loads(uc.STATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(state["checked_at"], "2026-07-10T13:00:00Z")

    def test_expired_cache_picks_up_new_release(self):
        fetch = FetchStub((200, 'W/"e1"', _release("v9.9.9")),
                          (200, 'W/"e2"', _release("v9.10.0")))
        uc.check(_cfg(), fetch=fetch, now=self.t0)
        info = uc.check(_cfg(), fetch=fetch,
                        now=self.t0 + dt.timedelta(hours=25))
        self.assertEqual(info["latest"], "9.10.0")
        state = json.loads(uc.STATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(state["etag"], 'W/"e2"')

    def test_offline_keeps_cache_and_consumes_budget(self):
        fetch = FetchStub((200, 'W/"e1"', _release("v9.9.9")),
                          OSError("offline"))
        uc.check(_cfg(), fetch=fetch, now=self.t0)
        later = self.t0 + dt.timedelta(hours=25)
        info = uc.check(_cfg(), fetch=fetch, now=later)
        self.assertEqual(info["latest"], "9.9.9")  # silently kept
        # the FAILED attempt consumed the 24h budget: an immediate retry
        # must not touch the network again (no retry storm)
        def boom(_etag):
            raise AssertionError("retry inside the 24h window after a failure")
        info2 = uc.check(_cfg(), fetch=boom, now=later + dt.timedelta(minutes=10))
        self.assertEqual(info2["latest"], "9.9.9")

    def test_offline_first_run_returns_none(self):
        fetch = FetchStub(OSError("offline"))
        self.assertIsNone(uc.check(_cfg(), fetch=fetch, now=self.t0))
        # budget consumed even so
        state = json.loads(uc.STATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(state["checked_at"], "2026-07-09T12:00:00Z")

    def test_unparsable_release_kept_out_of_cache(self):
        fetch = FetchStub((200, 'W/"e1"', {"tag_name": "nightly"}))
        self.assertIsNone(uc.check(_cfg(), fetch=fetch, now=self.t0))

    def test_release_without_pkg_asset(self):
        fetch = FetchStub((200, 'W/"e1"', _release("v9.9.9", assets=[])))
        info = uc.check(_cfg(), fetch=fetch, now=self.t0)
        self.assertIsNone(info["pkg_asset_url"])
        self.assertIn("/releases/tag/v9.9.9", info["url"])

    def test_corrupt_state_file_recovers(self):
        config.ensure_state_dirs()
        uc.STATE_PATH.write_text("{not json", encoding="utf-8")
        fetch = FetchStub((200, 'W/"e1"', _release("v9.9.9")))
        info = uc.check(_cfg(), fetch=fetch, now=self.t0)
        self.assertEqual(info["latest"], "9.9.9")


class ForceCheckTestCase(unittest.TestCase):
    """§26 manual「立即检查」— force + the cli_status one-line JSON."""

    def setUp(self):
        if uc.STATE_PATH.exists():
            uc.STATE_PATH.unlink()
        self.t0 = dt.datetime(2026, 7, 9, 12, 0, tzinfo=dt.timezone.utc)

    def test_force_bypasses_fresh_budget(self):
        fetch = FetchStub((200, 'W/"e1"', _release("v9.9.9")),
                          (200, 'W/"e2"', _release("v9.10.0")))
        uc.check(_cfg(), fetch=fetch, now=self.t0)
        # one minute later the periodic path would cache-hit; force must fetch
        later = self.t0 + dt.timedelta(minutes=1)
        info = uc.check(_cfg(), fetch=fetch, now=later, force=True)
        self.assertEqual(fetch.calls, [None, 'W/"e1"'])  # ETag still sent
        self.assertEqual(info["latest"], "9.10.0")
        state = json.loads(uc.STATE_PATH.read_text(encoding="utf-8"))
        # checked_at restamped -> the periodic budget restarts from the click
        self.assertEqual(state["checked_at"], "2026-07-09T12:01:00Z")

    def test_force_304_is_a_valid_fresh_answer(self):
        fetch = FetchStub((200, 'W/"e1"', _release("v9.9.9")),
                          (304, 'W/"e1"', None))
        uc.check(_cfg(), fetch=fetch, now=self.t0)
        st = uc.cli_status(True, cfg=_cfg(), fetch=fetch,
                           now=self.t0 + dt.timedelta(minutes=5))
        self.assertTrue(st["ok"])
        self.assertNotIn("error", st)
        self.assertEqual(st["latest"], "9.9.9")           # cache kept
        self.assertEqual(st["checked_at"], "2026-07-09T12:05:00Z")

    def test_force_never_overrides_disabled(self):
        fetch = FetchStub((200, 'W/"e1"', _release("v9.9.9")))
        self.assertIsNone(uc.check(_cfg(enabled=False), fetch=fetch,
                                   now=self.t0, force=True))
        self.assertEqual(fetch.calls, [])                 # no network, ever
        st = uc.cli_status(True, cfg=_cfg(enabled=False), fetch=fetch,
                           now=self.t0)
        self.assertEqual(fetch.calls, [])
        self.assertTrue(st["ok"])
        self.assertFalse(st["enabled"])
        self.assertFalse(st["update_available"])

    def test_force_offline_reports_failure_and_keeps_cache(self):
        fetch = FetchStub((200, 'W/"e1"', _release("v9.9.9")),
                          OSError("offline"))
        uc.check(_cfg(), fetch=fetch, now=self.t0)
        later = self.t0 + dt.timedelta(minutes=1)
        st = uc.cli_status(True, cfg=_cfg(), fetch=fetch, now=later)
        self.assertFalse(st["ok"])
        self.assertEqual(st["error"], "network")
        self.assertEqual(st["latest"], "9.9.9")           # cache preserved
        # the failed manual attempt consumed the budget like any other
        self.assertEqual(st["checked_at"], "2026-07-09T12:01:00Z")

    def test_cli_status_surfaces_checked_at_and_verdict(self):
        fetch = FetchStub((200, 'W/"e1"', _release("v9.9.9")))
        st = uc.cli_status(cfg=_cfg(), fetch=fetch, now=self.t0)
        self.assertTrue(st["ok"])
        self.assertTrue(st["enabled"])
        self.assertTrue(st["update_available"])
        self.assertEqual(st["current"], __version__)
        self.assertEqual(st["latest"], "9.9.9")
        self.assertIn("/releases/tag/v9.9.9", st["url"])
        self.assertEqual(st["pkg_asset_url"], "https://example.com/v9.9.9.pkg")
        self.assertEqual(st["checked_at"], "2026-07-09T12:00:00Z")

    def test_cli_status_same_version_not_available(self):
        fetch = FetchStub((200, 'W/"e1"', _release("v" + __version__)))
        st = uc.cli_status(cfg=_cfg(), fetch=fetch, now=self.t0)
        self.assertTrue(st["ok"])
        self.assertFalse(st["update_available"])
        self.assertEqual(st["latest"], __version__)

    def test_cli_status_without_force_respects_budget(self):
        fetch = FetchStub((200, 'W/"e1"', _release("v9.9.9")))
        uc.check(_cfg(), fetch=fetch, now=self.t0)

        def boom(_etag):
            raise AssertionError("non-force cli_status fetched inside 24h")

        st = uc.cli_status(cfg=_cfg(), fetch=boom,
                           now=self.t0 + dt.timedelta(hours=1))
        self.assertTrue(st["ok"])
        self.assertEqual(st["latest"], "9.9.9")


class DashboardProjectionTestCase(unittest.TestCase):
    def setUp(self):
        if uc.STATE_PATH.exists():
            uc.STATE_PATH.unlink()
        self.t0 = dt.datetime(2026, 7, 9, 12, 0, tzinfo=dt.timezone.utc)

    def test_newer_release_projects_update_available(self):
        fetch = FetchStub((200, 'W/"e1"', _release("v9.9.9")))
        dash = uc.attach({"counts": {}}, _cfg(), fetch=fetch, now=self.t0)
        ua = dash["update_available"]
        self.assertEqual(ua["current"], __version__)
        self.assertEqual(ua["latest"], "9.9.9")
        self.assertIn("/releases/tag/v9.9.9", ua["url"])
        self.assertEqual(ua["pkg_asset_url"], "https://example.com/v9.9.9.pkg")

    def test_same_version_stays_absent(self):
        fetch = FetchStub((200, 'W/"e1"', _release("v" + __version__)))
        dash = uc.attach({"counts": {}}, _cfg(), fetch=fetch, now=self.t0)
        self.assertNotIn("update_available", dash)

    def test_older_release_stays_absent(self):
        fetch = FetchStub((200, 'W/"e1"', _release("v0.0.1")))
        dash = uc.attach({"counts": {}}, _cfg(), fetch=fetch, now=self.t0)
        self.assertNotIn("update_available", dash)

    def test_disabled_stays_absent_even_with_cached_newer(self):
        fetch = FetchStub((200, 'W/"e1"', _release("v9.9.9")))
        uc.check(_cfg(), fetch=fetch, now=self.t0)  # cache a newer version
        dash = uc.attach({"counts": {}}, _cfg(enabled=False),
                         now=self.t0 + dt.timedelta(hours=1))
        self.assertNotIn("update_available", dash)

    def test_unknown_stays_absent(self):
        fetch = FetchStub(OSError("offline"))
        dash = uc.attach({"counts": {}}, _cfg(), fetch=fetch, now=self.t0)
        self.assertNotIn("update_available", dash)


class OverrideTestCase(unittest.TestCase):
    """§26: the App toggle rides the standard overrides allowlist."""

    def tearDown(self):
        if config.SETTINGS_OVERRIDES_PATH.exists():
            config.SETTINGS_OVERRIDES_PATH.unlink()

    def test_updates_check_enabled_override(self):
        config.ensure_state_dirs()
        config.SETTINGS_OVERRIDES_PATH.write_text(
            json.dumps({"updates_check_enabled": False}), encoding="utf-8")
        cfg = config.load_config()
        self.assertFalse(cfg.updates_check_enabled)

    def test_default_is_enabled(self):
        cfg = config.load_config()
        self.assertTrue(cfg.updates_check_enabled)


if __name__ == "__main__":
    unittest.main()

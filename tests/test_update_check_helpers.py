"""update_check — the semver / release-view / budget helpers behind check() (§26).

Pins the P3b split: core-number parsing edges, prerelease id typing, the
.pkg asset picker (non-dict assets, missing url, first match wins), the
release url fallback, the freshness gate (budget boundary, force), the
fetch attempt on non-200 / non-dict / unparsable answers, and the CLI error
kind (403 / 429 → rate_limited, otherwise network).
"""
import datetime as _dt
import unittest
import urllib.error

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import update_check as uc


class SemverHelpersTestCase(unittest.TestCase):
    def test_core_numbers(self):
        self.assertEqual(uc._core_numbers("1.2"), (1, 2, 0))
        self.assertEqual(uc._core_numbers(" 1.2.3 "), (1, 2, 3))
        self.assertIsNone(uc._core_numbers("1"))
        self.assertIsNone(uc._core_numbers("1.2.3.4"))
        self.assertIsNone(uc._core_numbers("a.b"))

    def test_prerelease_ids(self):
        self.assertEqual(uc._prerelease_ids("rc.1"), ((1, 0, "rc"), (0, 1, "")))
        self.assertEqual(uc._prerelease_ids("10"), ((0, 10, ""),))

    def test_parse_version_shapes(self):
        self.assertEqual(uc.parse_version("V2.0"), (2, 0, 0, 1, ()))
        self.assertEqual(uc.parse_version("2.0.1-beta.2+build9"),
                         (2, 0, 1, 0, ((1, 0, "beta"), (0, 2, ""))))
        self.assertIsNone(uc.parse_version(""))
        self.assertIsNone(uc.parse_version(None))
        self.assertIsNone(uc.parse_version("v"))
        self.assertIsNone(uc.parse_version("1.x"))
        # a dangling "-" is a prerelease with one empty alpha id
        self.assertEqual(uc.parse_version("1.0-"), (1, 0, 0, 0, ((1, 0, ""),)))


class ReleaseViewTestCase(unittest.TestCase):
    def test_pkg_asset_picker(self):
        assets = ["junk", {"name": "a.zip", "browser_download_url": "u1"},
                  {"name": "b.pkg"}, {"name": "c.pkg", "browser_download_url": "u3"},
                  {"name": "d.pkg", "browser_download_url": "u4"}]
        self.assertEqual(uc._pkg_asset_url(assets), "u3")
        self.assertIsNone(uc._pkg_asset_url(None))
        self.assertIsNone(uc._pkg_asset_url([]))
        self.assertFalse(uc._is_pkg_asset({"name": None, "browser_download_url": "u"}))

    def test_release_url_fallback(self):
        self.assertEqual(uc._release_url({"html_url": "H"}, "v1"), "H")
        self.assertEqual(uc._release_url({"html_url": ""}, "v1"), uc.RELEASES_PAGE_URL + "/tag/v1")
        self.assertEqual(uc._release_url({}, "v1"), uc.RELEASES_PAGE_URL + "/tag/v1")

    def test_release_view(self):
        self.assertIsNone(uc._release_view({"tag_name": "nightly"}))
        self.assertIsNone(uc._release_view({}))
        view = uc._release_view({"tag_name": " v1.2.3 ", "html_url": "H",
                                 "assets": [{"name": "x.pkg", "browser_download_url": "P"}]})
        self.assertEqual(view, {"latest": "1.2.3", "url": "H", "pkg_asset_url": "P"})
        self.assertEqual(uc._release_view({"tag_name": "1.0.0"})["latest"], "1.0.0")


class BudgetAndFetchTestCase(unittest.TestCase):
    NOW = _dt.datetime(2026, 9, 2, 12, 0, tzinfo=_dt.timezone.utc)

    def test_is_fresh_boundary_and_force(self):
        inside = {"checked_at": uc._iso(self.NOW - _dt.timedelta(seconds=uc.CHECK_INTERVAL_SECONDS - 1))}
        at = {"checked_at": uc._iso(self.NOW - _dt.timedelta(seconds=uc.CHECK_INTERVAL_SECONDS))}
        self.assertTrue(uc._is_fresh(inside, self.NOW, False))
        self.assertFalse(uc._is_fresh(at, self.NOW, False))
        self.assertFalse(uc._is_fresh(inside, self.NOW, True))
        self.assertFalse(uc._is_fresh({}, self.NOW, False))
        self.assertFalse(uc._is_fresh({"checked_at": "garbage"}, self.NOW, False))

    def test_attempt_fetch_outcomes(self):
        state = {"etag": "e0", "latest": "0.1.0"}
        uc._attempt_fetch(state, lambda etag: (304, etag, None))
        self.assertEqual(state, {"etag": "e0", "latest": "0.1.0"})
        uc._attempt_fetch(state, lambda etag: (200, "e1", "not-a-dict"))
        self.assertEqual(state["etag"], "e0")
        uc._attempt_fetch(state, lambda etag: (200, "e1", {"tag_name": "junk"}))
        self.assertEqual(state["latest"], "0.1.0")
        uc._attempt_fetch(state, lambda etag: (200, "e2", {"tag_name": "v9.9.9"}))
        self.assertEqual((state["latest"], state["etag"]), ("9.9.9", "e2"))
        uc._attempt_fetch(state, lambda etag: (500, None, {"tag_name": "v1.0.0"}))
        self.assertEqual(state["latest"], "9.9.9")

        def boom(_etag):
            raise OSError("offline")

        uc._attempt_fetch(state, boom)
        self.assertEqual(state["latest"], "9.9.9")

    def test_answer(self):
        self.assertIsNone(uc._answer({}))
        self.assertIsNone(uc._answer({"latest": ""}))
        out = uc._answer({"latest": 1.5, "checked_at": "c"})
        self.assertEqual(out["latest"], "1.5")
        self.assertEqual(out["url"], uc.RELEASES_PAGE_URL)
        self.assertEqual(out["current"], uc.__version__)
        self.assertIsNone(out["pkg_asset_url"])


class CliHelpersTestCase(unittest.TestCase):
    def test_error_kind(self):
        rl = urllib.error.HTTPError("u", 429, "slow", {}, None)
        forbidden = urllib.error.HTTPError("u", 403, "no", {}, None)
        other = urllib.error.HTTPError("u", 500, "boom", {}, None)
        self.assertEqual(uc._error_kind([OSError(), rl]), "rate_limited")
        self.assertEqual(uc._error_kind([forbidden]), "rate_limited")
        self.assertEqual(uc._error_kind([other]), "network")
        self.assertEqual(uc._error_kind([OSError("x")]), "network")

    def test_cli_payload(self):
        out = uc._cli_payload({}, True)
        self.assertEqual((out["ok"], out["latest"], out["update_available"]), (True, None, False))
        out = uc._cli_payload({"latest": "999.0.0", "url": "U"}, False)
        self.assertEqual((out["update_available"], out["url"]), (False, "U"))
        out = uc._cli_payload({"latest": "999.0.0"}, True)
        self.assertTrue(out["update_available"])


if __name__ == "__main__":
    unittest.main()

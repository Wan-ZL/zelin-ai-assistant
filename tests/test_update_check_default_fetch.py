"""The one real network call of the product besides model calls (CONTRACT §26).

``update_check._default_fetch`` is what ``check()`` uses when no fetch is
injected. Pinned with ``urllib.request.urlopen`` patched (no network ever): the
request exposes only the User-Agent version string and an optional
``If-None-Match``; a 200 hands back ``(status, ETag, json)``; urllib's 304
"error" becomes ``(304, the same etag, None)``; any other HTTP error propagates
(``check()`` counts it against the 24h budget).
"""
import io
import json
import unittest
import urllib.error
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import update_check as uc


class _Resp:
    def __init__(self, status, body, etag):
        self.status = status
        self.headers = {"ETag": etag}
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class DefaultFetchTestCase(unittest.TestCase):
    def test_200_returns_status_etag_and_json_and_sends_only_the_ua(self):
        seen = []

        def urlopen(req, timeout):
            seen.append((req, timeout))
            return _Resp(200, json.dumps({"tag_name": "v2.0.1"}).encode("utf-8"), 'W/"e2"')

        with mock.patch.object(uc.urllib.request, "urlopen", urlopen):
            out = uc._default_fetch(None)
        self.assertEqual(out, (200, 'W/"e2"', {"tag_name": "v2.0.1"}))
        req, timeout = seen[0]
        self.assertEqual(req.full_url, uc.RELEASES_LATEST_URL)
        self.assertEqual(timeout, uc.TIMEOUT_SECONDS)
        self.assertIn("(update-check)", req.get_header("User-agent"))
        self.assertIsNone(req.get_header("If-none-match"))

    def test_the_etag_rides_along_and_304_keeps_it(self):
        seen = []

        def urlopen(req, timeout):
            seen.append(req)
            raise urllib.error.HTTPError(req.full_url, 304, "Not Modified", {}, io.BytesIO(b""))

        with mock.patch.object(uc.urllib.request, "urlopen", urlopen):
            self.assertEqual(uc._default_fetch('W/"e1"'), (304, 'W/"e1"', None))
        self.assertEqual(seen[0].get_header("If-none-match"), 'W/"e1"')

    def test_other_http_errors_propagate(self):
        def urlopen(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 403, "rate limited", {}, io.BytesIO(b""))

        with mock.patch.object(uc.urllib.request, "urlopen", urlopen):
            with self.assertRaises(urllib.error.HTTPError):
                uc._default_fetch(None)


if __name__ == "__main__":
    unittest.main()

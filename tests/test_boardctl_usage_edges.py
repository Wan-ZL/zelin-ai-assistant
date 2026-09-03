"""boardctl 参数解析 / 错误渲染的边角判例（CONTRACT §52 agent 有界通道）。

test_boardctl 走真 server 主线；这里只用 main() 注入缝 + 假 urlopen，钉此前
没有判例的 usage 分支（空 option 名、缺值、重复 option、operand 数、help
不独占、--image 三条闸、--*-file 读失败、ZAI_PORT 非法）、server 非 JSON
成功响应 → INVALID_RESPONSE(4)、409 → exit 5、envelope 退化为 HTTP_<n>，
以及 main 的 INTERNAL_ERROR 兜底（exit 1）。
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401

from act import boardctl

_ENV = {"ZAI_PORT": "47821", "AIASSISTANT_HOME": TMP_HOME}


def _ctl(*argv, env=None):
    out, err = io.StringIO(), io.StringIO()
    rc = boardctl.main(list(argv), stdout=out, stderr=err,
                       environ=_ENV if env is None else env)
    return rc, out.getvalue(), err.getvalue()


def _err(case, expected_rc, *argv, env=None) -> dict:
    rc, out, err = _ctl(*argv, env=env)
    case.assertEqual(rc, expected_rc, f"stdout={out!r} stderr={err!r}")
    case.assertEqual(out, "")
    doc = json.loads(err)
    case.assertEqual(doc["schemaVersion"], boardctl.SCHEMA_VERSION)
    return doc["error"]


class ParseArgsTestCase(unittest.TestCase):
    def test_empty_option_name(self):
        self.assertEqual(_err(self, 2, "board", "--")["message"], "empty option name")

    def test_value_option_missing_value(self):
        self.assertIn("requires a value", _err(self, 2, "board", "--lane")["message"])

    def test_repeatable_option_missing_value(self):
        self.assertIn("requires a value",
                      _err(self, 2, "capture", "--text", "t", "--image")["message"])

    def test_value_option_given_twice(self):
        self.assertIn("more than once",
                      _err(self, 2, "board", "--lane", "a", "--lane", "b")["message"])

    def test_repeatable_accumulates_and_bools_take_no_value(self):
        parsed = boardctl.parse_args(["capture", "--image", "/a", "--image", "/b",
                                      "--json", "--text", "t"])
        self.assertEqual(parsed["options"],
                         {"image": ["/a", "/b"], "json": True, "text": "t"})
        self.assertEqual(parsed["command"], "capture")

    def test_operand_count_enforced(self):
        self.assertIn("exactly 1 operand", _err(self, 2, "card")["message"])
        self.assertIn("exactly 0 operand", _err(self, 2, "board", "extra")["message"])

    def test_help_must_be_alone(self):
        self.assertIn("help is available",
                      _err(self, 2, "board", "--lane", "x", "--help")["message"])
        self.assertIn("help is available", _err(self, 2, "nope", "--help")["message"])

    def test_unknown_option_for_command(self):
        msg = _err(self, 2, "board", "--text", "x")["message"]
        self.assertIn("unknown option(s) for board", msg)
        self.assertIn("--text", msg)


class TextSourceAndImagesTestCase(unittest.TestCase):
    def test_text_and_text_file_are_exclusive(self):
        self.assertIn("exactly one of", _err(self, 2, "capture")["message"])
        self.assertIn("exactly one of",
                      _err(self, 2, "capture", "--text", "a", "--text-file", "b")["message"])

    def test_unreadable_text_file(self):
        err = _err(self, 2, "capture", "--text-file", "/nonexistent/zai.txt")
        self.assertEqual(err["code"], "FILE_READ_FAILED")
        self.assertIn("cause", err["details"])

    def test_empty_text_and_empty_body(self):
        self.assertIn("must not be empty", _err(self, 2, "capture", "--text", "  ")["message"])
        self.assertIn("must not be empty",
                      _err(self, 2, "comment", "R-1", "--body", " ")["message"])

    def test_comment_bad_id(self):
        self.assertIn("card id must match", _err(self, 2, "comment", "../x", "--body", "b")["message"])

    def test_image_gates(self):
        too_many = []
        for i in range(boardctl.CAPTURE_IMAGES_MAX + 1):
            too_many += ["--image", f"/p{i}.png"]
        self.assertIn("at most", _err(self, 2, "capture", "--text", "t", *too_many)["message"])
        self.assertIn("must not repeat",
                      _err(self, 2, "capture", "--text", "t", "--image", "/a", "--image", "/a")["message"])
        self.assertIn("must be absolute",
                      _err(self, 2, "capture", "--text", "t", "--image", "rel.png")["message"])

    def test_zai_port_validation(self):
        for bad in ("abc", "0", "70000"):
            env = dict(_ENV, ZAI_PORT=bad)
            self.assertIn("ZAI_PORT", _err(self, 2, "board", env=env)["message"], bad)
        self.assertEqual(boardctl._base_url({"ZAI_PORT": "  "}),
                         f"http://127.0.0.1:{boardctl.DEFAULT_PORT}")


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class ResponseDecodingTestCase(unittest.TestCase):
    def test_non_json_success_body_is_invalid_response(self):
        with mock.patch.object(boardctl.urllib.request, "urlopen",
                               return_value=_FakeResponse(b"<html>")):
            err = _err(self, 4, "board")
        self.assertEqual(err["code"], "INVALID_RESPONSE")

    def test_json_array_success_body_is_invalid_response(self):
        with mock.patch.object(boardctl.urllib.request, "urlopen",
                               return_value=_FakeResponse(b"[1]")):
            self.assertEqual(_err(self, 4, "board")["code"], "INVALID_RESPONSE")

    def _http_error(self, status: int, body: bytes) -> urllib.error.HTTPError:
        return urllib.error.HTTPError("http://x", status, "msg", {}, io.BytesIO(body))

    def test_409_maps_to_exit_5_and_passes_envelope(self):
        body = json.dumps({"error": {"code": "STALE", "message": "moved",
                                     "details": {"seq": 3}}}).encode()
        with mock.patch.object(boardctl.urllib.request, "urlopen",
                               side_effect=self._http_error(409, body)):
            err = _err(self, 5, "board")
        self.assertEqual((err["code"], err["message"], err["details"]),
                         ("STALE", "moved", {"seq": 3}))

    def test_unparsable_error_body_degrades_to_http_code(self):
        with mock.patch.object(boardctl.urllib.request, "urlopen",
                               side_effect=self._http_error(502, b"gateway")):
            err = _err(self, 4, "board")
        self.assertEqual(err["code"], "HTTP_502")
        self.assertEqual(err["message"], "server returned HTTP 502")
        self.assertNotIn("details", err)

    def test_empty_details_dict_is_dropped(self):
        body = json.dumps({"error": {"code": 7, "message": None, "details": {}}}).encode()
        with mock.patch.object(boardctl.urllib.request, "urlopen",
                               side_effect=self._http_error(400, body)):
            err = _err(self, 4, "board")
        self.assertEqual(err["code"], "HTTP_400")   # 非字符串 code 退化
        self.assertNotIn("details", err)

    def test_error_envelope_helper_shapes(self):
        self.assertEqual(boardctl._error_envelope(b"not json"), {})
        self.assertEqual(boardctl._error_envelope(b'{"error": "str"}'), {})
        self.assertEqual(boardctl._error_envelope(b'[1]'), {})


class InternalErrorTestCase(unittest.TestCase):
    def test_unexpected_exception_is_exit_1_without_trace(self):
        with mock.patch.object(boardctl, "parse_args", side_effect=RuntimeError("bug")):
            rc, out, err = _ctl("board")
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        doc = json.loads(err)
        self.assertEqual(doc["error"], {"code": "INTERNAL_ERROR", "message": "bug"})

    def test_exception_without_text_uses_type_name(self):
        with mock.patch.object(boardctl, "parse_args", side_effect=KeyError()):
            _rc, _out, err = _ctl("board")
        self.assertEqual(json.loads(err)["error"]["message"], "KeyError")

    def test_instance_token_missing_file_is_none(self):
        home = Path(tempfile.mkdtemp(prefix="zai-ctl-tok-"))
        self.assertIsNone(boardctl._instance_token({"AIASSISTANT_HOME": str(home)}))
        (home / "state").mkdir()
        (home / "state" / "server.token").write_text("\n", encoding="utf-8")
        self.assertIsNone(boardctl._instance_token({"AIASSISTANT_HOME": str(home)}))


if __name__ == "__main__":
    unittest.main()

"""§63.14（issue #440）：``GET /api/settings/recap`` 多一个只读的 ``glossary`` 提示——术语表在哪、有没有。

面板要能告诉 owner「术语表住 state/recap-glossary.md（尚未创建 / 已有）、config.yaml
recap.glossary 里有 N 条」；解析器（去重、上限、听错形）住 act 侧，server 不 import act
（§49），所以这里只报路径、文件在不在、config 列表里字符串的条数——不数解析后的词条、
不撒一个自己算不出的数。PUT 仍只认三把旋钮（这一格是只读的）。
"""
import json
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import (assert_envelope, auth_headers, get_json, http_request,
                                      start_server, write_text)


class GlossaryHintTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-recap-glossary-hint-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        _httpd, self.port = start_server(self, self.home)

    def test_absent_file_and_no_config(self):
        status, snap = get_json(self.port, "/api/settings/recap")
        self.assertEqual(status, 200)
        self.assertEqual(snap["glossary"], {"path": str(self.home / "state" / "recap-glossary.md"),
                                            "present": False, "config_terms": 0})
        # 三把旋钮的 source 表不因这一格而多一项（它不是旋钮）
        self.assertNotIn("glossary", snap["source"])

    def test_present_file_and_the_config_list_count(self):
        write_text(self.home / "state" / "recap-glossary.md", "SageMaker: stage maker\n")
        write_text(self.home / "config.yaml",
                   "recap:\n  glossary:\n    - 'SageMaker: stage maker'\n    - Nemotron\n    - 7\n")
        _s, snap = get_json(self.port, "/api/settings/recap")
        self.assertEqual((snap["glossary"]["present"], snap["glossary"]["config_terms"]), (True, 2))
        # 空文件不算「有」
        write_text(self.home / "state" / "recap-glossary.md", "")
        _s, snap = get_json(self.port, "/api/settings/recap")
        self.assertFalse(snap["glossary"]["present"])
        write_text(self.home / "config.yaml", "recap:\n  glossary: not-a-list\n")
        _s, snap = get_json(self.port, "/api/settings/recap")
        self.assertEqual(snap["glossary"]["config_terms"], 0)

    def test_put_still_refuses_the_read_only_hint(self):
        body = b'{"glossary": {"present": true}}'
        status, _headers, raw = http_request(self.port, "PUT", "/api/settings/recap", body=body,
                                             headers=auth_headers(self.port))
        self.assertEqual(status, 400)
        assert_envelope(self, json.loads(raw.decode("utf-8")), "UNKNOWN_FIELD")


if __name__ == "__main__":
    unittest.main()

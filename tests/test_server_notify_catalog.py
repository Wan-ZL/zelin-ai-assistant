"""GET /api/notifications —— 系统通知目录（CONTRACT §28 / §49 路由表）。

钉住：
- 壳直发的每句系统通知（title + body）与 shell/Sources 的 ``L("zh","en")`` 对逐字一致
  （占位只差名字：目录 `{n}` vs Swift `\\(overflow.count)`）；引用 §25 FailureCatalog 的
  正文与 act/lib/failures.py plain_zh / plain_en 逐字一致（单源，不复制第二份）；
- （v2.0.0 退役）§66 原生清单的 ``notification:<kind>`` / ``control:notifications:*`` 对照：清单随
  mac/ 与 ui/parity/ 删除（全文见 tag full）；壳侧的 L() 对照仍在（下面的内联扫描器）；
- 写方真写的 kind（act/recap NOTIFY_KIND、act/lib/notify.KINDS）在词表里，退役的卡片类不在；
- 路由：token-light GET、no-store、body == catalog()、不需要 dashboard。
"""
from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import recap
from act.lib import failures, notify
from server import notify_catalog as nc
from tests.test_server_common import get_json, http_request, start_server

REPO_ROOT = Path(__file__).resolve().parent.parent

# L("zh", "en") 双语字面量（v2.0.0 前借 scripts/ui/ui_common.py 的扫描器；它随 §66 退役，
# 这里内联判例需要的那一小段：去掉行注释、认字面量、插值 \(expr) → {expr}，括号配对）
_STR = r'"((?:[^"\\]|\\.)*)"'
_L_CALL = re.compile(r"\bL\(\s*" + _STR + r"\s*,\s*" + _STR + r"\s*\)", re.S)
_LINE_COMMENT = re.compile(r"^\s*//.*$", re.M)


def _fold_interpolations(literal: str) -> str:
    out, i = [], 0
    while i < len(literal):
        if literal.startswith("\\(", i):
            depth, j = 1, i + 2
            while j < len(literal) and depth:
                depth += {"(": 1, ")": -1}.get(literal[j], 0)
                j += 1
            if not depth:
                out.append("{%s}" % literal[i + 2:j - 1].strip())
                i = j
                continue
        out.append(literal[i])
        i += 1
    return "".join(out)


_PLACEHOLDER = re.compile(r"\{[^{}]*\}")


def _fragments(template: str) -> list:
    """模板 → 去掉 ``{占位}`` 后的静态片段（v2.0.0 前是 server.notify_catalog.fragments）。"""
    return [part for part in _PLACEHOLDER.split(template) if part]


def _same_template(a: str, b: str) -> bool:
    """两句只差占位名（`{n}` vs `{overflow.count}`）即视为同一句。"""
    return _fragments(a) == _fragments(b)


def _kind_names() -> list:
    return [k["kind"] for k in nc.KINDS]


def _has_sentence(zh: str, en: str) -> bool:
    """目录里（title / body / 每个 slot 取值）有没有这一对句子（只差占位名即算同一句）。"""
    pairs = []
    for notice in nc.SHELL_NOTICES:
        resolved = nc.resolve_notice(notice)
        pairs.append((resolved["title"]["zh"], resolved["title"]["en"]))
        pairs.append((resolved["body"]["zh"], resolved["body"]["en"]))
        for values in resolved["slots"].values():
            pairs.extend((v["zh"], v["en"]) for v in values)
    return any(_same_template(zh, pzh) and _same_template(en, pen) for pzh, pen in pairs)


def _unescape_swift(literal: str) -> str:
    text = _fold_interpolations(literal)
    text = text.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t")
    return text.replace("\\\\", "\\")


# 随 Act 管线退役的卡片生命周期 kind（v2.0.0，全文见 tag full）。
RETIRED_KINDS = frozenset({"review_ready", "proposal", "needs_input", "receipt", "review_stale"})


def _shell_l_pairs() -> list:
    """shell/Sources 全部 L() 对（zh, en），插值已成 {expr} 占位。"""
    pairs = []
    for name in sorted(os.listdir(REPO_ROOT / "shell" / "Sources")):
        if not name.endswith(".swift"):
            continue
        raw = (REPO_ROOT / "shell" / "Sources" / name).read_text(encoding="utf-8")
        code = _LINE_COMMENT.sub("", raw)
        pairs.extend((_unescape_swift(m.group(1)), _unescape_swift(m.group(2))) for m in _L_CALL.finditer(code))
    return pairs


def _has_template(pairs: list, zh: str, en: str) -> bool:
    """title / 静态 body：某个 L() 对与目录句只差占位名。"""
    return any(_same_template(zh, pzh) and _same_template(en, pen) for pzh, pen in pairs)


def _contains_fragments(pairs: list, zh: str, en: str) -> bool:
    """插值 body（Swift 嵌套括号的插值提取不稠密）：目录句的每个静态片段都是某个 L() 对的子串。"""
    for pzh, pen in pairs:
        if all(f in pzh for f in _fragments(zh)) and all(f in pen for f in _fragments(en)):
            return True
    return False


class ShellMirrorTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pairs = _shell_l_pairs()

    def test_scanner_reads_l_pairs(self):
        self.assertGreater(len(self.pairs), 20)
        self.assertEqual(_unescape_swift('还有 \\(overflow.count) 条'), "还有 {overflow.count} 条")
        self.assertEqual(_unescape_swift('a \\(f(x)) \\"q\\"'), 'a {f(x)} "q"')

    def test_every_title_is_an_l_pair_in_shell_sources(self):
        for notice in nc.SHELL_NOTICES:
            with self.subTest(notice=notice["id"]):
                self.assertTrue(_has_template(self.pairs, notice["title"]["zh"], notice["title"]["en"]),
                                "title of %s is not what the shell posts" % notice["id"])

    def test_bodies_are_the_shell_sentence_or_the_failure_catalog_sentence(self):
        for notice in nc.SHELL_NOTICES:
            body = nc.resolve_notice(notice)["body"]
            with self.subTest(notice=notice["id"]):
                self.assertTrue(body["zh"] and body["en"], "body must be bilingual and non-empty")
                fid = notice["body_failure_id"]
                if fid is not None:
                    self.assertEqual(body, {"zh": failures.FAILURES[fid]["plain_zh"],
                                            "en": failures.FAILURES[fid]["plain_en"]})
                else:
                    self.assertTrue(_contains_fragments(self.pairs, body["zh"], body["en"]),
                                    "body of %s drifted from shell/Sources" % notice["id"])

    def test_slot_values_are_l_pairs_in_shell_sources(self):
        """回滚句的插值词表（模式名 / 死因）每个取值都是壳真有的 L() 对。"""
        reverted = next(n for n in nc.SHELL_NOTICES if n["id"] == "recording_mode_reverted")
        self.assertEqual(set(reverted["slots"]), {"failed", "kept", "cause"})
        for slot, values in reverted["slots"].items():
            self.assertTrue(values, slot)
            for value in values:
                with self.subTest(slot=slot, value=value["en"]):
                    self.assertTrue(_has_template(self.pairs, value["zh"], value["en"]),
                                    "slot value %r is not what the shell composes" % value["en"])
        self.assertTrue(_has_sentence("缺 Node.js", "Node.js is missing"))
        self.assertTrue(_has_sentence("屏幕 + 音频", "Screen + Audio"))
        self.assertFalse(_has_sentence("屏幕+音频", "Screen + audio"))   # header 按钮词是另一对，web 自己渲

    def test_daemon_written_kinds_are_in_the_vocabulary(self):
        names = _kind_names()
        self.assertIn(recap.NOTIFY_KIND, names)
        self.assertIn("general", names)
        # v2.0.0（宪法第 10 条）：写方的 kind 词表就是 act/lib/notify.KINDS，目录逐个登记，
        # 退役的卡片类一个都不许留在词表里。
        for kind in notify.KINDS:
            with self.subTest(kind=kind):
                self.assertIn(kind, names)
        self.assertFalse(RETIRED_KINDS & set(names))

    def test_template_helpers(self):
        self.assertEqual(_fragments("还有 {n} 条通知"), ["还有 ", " 条通知"])
        self.assertTrue(_same_template("+{n} more notifications", "+{overflow.count} more notifications"))
        self.assertFalse(_same_template("+{n} more notifications", "{n} more notifications"))
        self.assertEqual(_fragments("plain"), ["plain"])
        self.assertTrue(_has_sentence("打开 App 查看会议纪要", "Open the app to see the recaps"))   # body 也算一句
        self.assertFalse(_has_sentence("打开 App 查看会议纪要", "Open the app"))
        self.assertFalse(_has_sentence("打开 App 查看看板", "Open the app to see the board"),
                         "the board is retired (v2.0.0): the overflow body names the recaps")


class CatalogShapeTestCase(unittest.TestCase):
    def test_shape_and_fresh_copy(self):
        doc = nc.catalog()
        self.assertEqual(set(doc), {"shell_notices", "kinds"})
        ids = [n["id"] for n in doc["shell_notices"]]
        self.assertEqual(len(ids), len(set(ids)))
        for notice in doc["shell_notices"]:
            self.assertEqual(set(notice), {"id", "title", "body", "slots", "source"})
            for key in ("title", "body"):
                self.assertTrue(notice[key]["zh"] and notice[key]["en"])
        for kind in doc["kinds"]:
            self.assertTrue(kind["title"]["zh"] and kind["help"]["en"])
        doc["shell_notices"][0]["title"]["zh"] = "tampered"
        doc["kinds"][0]["title"]["zh"] = "tampered"
        fresh = nc.catalog()
        self.assertNotEqual(fresh["shell_notices"][0]["title"]["zh"], "tampered")
        self.assertNotEqual(fresh["kinds"][0]["title"]["zh"], "tampered")

    def test_unknown_failure_id_body_is_empty_not_a_crash(self):
        notice = dict(nc.SHELL_NOTICES[1], body_failure_id="no_such_failure")
        self.assertEqual(nc.resolve_notice(notice)["body"], {"zh": "", "en": ""})


class CatalogRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-notify-"))
        (self.home / "state").mkdir()
        _, self.port = start_server(self, self.home)

    def test_get_notifications_is_token_light_and_no_store(self):
        status, headers, data = http_request(self.port, "GET", "/api/notifications")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertIn(b'"kind": "recap_ready"', data)

    def test_body_equals_catalog(self):
        status, obj = get_json(self.port, "/api/notifications")
        self.assertEqual(status, 200)
        self.assertEqual(obj, nc.catalog())


if __name__ == "__main__":
    unittest.main()

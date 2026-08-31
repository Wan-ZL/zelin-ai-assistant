"""POST /api/actions → state/inbox 落盘（BUILD-CONTRACT §2.1/§2.3 + F3 契约）。

golden 对照（语义级 + 字节级）：payload 由 golden 反推（去 ts；卡片决策类去
null comment 交给 server 补齐——merge_apply/merge_dismiss 例外，Mac 客户端
显式带 ``comment: null``，web 客户端照抄，两种 G1 实现口径下字节都成立）。
产物与 golden 先做 JSON 语义比较，再做 **逐字节比较**（都仅替换 ts 值——
mac_json_bytes 的 Mac JSONSerialization 复刻由此钉死）。

G1 尚未落地时（inbox_writer 仍是 stub），golden/校验用例整组 skip，
另有 StubTestCase 钉住诚实 501 行为；G1 落地后自动翻转激活。

真源冲突备注（写进测试即仲裁，集成 agent 终裁）：
- 字段全集按 F3 docs/design/inbox-actions.md（含 publish/images/session_ids/
  preset）——比 G1 stub docstring 的 webui _INBOX_KEYS 白名单宽，golden 是
  BUILD-CONTRACT 点名的验收物，以 golden 为准；
- 特形动作（split_note/set_title/answer_input/capture/...）**无 comment 键**
  （F3 §3）——G1 不得照抄 webui 的「有 id 就补 comment」规则。
"""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first
from tests.test_server_common import (GOLDEN_DIR, assert_envelope, http_request,
                                      post_json, start_server)

from server import inbox_writer

_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# Mac writeInbox 卡片决策路径的动词（恒带 comment 键；client 可省略 null）
_CARD_VERBS = frozenset({
    "approve", "reject", "comment", "defer", "raise", "trash", "restore",
    "pin", "accept", "rework", "done_external", "abort_execution",
    "stop_to_review", "revert_review", "archive", "unarchive",
})


def _inbox_writer_is_stub() -> bool:
    """探测 G1 是否已填充（stub 抛 NotImplementedError）。"""
    with tempfile.TemporaryDirectory(prefix="zai-g5-probe-") as td:
        try:
            inbox_writer.write_action({"action": "weekly_digest_now"},
                                      home=Path(td))
        except NotImplementedError:
            return True
        except Exception:
            return False
    return False


_STUB = _inbox_writer_is_stub()


def _payload_from_golden(golden: dict) -> dict:
    """golden（落盘形）反推客户端 POST payload：
    - ``ts`` 永远 server 端重打，client 不发；
    - 卡片决策类的 ``comment: null`` 由 server 补齐，client 省略；
      merge_apply/merge_dismiss 的 null 由 client 显式携带（Mac 同款）。
    """
    payload = {k: v for k, v in golden.items() if k != "ts"}
    if (golden["action"] in _CARD_VERBS
            and payload.get("comment", "sentinel") is None):
        del payload["comment"]
    return payload


class _ActionsHomeMixin:
    def _boot(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-g5-actions-"))
        _, self.port = start_server(self, self.home)
        self.inbox = self.home / "state" / "inbox"

    def _inbox_files(self):
        if not self.inbox.is_dir():
            return set()
        return {p.name for p in self.inbox.iterdir()}


@unittest.skipUnless(_STUB, "G1 landed — stub behavior no longer applies")
class StubNotImplementedTestCase(_ActionsHomeMixin, unittest.TestCase):
    """G1 落地前：/api/actions 诚实 501（web 端把 501 当「未接线」）。"""

    def setUp(self):
        self._boot()

    def test_actions_returns_501_envelope_and_writes_nothing(self):
        status, obj = post_json(self.port, "/api/actions",
                                {"action": "approve", "id": "R-101"})
        self.assertEqual(status, 501)
        assert_envelope(self, obj, "NOT_IMPLEMENTED")
        self.assertEqual(self._inbox_files(), set())


@unittest.skipIf(_STUB, "G1 inbox_writer not implemented yet — "
                        "golden tests activate when it lands")
class GoldenActionTestCase(_ActionsHomeMixin, unittest.TestCase):
    """全部 33 个 golden：每个动词/变体 → 恰好一个新 inbox 文件，语义等于 golden。"""

    def setUp(self):
        self._boot()

    def test_every_golden_fixture_round_trips(self):
        goldens = sorted(GOLDEN_DIR.glob("*.golden.json"))
        self.assertGreaterEqual(len(goldens), 33, "golden fixtures missing?")
        seen_stems = set()
        for gpath in goldens:
            with self.subTest(golden=gpath.name):
                golden = json.loads(gpath.read_text(encoding="utf-8"))
                before = self._inbox_files()
                status, obj = post_json(self.port, "/api/actions",
                                        _payload_from_golden(golden))
                self.assertEqual(status, 200,
                                 f"{gpath.name}: unexpected {status}: {obj}")
                self.assertIs(obj.get("ok"), True)
                new = self._inbox_files() - before
                self.assertEqual(len(new), 1, f"{gpath.name}: {new}")
                fname = new.pop()
                self.assertEqual(obj.get("file"), fname)
                self.assertTrue(fname.endswith(".json"))
                self.assertNotIn(fname, seen_stems)
                seen_stems.add(fname)
                # 原子写纪律：不许留 *.tmp 半截文件
                self.assertFalse([p for p in self.inbox.iterdir()
                                  if p.name.endswith(".tmp")])
                # capture 专属文件名前缀（G1 冻结规则；其余动词纯 uuid）
                if golden["action"] == "capture":
                    self.assertTrue(fname.startswith("capture-"), fname)
                produced = json.loads(
                    (self.inbox / fname).read_text(encoding="utf-8"))
                self.assertRegex(produced.get("ts", ""), _TS_RE)
                normalized = dict(produced)
                normalized["ts"] = golden["ts"]  # 仅替换 ts 值做语义比较
                self.assertEqual(normalized, golden,
                                 f"{gpath.name}: wire shape drifted")
                # 字节级对照（Mac JSONSerialization 复刻是 G1 的硬承诺）：
                # 仅替换 ts 值字节，其余必须与 golden 逐字节一致——`\/` 转义、
                # 空数组三行、`" : "` 分隔、末尾无换行全部钉死
                raw = (self.inbox / fname).read_bytes()
                raw = raw.replace(
                    f'"ts" : "{produced["ts"]}"'.encode("utf-8"),
                    f'"ts" : "{golden["ts"]}"'.encode("utf-8"), 1)
                self.assertEqual(raw, gpath.read_bytes(),
                                 f"{gpath.name}: byte layout drifted")

    def test_same_action_twice_mints_two_stems(self):
        # §34.1 幂等键 = 文件 stem：每个逻辑动作必须铸新 stem
        p = {"action": "approve", "id": "R-101"}
        _, obj1 = post_json(self.port, "/api/actions", p)
        _, obj2 = post_json(self.port, "/api/actions", p)
        self.assertNotEqual(obj1["file"], obj2["file"])
        self.assertEqual(len(self._inbox_files()), 2)


@unittest.skipIf(_STUB, "G1 inbox_writer not implemented yet")
class ActionValidationTestCase(_ActionsHomeMixin, unittest.TestCase):
    """字段/动词闸门：零容忍 400，且拒绝时绝不落盘。"""

    def setUp(self):
        self._boot()

    def _assert_rejected(self, payload: dict, code: str):
        status, obj = post_json(self.port, "/api/actions", payload)
        self.assertEqual(status, 400, f"{payload}: got {status}: {obj}")
        assert_envelope(self, obj, code)
        self.assertEqual(self._inbox_files(), set(),
                         "rejected action must not write an inbox file")

    def test_unknown_verb_rejected(self):
        self._assert_rejected({"action": "frobnicate", "id": "R-101"},
                              "INVALID_FIELD")

    def test_unknown_field_rejected(self):
        self._assert_rejected(
            {"action": "approve", "id": "R-101", "frobnicate": 1},
            "UNKNOWN_FIELD")

    def test_syncd_only_keys_rejected_on_web_inbound(self):
        # F3 §1 末条：expected_status/board_seq 只在 syncd 落的文件里，
        # 不在 web→server 入站面上——零容忍 400
        for key in ("expected_status", "board_seq"):
            with self.subTest(key=key):
                status, obj = post_json(
                    self.port, "/api/actions",
                    {"action": "approve", "id": "R-101", key: "review"})
                self.assertEqual(status, 400)
                self.assertEqual(self._inbox_files(), set())

    def test_traversal_id_rejected(self):
        for bad in ("../../../tmp/x", "R-1/../../x", ".hidden", "-dash",
                    "a" * 65, ""):
            with self.subTest(bad=bad):
                self._assert_rejected({"action": "approve", "id": bad},
                                      "INVALID_FIELD")

    def test_merge_force_shape_guard(self):
        # 去重后 <2 / primary ∉ ids → fail closed
        self._assert_rejected(
            {"action": "merge_force", "ids": ["R-1", "R-1"], "primary": "R-1"},
            "INVALID_FIELD")
        self._assert_rejected(
            {"action": "merge_force", "ids": ["R-1", "R-2"], "primary": "R-3"},
            "INVALID_FIELD")

    def test_mode_only_on_capture_run(self):
        # §34/§41：mode 仅 capture 且值恰为 "run"。动词 schema 外的键 →
        # UNKNOWN_FIELD；schema 内的键取值非法 → INVALID_FIELD（G1 口径）
        self._assert_rejected(
            {"action": "approve", "id": "R-101", "mode": "run"},
            "UNKNOWN_FIELD")
        self._assert_rejected(
            {"action": "capture", "text": "hi", "mode": "sprint"},
            "INVALID_FIELD")

    def test_client_ts_cannot_be_spoofed(self):
        # ts 一律 server 重打：拒收（零容忍）或重打皆合规，但 1999 绝不能落盘
        spoofed = "1999-01-01T00:00:00Z"
        status, _obj = post_json(
            self.port, "/api/actions",
            {"action": "approve", "id": "R-101", "ts": spoofed})
        if status == 200:
            files = list(self.inbox.glob("*.json"))
            self.assertEqual(len(files), 1)
            rec = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertNotEqual(rec["ts"], spoofed)
        else:
            self.assertEqual(status, 400)
            self.assertEqual(self._inbox_files(), set())


class BodyGateTestCase(_ActionsHomeMixin, unittest.TestCase):
    """app 层的 body 闸门（不依赖 G1，stub 期也生效）。"""

    def setUp(self):
        self._boot()

    def test_oversize_body_rejected_413(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            # 只发头不发体：server 看 Content-Length 即拒，不读 1MiB
            conn.putrequest("POST", "/api/actions")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str((1 << 20) + 1))
            conn.endheaders()
            resp = conn.getresponse()
            obj = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(resp.status, 413)
            assert_envelope(self, obj, "INVALID_FIELD")
        finally:
            conn.close()

    def test_invalid_json_rejected(self):
        status, _h, data = http_request(
            self.port, "POST", "/api/actions", body=b"{not json",
            headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        assert_envelope(self, json.loads(data.decode("utf-8")),
                        "INVALID_FIELD")

    def test_non_object_body_rejected(self):
        status, _h, data = http_request(
            self.port, "POST", "/api/actions", body=b"[1, 2]",
            headers={"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        assert_envelope(self, json.loads(data.decode("utf-8")),
                        "INVALID_FIELD")

    def test_missing_content_length_rejected(self):
        status, _h, data = http_request(self.port, "POST", "/api/actions")
        self.assertEqual(status, 400)
        assert_envelope(self, json.loads(data.decode("utf-8")),
                        "INVALID_FIELD")


if __name__ == "__main__":
    unittest.main()

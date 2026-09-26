"""registry 写入护栏的边角 —— P3b 变异轮找出来的未钉点（CONTRACT §34bis / §78）。

§78（owner 决策 D80.11，issue #447）退役了 §34bis 的提案积压清理按钮与它的
``preset`` 词表，护栏机械本体改锚在普通直跑卡（``execution.direct_run``）上；
本文件的判例随之改锚 —— 被护栏盯上的卡是**直跑卡**，不再是 preset 卡。

Covered: a failed ``guard_snapshot`` yields ``{}`` (never raises); a snapshot
payload missing ``at`` (or with a non-dict ``files``) is consumed silently — no
comparison, no alarm; the alarm lists at most five files and appends the
ellipsis only past five; the snapshot file is always consumed (also when
unreadable).
"""
import json
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd
from act.lib import config, registry
from act.lib.registry import Requirement, State


class TriageGuardEdgeBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.notify = mock.patch.object(actd.notify, "notify").start()
        self.addCleanup(mock.patch.stopall)

    def _card(self, rid="R-guard", **kw):
        # §78 改锚：护栏盯的是直跑卡（§34 mode:"run"），不再是退役的 preset 卡。
        kw.setdefault("execution", {"direct_run": True, "session_id": "sid-edge"})
        req = Requirement(id=rid, title="直跑", status=State.EXECUTING.value, **kw)
        registry.save(req)
        return req

    def _snapshot(self, payload) -> str:
        path = actd._triage_snapshot_path("R-guard")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload),
                        encoding="utf-8")
        return str(path)


class SnapshotStampTest(TriageGuardEdgeBase):
    def test_snapshot_failure_is_an_empty_dict(self):
        with mock.patch.object(registry, "guard_snapshot", side_effect=RuntimeError("db")):
            self.assertEqual(actd._registry_snapshot(), {})


class SnapshotPayloadTest(TriageGuardEdgeBase):
    def test_payload_without_at_is_consumed_without_an_alarm(self):
        card = self._card()
        ref = self._snapshot({"files": {"R-x.yaml": "1:1"}})
        ex = {"registry_snapshot_ref": ref}
        with mock.patch.object(registry, "writes_since") as writes:
            actd._check_triage_registry_guard(card, ex)
        writes.assert_not_called()
        self.notify.assert_not_called()
        self.assertNotIn("registry_snapshot_ref", ex)
        self.assertFalse(actd._triage_snapshot_path("R-guard").exists())

    def test_non_dict_files_is_consumed_without_an_alarm(self):
        card = self._card()
        ref = self._snapshot({"at": "2026-09-02T00:00:00Z", "files": ["nope"]})
        actd._check_triage_registry_guard(card, {"registry_snapshot_ref": ref})
        self.notify.assert_not_called()
        self.assertFalse(actd._triage_snapshot_path("R-guard").exists())

    def test_unreadable_snapshot_is_consumed(self):
        card = self._card()
        ref = self._snapshot("{broken json")
        actd._check_triage_registry_guard(card, {"registry_snapshot_ref": ref})
        self.notify.assert_not_called()
        self.assertFalse(actd._triage_snapshot_path("R-guard").exists())

    def _alarm_for(self, n_files: int) -> str:
        card = self._card()
        before = {f"R-{i}.yaml": "1:1" for i in range(n_files)}
        ref = self._snapshot({"at": "2026-09-02T00:00:00Z", "files": before})
        after = {name: "2:2" for name in before}
        with mock.patch.object(registry, "guard_snapshot", return_value=after), \
                mock.patch.object(registry, "writes_since", return_value=set()):
            actd._check_triage_registry_guard(card, {"registry_snapshot_ref": ref})
        self.notify.assert_called_once()
        return card.notes   # the guard writes the in-memory card; the caller saves

    def test_alarm_lists_five_files_without_ellipsis(self):
        notes = self._alarm_for(5)
        self.assertIn("R-0.yaml, R-1.yaml, R-2.yaml, R-3.yaml, R-4.yaml —— 会话按律只读", notes)
        self.assertNotIn("…", notes)

    def test_alarm_truncates_past_five_with_an_ellipsis(self):
        notes = self._alarm_for(6)
        self.assertIn("R-0.yaml, R-1.yaml, R-2.yaml, R-3.yaml, R-4.yaml… —— 会话按律只读", notes)
        self.assertNotIn("R-5.yaml", notes)


if __name__ == "__main__":
    unittest.main()

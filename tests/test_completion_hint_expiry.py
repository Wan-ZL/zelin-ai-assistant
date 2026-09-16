"""§76.1 「疑似已完成」只描述当前这一轮：回锅与批准都清章。

PR #349 评审抓到的洞：`completion_hint` 原本只有写、没有任何清除路径，于是
一张 delivered 卡在两周后被重述回锅（`registry._reraise`）时，会带着两周前
的绿章、那句旧证据和一颗「已办完 · 记为已交付」一键回到提案列——正是 issue
#313 要消灭的「看板和现实脱节」，只是方向反了。判例钉两条清除路：

* **回锅**（`registry._reraise`）= 新一轮诉求 -> 章清空（其余回锅簿记不变：
  sources / 被提数 / `[re-raised]` 备注 / `reraised_at` 照旧）；
* **批准**（`decisions._approve`）= owner 看着提示仍然要做 -> 章当场作废，
  因此日后「退回提案」/ 评论重批把卡送回提案列时不会带着旧章回来。

顺带钉住「暂缓不清章」：`card_sent -> detected` 只是换了一列，提示在债务列
仍然是最新的那条证据（§76.2 的备选列投影读它）。
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd
from act.lib import config, registry
from act.lib.registry import Requirement, State

_HINT = {"at": "2026-09-01T10:00:00Z", "note": "Compass repo 已建", "channel": "meeting"}


def _card(rid, status, **kw) -> Requirement:
    req = Requirement(id=rid, title="把 Strawberry 改名 Compass", status=status, **kw)
    registry.save(req)
    return req


def _clean():
    config.ensure_state_dirs()
    for path in (list(config.REGISTRY_DIR.glob("*.yaml"))
                 + list(registry.ARCHIVE_DIR.glob("*.yaml"))):
        path.unlink()


class CompletionHintExpiryTestCase(unittest.TestCase):
    def setUp(self):
        _clean()
        self.addCleanup(_clean)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(actd.notify, "notify").start()
        mock.patch.object(actd, "_log").start()

    def test_a_re_raise_drops_the_stale_hint(self):
        parent = _card("P-023", State.DELIVERED.value, completion_hint=dict(_HINT))
        restatement = Requirement(id="", title="把 Strawberry 改名 Compass",
                                  hardness="hard", summary="还要改 slides")
        kind, saved = registry.reraise_or_followup(parent, restatement, same_task=True,
                                                  note="又被提起")
        self.assertEqual((kind, saved.status), ("reraised", State.CARD_SENT.value))
        self.assertIsNone(saved.completion_hint)
        self.assertIsNone(registry.load("P-023").completion_hint)
        # 回锅本身的簿记一字不动
        self.assertIn("[re-raised] 又被提起", saved.notes)
        self.assertEqual(saved.repeated_mentions, 2)
        self.assertEqual(saved.execution.get("reraised_note"), "又被提起")

    def test_approving_voids_the_hint(self):
        req = _card("P-024", State.CARD_SENT.value, completion_hint=dict(_HINT),
                    plan="第一步", definition_of_done=["能用"])
        self.assertEqual(actd._apply_decision(req, "approve", None), "running")
        saved = registry.load("P-024")
        self.assertEqual(saved.status, State.APPROVED.value)
        self.assertIsNone(saved.completion_hint)

    def test_defer_keeps_it_because_the_backlog_lane_shows_it(self):
        req = _card("P-025", State.CARD_SENT.value, completion_hint=dict(_HINT))
        self.assertEqual(actd._apply_decision(req, "defer", None), "running")
        saved = registry.load("P-025")
        self.assertEqual(saved.status, State.DETECTED.value)
        self.assertEqual(saved.completion_hint, _HINT)


if __name__ == "__main__":
    unittest.main()

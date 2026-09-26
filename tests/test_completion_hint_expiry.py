"""§76.1 「疑似已完成」只描述当前这一轮：回锅与批准都清章。

PR #349 评审抓到的洞：`completion_hint` 原本只有写、没有任何清除路径，于是
一张 delivered 卡在两周后被重述回锅（`registry._reraise`）时，会带着两周前
的绿章、那句旧证据和一颗「已办完 · 记为已交付」一键回到 owner 的决策列——正是
issue #313 要消灭的「看板和现实脱节」，只是方向反了。判例钉两条清除路：

* **回锅**（`registry._reraise`）= 新一轮诉求 -> 章清空（其余回锅簿记不变：
  sources / 被提数 / `[re-raised]` 备注 / `reraised_at` 照旧）；
* **批准**（`decisions._approve`）= owner 看着提示仍然要做 -> 章当场作废，
  因此日后「退回潜在任务」（§4.1 abort）/ 评论重批把卡送回来时不会带着旧章。

**§78（issue #447 / owner 决策 D80）**：提案车道退役，上面两条路的**落点**都
改成 `detected`（潜在任务）——回锅是 `delivered -> detected`，白名单行既有。
清章的判据、时机、范围一字未动：变的只有卡落在哪一列。

顺带钉住「暂缓不清章」：`defer`（retired §78，动词保留）把一张尚未被归并扫描
搬走的 `card_sent` 落单卡挪进 `detected` 时**不清章**——只是换了一列，提示仍是
最新的那条证据（§76.2 §78 修法之后，潜在任务行照发它）。
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
        # §78：回锅的落点是潜在任务（delivered -> detected），不再是提案列
        self.assertEqual((kind, saved.status), ("reraised", State.DETECTED.value))
        self.assertIsNone(saved.completion_hint)
        self.assertIsNone(registry.load("P-023").completion_hint)
        # 回锅本身的簿记一字不动
        self.assertIn("[re-raised] 又被提起", saved.notes)
        self.assertEqual(saved.repeated_mentions, 2)
        self.assertEqual(saved.execution.get("reraised_note"), "又被提起")

    def test_approving_voids_the_hint(self):
        req = _card("P-024", State.DETECTED.value, completion_hint=dict(_HINT),
                    plan="第一步", definition_of_done=["能用"])
        self.assertEqual(actd._apply_decision(req, "approve", None), "running")
        saved = registry.load("P-024")
        self.assertEqual(saved.status, State.APPROVED.value)
        self.assertIsNone(saved.completion_hint)

    def test_defer_on_a_retired_straggler_keeps_it(self):
        """`defer`（retired §78，D80 / §10 §78 追记）唯一的来源态 `card_sent`
        已经退役，动词本身**保留不删**（迟到 / 重放的 inbox 文件还会发它）：
        落单卡照旧被挪进潜在任务列，而绿章**不清**——它在那一列仍然是最新的
        那条证据，清掉就是把 owner 刚看见的事实抹掉。"""
        req = _card("P-025", State.CARD_SENT.value, completion_hint=dict(_HINT))
        self.assertEqual(actd._apply_decision(req, "defer", None), "running")
        saved = registry.load("P-025")
        self.assertEqual(saved.status, State.DETECTED.value)
        self.assertEqual(saved.completion_hint, _HINT)


if __name__ == "__main__":
    unittest.main()

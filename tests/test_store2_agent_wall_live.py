"""agent 转移墙成为 actd 级现实的判例（CONTRACT §53.5 / R2.1.4）。

「agent 不得批准/验收」从休眠 schema trigger 变为实际生效：actd 按 inbox
决策的 ingress 落款（via:"agent"）以 agent actor 应用动作——approve/accept
这类状态转移在 registry.save 处被 TransitionDenied 拒绝（sqlite = schema
trigger + Python 墙双层；yaml 回滚窗口 = Python 墙同款），卡纹丝不动，
inbox 文件按干净 no-op ack 收尾（不是 poison）。owner 面（无 via / web）
照常放行。
"""
import json
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports
from tests import store2_testkit

from act import actd
from act.lib import config, registry
from act.lib.registry import Requirement


def _drop_decision(body: dict, stem: str = "t-agent-1") -> None:
    config.INBOX_DIR.mkdir(parents=True, exist_ok=True)
    (config.INBOX_DIR / f"{stem}.json").write_text(
        json.dumps(body, ensure_ascii=False), encoding="utf-8")


class AgentWallLiveTestCase(unittest.TestCase):
    def _seed_card(self):
        registry.upsert(Requirement(id="R-100", title="墙前的提案",
                                    status="card_sent"))

    def _acks(self):
        acked = {}
        for p in (config.STATE_DIR / "sync" / "acks").glob("*.json"):
            try:
                acked[p.stem] = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
        return acked

    def test_agent_approve_is_refused_on_sqlite(self):
        store2_testkit.use_backend(self, "sqlite")
        self._seed_card()
        _drop_decision({"id": "R-100", "action": "approve", "via": "agent"})
        actd.process_inbox()
        self.assertEqual(registry.load("R-100").status, "card_sent")
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])

    def test_agent_approve_is_refused_on_yaml_too(self):
        # 回滚窗口内墙不消失（Python 面同款，R2.1.4）
        store2_testkit.use_backend(self, "yaml")
        self._seed_card()
        _drop_decision({"id": "R-100", "action": "approve", "via": "agent"})
        actd.process_inbox()
        self.assertEqual(registry.load("R-100").status, "card_sent")

    def test_owner_approve_still_flows(self):
        store2_testkit.use_backend(self, "sqlite")
        self._seed_card()
        _drop_decision({"id": "R-100", "action": "approve"})   # Mac 形无 via
        actd.process_inbox()
        self.assertEqual(registry.load("R-100").status, "approved")

    def test_agent_accept_is_refused(self):
        store2_testkit.use_backend(self, "sqlite")
        registry.upsert(Requirement(id="R-101", title="待验收", status="review",
                                    execution={"done": True}))
        _drop_decision({"id": "R-101", "action": "accept", "via": "agent"},
                       stem="t-agent-2")
        actd.process_inbox()
        self.assertEqual(registry.load("R-101").status, "review")


if __name__ == "__main__":
    unittest.main()

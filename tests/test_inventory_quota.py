"""W1 inverted inventory quota (docs/design/vnext-amendments.md §W1).

triage/capture 清单窗口的配额反转：open 卡保证槽位永不掉出窗口；
delivered/merged 按 recency 填剩余空位且受 _CLOSED_RECENCY_CAP 硬上限。
另钉 auto-archive 配置默认值 archive_after_days = 30（§W1.c）。
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act.lib import config, quick_capture, registry


def _mk(idnum: int, status: str) -> registry.Requirement:
    req = registry.Requirement(
        id=f"R-{idnum:03d}",
        title=f"card {idnum}",
        type="other",
        status=status,
    )
    registry.save(req)
    return req


class InventoryQuotaTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        if config.REGISTRY_DIR.exists():
            for p in config.REGISTRY_DIR.glob("*.yaml"):
                p.unlink()

    def test_open_cards_guaranteed_and_closed_recency_capped(self):
        # 10 open + 30 delivered：open 全保留；delivered 只留 recency 最高的
        # _CLOSED_RECENCY_CAP 张（R-021..R-040），R-011..R-020 被挤出。
        for i in range(1, 11):
            _mk(i, registry.State.CARD_SENT.value)
        for i in range(11, 41):
            _mk(i, registry.State.DELIVERED.value)

        ids = [r.id for r in quick_capture._inventory_reqs()]
        for i in range(1, 11):
            self.assertIn(f"R-{i:03d}", ids)
        self.assertEqual(
            sum(1 for r in quick_capture._inventory_reqs()
                if r.status == registry.State.DELIVERED.value),
            quick_capture._CLOSED_RECENCY_CAP,
        )
        self.assertIn("R-040", ids)   # 最新的 delivered 在窗
        self.assertNotIn("R-011", ids)  # 最老的 delivered 被挤出

    def test_open_overflow_never_drops_open_and_starves_closed(self):
        # open 超过 _INVENTORY_CAP：窗口整体超 cap（open 是保证槽位），
        # closed 一张都进不来（room = 0）。
        n_open = quick_capture._INVENTORY_CAP + 5
        for i in range(1, n_open + 1):
            _mk(i, registry.State.DETECTED.value)
        for i in range(n_open + 1, n_open + 4):
            _mk(i, registry.State.DELIVERED.value)

        selected = quick_capture._inventory_reqs()
        self.assertEqual(len(selected), n_open)
        self.assertTrue(all(r.status == registry.State.DETECTED.value
                            for r in selected))

    def test_merged_counts_as_closed_and_trashed_excluded(self):
        _mk(1, registry.State.EXECUTING.value)
        _mk(2, registry.State.DELIVERED.value)
        _mk(3, f"{registry.MERGED_PREFIX}R-002")
        _mk(4, registry.State.TRASHED.value)

        ids = [r.id for r in quick_capture._inventory_reqs()]
        self.assertEqual(ids, ["R-001", "R-002", "R-003"])  # 有余位则 closed 也进窗；trashed 永不
        self.assertNotIn("R-004", ids)

    def test_inventory_text_renders_selected_window(self):
        _mk(1, registry.State.REVIEW.value)
        text = quick_capture.registry_inventory_text()
        self.assertIn("R-001 | review | card 1", text)

    def test_inventory_text_empty_registry(self):
        self.assertEqual(quick_capture.registry_inventory_text(),
                         "(registry is empty)")


class StarvationScenarioTestCase(unittest.TestCase):
    """W1 病根复现（critique 场景）：100 张 delivered + 少量 open 卡。

    live v0.20.0 的旧配额把 delivered/merged 硬钉进 60 窗口、open 卡抢剩余
    槽位——delivered 一旦破百，open 卡（triage 唯一需要对账的对象）会被挤出
    清单，follow-up 认卡全靠 LLM 记忆。反转后的不变量：**open 卡永不掉窗**，
    delivered 只按 recency 吃 _CLOSED_RECENCY_CAP 以内的剩余空位。
    """

    def setUp(self):
        config.ensure_state_dirs()
        if config.REGISTRY_DIR.exists():
            for p in config.REGISTRY_DIR.glob("*.yaml"):
                p.unlink()

    def test_100_delivered_cannot_starve_open_cards(self):
        open_statuses = [
            registry.State.DETECTED.value, registry.State.CARD_SENT.value,
            registry.State.RAISING.value, registry.State.APPROVED.value,
            registry.State.EXECUTING.value, registry.State.REVIEW.value,
            registry.State.CARD_SENT.value, registry.State.REVIEW.value,
        ]
        for i, status in enumerate(open_statuses, start=1):
            _mk(i, status)
        for i in range(9, 109):                       # 100 张 delivered
            _mk(i, registry.State.DELIVERED.value)

        selected = quick_capture._inventory_reqs()
        ids = [r.id for r in selected]
        # 反转不变量：8 张 open 全部在窗——旧配额下它们会被 100 张 delivered
        # 挤出（60 窗口装不下 100 张硬钉的 closed，遑论 open）。
        for i in range(1, 9):
            self.assertIn(f"R-{i:03d}", ids)
        # delivered 只吃 recency 硬上限内的空位：恰 20 张、且是最新的 20 张
        delivered = [r.id for r in selected
                     if r.status == registry.State.DELIVERED.value]
        self.assertEqual(len(delivered), quick_capture._CLOSED_RECENCY_CAP)
        self.assertEqual(delivered,
                         [f"R-{i:03d}" for i in range(89, 109)])
        self.assertEqual(len(selected), 8 + quick_capture._CLOSED_RECENCY_CAP)
        # triage prompt 里 open 卡逐行可见——follow-up 认卡不靠 LLM 记忆
        text = quick_capture.registry_inventory_text()
        for i in range(1, 9):
            self.assertIn(f"R-{i:03d} | ", text)

    def test_near_full_open_window_shrinks_closed_share_below_cap(self):
        # 55 张 open + 100 张 delivered：closed 份额 = min(60-55, 20) = 5——
        # recency cap 是上限不是配额，剩余空位说了算。
        for i in range(1, 56):
            _mk(i, registry.State.CARD_SENT.value)
        for i in range(56, 156):
            _mk(i, registry.State.DELIVERED.value)
        selected = quick_capture._inventory_reqs()
        self.assertEqual(len(selected), quick_capture._INVENTORY_CAP)
        delivered = [r.id for r in selected
                     if r.status == registry.State.DELIVERED.value]
        self.assertEqual(delivered, [f"R-{i:03d}" for i in range(151, 156)])


class ArchiveAfterDaysDefaultTestCase(unittest.TestCase):
    def test_default_is_30_days(self):
        # vnext §W1.c：默认 30（live v0.20.0 是 0/off）
        self.assertEqual(config.load_config().archive_after_days, 30)

    def test_yaml_zero_turns_it_off(self):
        config.CONFIG_PATH.write_text("archive:\n  after_days: 0\n",
                                      encoding="utf-8")
        self.addCleanup(config.CONFIG_PATH.unlink)
        self.assertEqual(config.load_config().archive_after_days, 0)

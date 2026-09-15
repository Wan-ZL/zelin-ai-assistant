"""§2 追记 / D74（issue #312）——待验收行的 add-only 键 `self_improve`。

owner 的待验收列 19 张里 12 张是机器卡；列头要能把它们藏起来，而过滤器只许约束
**结构上携带该字段**的行（web/src/taskFilters.ts 的跨分区语义）。所以判据落在 server：
来源**全部**是 `self_improve` 渠道的行发 `self_improve: true`，其余行**整键不出**
（缺席 ≠ false —— 老 server 的行不该被当成人卡藏掉，也不该被当成机器卡藏掉）。
判据单源 = `policy.is_self_improve_sources`（混入任何别的渠道即失格）。
Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import config, dashboard
from act.lib.registry import Requirement, State


def _review(rid, sources):
    return Requirement(id=rid, title=f"draft {rid}", status=State.REVIEW.value,
                       sources=sources,
                       execution={"review_at": "2026-09-01T09:00:00Z",
                                  "delivered_summary": "done"})


def _row(req):
    dash = dashboard.build_dashboard(reqs=[req], agents=[], cfg=config.Config(), archived=[])
    return dash["review"][0]


class ReviewSelfImproveFlagTestCase(unittest.TestCase):
    def test_an_all_self_improve_card_carries_the_flag(self):
        row = _row(_review("P-1", [{"channel": "self_improve", "date": "2026-09-01",
                                    "ref": "self_improve:abc", "who": "daily_loop"}]))
        self.assertIs(row["self_improve"], True)

    def test_a_human_card_omits_the_key_entirely(self):
        row = _row(_review("P-2", [{"channel": "slack", "date": "2026-09-01", "quote": "q"}]))
        self.assertNotIn("self_improve", row)

    def test_a_mixed_origin_card_is_not_a_bot_card(self):
        """混合来源取最小信任（policy.is_self_improve_sources）：搭便车两个方向都关死。"""
        row = _row(_review("P-3", [{"channel": "self_improve", "date": "2026-09-01",
                                    "ref": "self_improve:abc"},
                                   {"channel": "slack", "date": "2026-09-02", "quote": "q"}]))
        self.assertNotIn("self_improve", row)

    def test_a_sourceless_card_omits_the_key(self):
        self.assertNotIn("self_improve", _row(_review("P-4", [])))


if __name__ == "__main__":
    unittest.main()

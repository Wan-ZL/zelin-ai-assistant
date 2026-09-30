"""§80.3 会话重新活动过 -> 交付探针的 120s 节流戳作废，下次 blocked 立刻探。

病灶（issue #450，owner 原话「running 完成后进入 review」那一段白等）：
`HARVEST_PROBE_AT` 记的是「上次读 transcript 时还没有 FINAL DRAFT」，而戳一旦
盖下，120s 内不再读。于是这条真实路径会出问题：

  T0     会话提了个问题 -> blocked -> 探针空手、盖戳
  T0     排队的 briefing/steer 被 flush -> 会话被 resume，继续干活
  T0+30  会话打完 FINAL DRAFT -> 又 blocked
  T0+30  探针**被自己 30 秒前的戳挡住** -> 卡不提升

而且不只是晚 90s：`_another_move_left` 这时已经没有别的出路了，于是
`_handle_blocked` 会按「[会话受阻]」把卡收进待验收——一次**成功交付**被记成
中断收割。reconcile.py 里 TITLE_PROBE_AT 上方那段注释早就点名了这个后果
（「比晚 120 s 更糟」），只是当时只为改名探针留了第二本台账。

判例：
  - 节流本身不变：同一条 blocked 会话背靠背两次探针，第二次仍被挡（120s 防的
    是 10s 一个 pass 反复重读同一条 transcript，一点没放宽）；
  - 中间只要会话被看见活过一次，戳作废 -> 下一次 blocked 的探针立刻跑，看到
    FINAL DRAFT 就干净提升，而不是被记成「会话受阻」；
  - `_clear_harvest_throttle` 对没盖过戳的 sid 是干净 no-op；
  - 一直 blocked（从不活过来）的会话走不到 `_note_alive`，节流照旧生效。

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import os
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd
from act.lib import config, registry
from act.lib.actd import reconcile
from act.lib.registry import Requirement, State

SID = "aaaa1111"
FULL_SID = "aaaa1111-0000-4000-8000-000000000001"


def _agent(state, pid=42):
    return {"id": SID, "sessionId": FULL_SID, "state": state, "cwd": "/tmp/wt",
            "name": "bg agent", "startedAt": "2026-07-08T00:00:00Z", "pid": pid}


class HarvestThrottleTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.cfg = config.Config()
        reconcile.HARVEST_PROBE_AT.clear()
        reconcile.TITLE_PROBE_AT.clear()
        self.addCleanup(reconcile.HARVEST_PROBE_AT.clear)
        self.addCleanup(reconcile.TITLE_PROBE_AT.clear)
        p = mock.patch.object(actd.notify, "notify", mock.Mock(return_value=True))
        p.start()
        self.addCleanup(p.stop)
        lang = mock.patch.dict(os.environ, {"AIASSISTANT_UI_LANG": "zh"})
        lang.start()
        self.addCleanup(lang.stop)

    def _mk_req(self):
        req = Requirement(id="R-901", title="节流判例", status=State.EXECUTING.value,
                          execution={"session_id": SID})
        registry.save(req)
        return req

    def _probe(self, req, harvested):
        """一次 promote_if_delivered；harvested = 假的 harvest_delivery 返回值。"""
        ex = dict(req.execution or {})
        ex_out = ex
        with mock.patch.object(actd, "executor") as ex_mod:
            ex_mod.harvest_delivery.return_value = harvested
            promoted = actd._promote_if_delivered(req, ex_out, SID)
        return promoted

    def _live_pass(self):
        """一个 live roster 的真 reconcile pass（跑到 `_note_alive`）。

        TITLE_PROBE_AT 预先盖戳，让 §37.1 的改名探针这一轮不开火——两本台账
        分开正是 reconcile 的既有设计，这里顺便钉住「清的是交付那一本」。
        """
        reconcile.TITLE_PROBE_AT[SID] = reconcile.time.monotonic()
        with mock.patch.object(actd, "_run_claude_agents",
                               return_value=[_agent("working")]), \
             mock.patch.object(actd, "executor") as ex_mod:
            ex_mod.resume.return_value = True
            actd.reconcile_executing(self.cfg, set())

    # ----------------------------------------------------------------- #
    def test_back_to_back_probes_on_a_blocked_session_stay_throttled(self):
        # 节流没有被放宽：同一条 blocked 会话，第二次探针仍被 120s 窗口挡住
        req = self._mk_req()
        self.assertFalse(self._probe(req, {}))          # 探针 #1：空手、盖戳
        self.assertFalse(self._probe(req, {"final_draft": "交付了"}))
        self.assertEqual(registry.load("R-901").status, State.EXECUTING.value)

    def test_activity_invalidates_the_stamp_so_the_next_probe_runs(self):
        # 这是 issue #450 那一段白等（外加「成功交付被记成会话受阻」）的判例
        req = self._mk_req()
        self.assertFalse(self._probe(req, {}))          # T0：提问 -> 空手、盖戳
        self._live_pass()                               # steer flush -> 会话活了
        self.assertNotIn(SID, reconcile.HARVEST_PROBE_AT)
        req = registry.load("R-901")
        self.assertTrue(self._probe(req, {"final_draft": "交付了"}))
        self.assertEqual(registry.load("R-901").status, State.REVIEW.value)

    def test_clearing_an_unstamped_session_is_a_clean_noop(self):
        reconcile._clear_harvest_throttle("never-stamped")
        self.assertEqual(reconcile.HARVEST_PROBE_AT, {})

    def test_clear_only_touches_the_delivery_ledger(self):
        reconcile.HARVEST_PROBE_AT[SID] = 1.0
        reconcile.TITLE_PROBE_AT[SID] = 2.0
        reconcile._clear_harvest_throttle(SID)
        self.assertNotIn(SID, reconcile.HARVEST_PROBE_AT)
        self.assertEqual(reconcile.TITLE_PROBE_AT[SID], 2.0)   # 改名那本不动

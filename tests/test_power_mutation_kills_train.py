"""§71.1 派发闸判决里夜报变异体活下来的那几格（act/lib/power.py）。

判例 tests/test_power_probe.py 钉住了主干（真机 fixture 的解析、两条判据并列、
fail-open、`require_awake` 旋钮、缓存跨睡眠作废），但它喂的读数全是「形状对、
数值正常」的那一类——夜间变异（§57）因此在**判据的边界**上留了一串存活体：
`_positive` 的 `> 0`、`_root_verdict` 的 `ceiling > 0`、`_memo_fresh` 的两个
时钟各自的两个端点，改一个字判例照样全绿。

这里钉的是 §71.1 那三句法条本身，不是它们的写法：

* **只有正证据才判 asleep，也只有正证据才判 awake**：`UserIsActive: 0`（真机
  fixture `pmset_assertions_arm64.txt` 里就是这个值）是「没人摁着」，不是「有人
  摁着」——把 0 读成证据 = 闸在 owner 那台 MacBook 上永远响不了，#311 原地复发。
* **「没有答案」不等于「醒着」**：IOPMrootDomain 报不出满档（`max_state` 为 0）
  时主判据必须交白卷，让整条判决落到 `unknown`（fail-open，卡照派），而不是
  伪造一个 `awake`；反过来，两档域（`max_state` 为 1）是**真答案**，不许被当成
  「探不到」丢掉。
* **缓存新鲜 = 两个时钟都新鲜、且都没回拨**：任一时钟过期或回拨都作废。半份
  基线（任一时钟缺席）同样不算新鲜——绝不拿只有一半基线的缓存去判 asleep。

**两个体判为等价（可达输入上无可观察差异，不强杀）**：

* `_memo_fresh` 的 `return False`（`→ return None`）：私名谓词，两个消费者
  （`current_verdict` 的 `if`、`observed_verdict` 的三元）都只取它的真假值，
  None 与 False 同为假值。（公开谓词 `held_awake` 不在此列——它的 `return False`
  在公开面上看得见，下面用 `assertIs` 管住。）
* `_read` 的 `return {}`（`→ return None`）：私名，唯一消费者是
  `verdict(_read(probe))`，而 `verdict` 对 `{}` 与对非 dict 同样答 `unknown`
  （空读数和垃圾读数本来就是同一件事：探不到）。
"""
from __future__ import annotations

import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env before any act import

from act.lib import config, power, registry
from act.lib.registry import Requirement, State

# 真机 dark wake 的形状：IOPMrootDomain 仍报满档，System Capabilities 缺 Graphics
# 位（tests/fixtures/power/pmset_log_darkwake_arm64.txt 钉住的那一位）。
DARK_WAKE = {"state": 4, "max_state": 4, "capabilities": 0x1}
FULL_AWAKE = {"state": 4, "max_state": 4, "capabilities": 0xF, "user_active": True}


class _Ctx:
    """`credit_sleep` / `sample_pass` 的 `d` 注入缝（actd 主循环那一把，§44
    单写者：落账只经 `d.save`）。"""

    def __init__(self):
        self.saved = []
        self.logs = []

    def save(self, req):
        self.saved.append(req)

    def log(self, message):
        self.logs.append(message)


def _card(req_id: str, status: str = State.EXECUTING.value, execution=None):
    req = Requirement(id=req_id, title="%s sleep ledger" % req_id, status=status,
                      execution={"session_id": "sid-1"} if execution is None
                      else execution)
    registry.save(req)
    return req


class PositiveEvidenceTestCase(unittest.TestCase):
    """`held_awake`：只有**正数**的 assertion 计数才是「有人摁着这台机器」。"""

    def test_a_zero_user_is_active_count_is_not_evidence(self):
        # 真机 fixture 里 `UserIsActive` 就是 0（没人在用）。把 0 读成证据 =
        # 这台机器永远判 awake，§71.1 的闸变成看不见的 no-op。
        dark = dict(DARK_WAKE, assertions={"UserIsActive": 0})
        self.assertIs(power.held_awake(dark), False)
        self.assertEqual(power.verdict(dark), power.ASLEEP)

    def test_a_positive_count_is_evidence(self):
        held = dict(DARK_WAKE, assertions={"UserIsActive": 1})
        self.assertIs(power.held_awake(held), True)

    def test_a_boolean_true_is_not_a_count(self):
        # `pmset -g assertions` 的系统块给的是计数；True 是解析漂移，不是证据
        # （`_positive` 显式排除 bool）。
        self.assertIs(power.held_awake(dict(DARK_WAKE,
                                            assertions={"UserIsActive": True})),
                      False)

    def test_the_predicate_answers_a_real_bool_not_none(self):
        # 公开谓词答 None = 把「判不了」和「判成假」混成一件事（`assertFalse`
        # 一类的松断言接受 None，所以这里逐个 assertIs）。
        for reading in ({}, {"assertions": None}, {"assertions": "garbage"},
                        {"user_active": False}):
            self.assertIs(power.held_awake(reading), False, reading)


class RootDomainCeilingTestCase(unittest.TestCase):
    """`_root_verdict`：没有满档 = 没有答案；两档域仍然是答案。"""

    def test_a_zero_ceiling_is_no_answer_not_awake(self):
        # ioreg / pmset 给不出满档（解析漂移、字段缺席）时主判据必须交白卷，
        # 整条判决落到 unknown（fail-open：卡照派）。伪造一个 awake 会让
        # 「探不到」永远说不出口——doctor 的 `power probe` 行也就永远是 OK。
        self.assertEqual(power.verdict({"state": 0, "max_state": 0}),
                         power.UNKNOWN)

    def test_a_two_step_domain_is_a_real_answer(self):
        # 满档为 1 的机器（两档域）不许被当成「探不到」丢掉：0 < 1 = 不在满醒。
        self.assertEqual(power.verdict({"state": 0, "max_state": 1}),
                         power.ASLEEP)
        self.assertEqual(power.verdict({"state": 1, "max_state": 1}),
                         power.AWAKE)


class MemoFreshnessTestCase(unittest.TestCase):
    """判决缓存的新鲜判据：两个时钟、两个方向、四个端点。"""

    def setUp(self):
        power.reset_probe_memo()
        self.addCleanup(power.reset_probe_memo)
        # 播一个判决进缓存（monotonic 1000、wall 50000）
        power.current_verdict(probe=lambda: {"state": 4, "max_state": 4},
                              now=1000.0, wall=50000.0)

    def _observed(self, now: float, wall: float):
        return power.observed_verdict(now=now, wall=wall)

    def test_both_clocks_are_fresh_inside_the_window(self):
        self.assertEqual(self._observed(1000.0 + power.MEMO_SECONDS - 1,
                                        50000.0 + power.MEMO_SECONDS - 1),
                         power.AWAKE)

    def test_the_window_edge_is_already_stale_on_either_clock(self):
        # 半开区间 `[0, MEMO_SECONDS)`：正好到点那一刻就不新鲜了，两个时钟各算各的。
        self.assertIsNone(self._observed(1000.0 + power.MEMO_SECONDS, 50000.0))
        self.assertIsNone(self._observed(1000.0, 50000.0 + power.MEMO_SECONDS))

    def test_either_clock_going_backwards_voids_the_memo(self):
        # NTP 校时 / 手工改钟 / 睡醒后的时钟跳变都能让差值变负——负的「新鲜度」
        # 不是新鲜，是不知道过了多久（宁可重探三个子进程，也不拿睡前的判决派卡）。
        self.assertIsNone(self._observed(999.0, 50000.0))
        self.assertIsNone(self._observed(1000.0, 49999.0))

    def test_half_a_baseline_is_never_fresh(self):
        # 两个时钟都记是 §71.1 的原话（只看 monotonic 会漏掉整场睡眠）。任一
        # 时钟缺席 = 这份缓存判不了新鲜，必须重探；绝不半读一个基线。
        for half in ({"at": 1000.0, "wall": None}, {"at": None, "wall": 50000.0}):
            power._MEMO.update(dict(half, verdict=power.AWAKE))
            self.assertIsNone(self._observed(1000.0, 50000.0), half)


class DispatchGateTestCase(unittest.TestCase):
    """§71.1 派发闸本身：答一个真 bool，且**判决变化时**才写日志。"""

    def setUp(self):
        power.reset_probe_memo()
        self.addCleanup(power.reset_probe_memo)
        self.cfg = config.Config()
        self.off = config.Config(raw={"autodispatch": {"require_awake": False}})

    def test_the_gate_answers_a_real_bool_in_every_branch(self):
        # 闸的返回值直接决定「这一 pass 派不派卡」——答 None 就是把「不知道」
        # 和「醒着」混成一件事（松断言看不出来，所以逐个 assertIs）。
        self.assertIs(power.machine_asleep(self.off, probe=lambda: DARK_WAKE), False)
        power.reset_probe_memo()
        self.assertIs(power.machine_asleep(self.cfg, probe=lambda: DARK_WAKE), True)
        power.reset_probe_memo()
        self.assertIs(power.machine_asleep(self.cfg, probe=lambda: FULL_AWAKE), False)
        power.reset_probe_memo()
        self.assertIs(power.machine_asleep(self.cfg, probe=dict), False)  # unknown

    def test_a_missing_log_seam_is_simply_no_log(self):
        # `log` 是可选的；没给就不写——不是拿 None 当函数调。
        self.assertIs(power.machine_asleep(self.cfg, probe=lambda: DARK_WAKE), True)

    def test_the_line_is_written_once_per_change_not_once_per_pass(self):
        # 派发 pass 每 10 s 一轮：每 pass 一行会把 actd.log 刷满，所以只有判决
        # **变化**时才说话。反过来，第一次探出来（before 还是空）必须说。
        lines = []
        self.assertIs(power.machine_asleep(self.cfg, probe=lambda: DARK_WAKE,
                                           log=lines.append), True)
        self.assertEqual(len(lines), 1)
        self.assertIn(power.ASLEEP, lines[0])
        self.assertIs(power.machine_asleep(self.cfg, probe=lambda: DARK_WAKE,
                                           log=lines.append), True)
        self.assertEqual(len(lines), 1)

    def test_an_unreadable_probe_says_so_and_names_the_fail_open(self):
        # 探不出状态也要说话——不然一个永远解析不出的探针会让整条闸静默成 no-op。
        lines = []
        self.assertIs(power.machine_asleep(self.cfg, probe=dict, log=lines.append),
                      False)
        self.assertEqual(len(lines), 1)
        self.assertIn(power.UNKNOWN, lines[0])
        self.assertIn("fail-open", lines[0])


class SuspensionSamplingTestCase(unittest.TestCase):
    """§71.2 挂起采样：两个注入时钟各自记基线，半份基线不许被半读。"""

    def setUp(self):
        self._reset()
        self.addCleanup(self._reset)

    @staticmethod
    def _reset():
        power.SUSPEND_STATE.update({"last_wall": None, "last_mono": None})

    def test_the_first_sample_is_zero_seconds_not_none(self):
        # 返回值会直接进 `slept >= SLEEP_GAP_SECONDS` 的比较——答 None 会把
        # 整个 pass 的记账炸成 TypeError。
        self.assertEqual(power.sample_suspension(wall=1000.0, mono=10.0), 0.0)

    def test_the_gap_is_the_wall_advance_minus_the_monotonic_advance(self):
        power.sample_suspension(wall=1000.0, mono=10.0)
        # 这一 pass 真跑了 60 s，机器另外睡了 540 s
        self.assertEqual(power.sample_suspension(wall=1600.0, mono=70.0), 540.0)

    def test_half_a_baseline_is_not_a_baseline(self):
        # 两个时钟是一对：任一缺席（老版本的半份 state / 新增字段尚未回填）就
        # 只能重新起基线，绝不拿另一半去做减法。
        power.SUSPEND_STATE.update({"last_wall": 1000.0, "last_mono": None})
        self.assertEqual(power.sample_suspension(wall=1600.0, mono=70.0), 0.0)
        power.SUSPEND_STATE.update({"last_wall": None, "last_mono": 10.0})
        self.assertEqual(power.sample_suspension(wall=1600.0, mono=70.0), 0.0)


class SleepCreditTestCase(unittest.TestCase):
    """§71.2 落账：门槛是闭区间的，扫描必须扫完整本台账。"""

    def setUp(self):
        config.ensure_state_dirs()
        for path in config.REGISTRY_DIR.glob("*.yaml"):
            path.unlink()
        power.reset_probe_memo()
        self.addCleanup(power.reset_probe_memo)
        power.SUSPEND_STATE.update({"last_wall": None, "last_mono": None})
        self.addCleanup(power.SUSPEND_STATE.update,
                        {"last_wall": None, "last_mono": None})
        self.ctx = _Ctx()

    def test_the_threshold_is_closed_at_the_bottom(self):
        # 「超过 SLEEP_GAP_SECONDS 才算睡过一觉」：正好到点那一秒算数（下面
        # 一秒不算）。差一秒就漏账 = 卡面上的耗时又开始数睡觉的时间（#311）。
        _card("R-9101")
        self.assertEqual(power.credit_sleep(self.ctx, power.SLEEP_GAP_SECONDS - 1), 0)
        self.assertEqual(self.ctx.saved, [])
        self.assertEqual(power.credit_sleep(self.ctx, power.SLEEP_GAP_SECONDS), 1)
        self.assertEqual(self.ctx.saved[0].execution["slept_seconds"],
                         int(power.SLEEP_GAP_SECONDS))

    def test_a_skipped_card_never_stops_the_scan(self):
        # 台账是全量扫的：前面一张 approved 卡、一张没有会话的 executing 卡，
        # 都只是「这张不落账」，不是「到此为止」——否则后面在跑的卡永远漏账，
        # 而且漏得跟卡 id 的排序有关（最难查的那种账）。
        _card("R-9201", status=State.APPROVED.value, execution={})
        _card("R-9202", execution={})
        _card("R-9203")
        self.assertEqual(power.credit_sleep(self.ctx, 600.0), 1)
        self.assertEqual([r.id for r in self.ctx.saved], ["R-9203"])

    def test_a_negative_stored_value_restarts_from_zero(self):
        # 累加的起点是 0：手工改坏 / 早期版本写进去的负数不许让耗时倒着走。
        _card("R-9301", execution={"session_id": "sid-1", "slept_seconds": -5})
        self.assertEqual(power.credit_sleep(self.ctx, 600.0), 1)
        self.assertEqual(self.ctx.saved[0].execution["slept_seconds"], 600)

    def test_a_suspension_exactly_at_the_threshold_voids_the_power_memo(self):
        # 同一个门槛管两件事（落账 + 作废判决缓存），两边必须在同一秒翻页：
        # 否则闸会拿睡前的 awake 往一台刚醒的机器上派卡（§71.1 第二道护栏）。
        power.current_verdict(probe=lambda: FULL_AWAKE, now=100.0, wall=1000.0)
        self.assertEqual(power.observed_verdict(now=100.0, wall=1000.0), power.AWAKE)
        power.sample_suspension(wall=1000.0, mono=10.0)
        power.sample_pass(self.ctx, wall=1000.0 + 10 + power.SLEEP_GAP_SECONDS,
                          mono=20.0)
        self.assertIsNone(power.observed_verdict(now=100.0, wall=1000.0))


if __name__ == "__main__":
    unittest.main()

"""§80.1 早醒：inbox 里排着 owner 动作时，主循环不睡满一个 pass 间隔。

病灶（issue #450，owner 原话「前端来看每一步都有很多等待」）：主循环历来结在
`time.sleep(interval)`（出厂 10s），owner 的每一个动作都要等下一个 pass 才被
drain——平均白等 interval/2、最坏 interval，而且这笔过路费挂在每一个动词上。

判例（`act.lib.actd.wakeup`，四个 seam 全部可注入，绝不睡真觉）：
  - 空闲（inbox 没东西）-> 睡满 interval，返回 False；
  - 基线之外冒出新文件 -> 立刻返回 True；
  - 一进来就有活（pass 中途 drain 之后才落地的那一笔）-> 不睡一秒就 True；
  - **删不掉的毒文件在基线里 -> 不早醒**（否则 250ms 空转一整天）；
  - 毒文件旁边来了新动作 -> 照样早醒；
  - 睡眠永不超过 interval（轮询粒度夹到剩余时间）；
  - `inbox_names` 目录不存在 / 读不动 = 空集，只认 *.json（探测失败绝不崩
    主循环，宪法第 11 条——最坏退化成早醒之前的老行为）。

主循环接线：`actd._loop_forever` 的基线取在 run_once **之前**，且 heartbeat
报的仍是**配置**间隔（staleness 门槛的真源；早醒只让 beat 更新鲜）。

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act.lib import config
from act.lib.actd import wakeup


class _Clock:
    """单调时钟的假体：sleeper 推进它，所以「睡了多久」可精确断言。"""

    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def _clear_inbox():
    for p in config.INBOX_DIR.glob("*"):
        p.unlink(missing_ok=True)


class InboxNamesTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        _clear_inbox()
        # 收尾必须清干净：`state/inbox/` 是 suite 共用的沙箱目录，留下的文件会
        # 让后面那条 `test_missing_inbox_dir_returns_zero` 的 rmdir 报
        # 「Directory not empty」（自测时真撞上了）
        self.addCleanup(_clear_inbox)

    def test_empty_inbox_is_the_empty_set(self):
        self.assertEqual(wakeup.inbox_names(), frozenset())

    def test_only_json_decision_files_count(self):
        (config.INBOX_DIR / "a.json").write_text("{}", encoding="utf-8")
        (config.INBOX_DIR / "notes.txt").write_text("x", encoding="utf-8")
        self.assertEqual(wakeup.inbox_names(), frozenset({"a.json"}))

    def test_missing_directory_degrades_to_empty_not_a_crash(self):
        # 探测失败绝不崩主循环——最坏结果只是这一轮睡满（宪法第 11 条）
        self.assertEqual(
            wakeup.inbox_names(config.INBOX_DIR / "definitely-not-here"),
            frozenset())


class WaitForWorkTestCase(unittest.TestCase):
    def _wait(self, interval, baseline, seen, clock=None, poll_s=0.25):
        """seen：每次探测返回的名字集合；传 list 逐次取用，传单值则恒定返回。"""
        clk = clock or _Clock()
        names = mock.Mock(side_effect=seen) if isinstance(seen, list) \
            else mock.Mock(return_value=seen)
        woke = wakeup.wait_for_work(
            interval, baseline, names=names,
            sleeper=clk.sleep, clock=clk, poll_s=poll_s)
        return woke, clk

    def test_idle_sleeps_the_whole_interval(self):
        woke, clk = self._wait(10, frozenset(), frozenset())
        self.assertFalse(woke)
        self.assertAlmostEqual(sum(clk.slept), 10.0, places=6)

    def test_a_new_file_wakes_the_loop_early(self):
        # 四次探测空手，第五次看到 owner 的动作 -> 1s 就醒（而不是 10s）
        seen = [frozenset()] * 4 + [frozenset({"new.json"})]
        woke, clk = self._wait(10, frozenset(), seen)
        self.assertTrue(woke)
        self.assertAlmostEqual(sum(clk.slept), 1.0, places=6)

    def test_work_already_queued_at_entry_never_sleeps(self):
        # pass 中途（drain 之后）才落地的那一笔：一秒都不该再等
        woke, clk = self._wait(10, frozenset(), frozenset({"mid.json"}))
        self.assertTrue(woke)
        self.assertEqual(clk.slept, [])

    def test_an_undeletable_poison_file_does_not_spin_the_loop(self):
        # safe_unlink 失败留下的文件在基线里：认它就是 250ms 空转一整天
        stuck = frozenset({"poison.json"})
        woke, clk = self._wait(10, stuck, stuck)
        self.assertFalse(woke)
        self.assertAlmostEqual(sum(clk.slept), 10.0, places=6)

    def test_a_new_action_beside_a_poison_file_still_wakes(self):
        woke, clk = self._wait(10, frozenset({"poison.json"}),
                               frozenset({"poison.json", "click.json"}))
        self.assertTrue(woke)
        self.assertEqual(clk.slept, [])

    def test_sleep_never_overshoots_the_interval(self):
        # 轮询粒度夹到剩余时间：interval=0.4 / poll=0.25 -> 0.25 + 0.15
        woke, clk = self._wait(0.4, frozenset(), frozenset(), poll_s=0.25)
        self.assertFalse(woke)
        self.assertAlmostEqual(sum(clk.slept), 0.4, places=6)
        self.assertTrue(all(s <= 0.25 + 1e-9 for s in clk.slept))

    def test_zero_interval_keeps_the_old_no_sleep_semantics(self):
        woke, clk = self._wait(0, frozenset(), frozenset())
        self.assertFalse(woke)
        self.assertEqual(clk.slept, [])

    def test_baseline_none_means_any_file_is_new(self):
        woke, _ = self._wait(10, None, frozenset({"a.json"}))
        self.assertTrue(woke)


class LoopWiringTestCase(unittest.TestCase):
    """主循环真的走早醒，而且基线取在 pass 之前。"""

    def test_loop_waits_on_wakeup_and_baselines_before_the_pass(self):
        from act import actd

        order = []

        def fake_names():
            order.append("baseline")
            return frozenset()

        def fake_run_once(*a, **kw):
            order.append("pass")
            return {}

        def fake_wait(interval, baseline, **kw):
            order.append(f"wait:{interval}")
            raise KeyboardInterrupt      # 跑完一圈就退出

        with mock.patch.object(actd._wakeup, "inbox_names", fake_names), \
             mock.patch.object(actd._wakeup, "wait_for_work", fake_wait), \
             mock.patch.object(actd, "run_once", fake_run_once), \
             self.assertRaises(KeyboardInterrupt):
            actd._loop_forever(config.Config(), 10, set(), set(), set())

        # 基线在 pass 之前、等待在 pass 之后，且**等待只有这一处**：
        # wait_for_work 不返回 = 下一个 pass 不会开始
        self.assertEqual(order, ["baseline", "pass", "wait:10"])

    def test_the_entry_layer_no_longer_owns_a_sleep_to_park_in(self):
        """2026-08-31 的静默卡死就是「进程活着、停在 `time.sleep`」；§80.1 起
        那句无条件 sleep 整个从入口层消失，等待归 `wakeup` 所有。

        这是一条**绊线**，不是禁令：真需要在 `act/actd.py` 里 import time 时，
        改掉这条判例是合法的——但请顺手确认 `_loop_forever` 里没有第二个
        sleep（判例 `test_loop_waits_on_wakeup_and_baselines_before_the_pass`
        只钉「等待走 wakeup」，钉不住旁边多出来的一句）。此前 patch
        `actd.time` 能影响 reconcile 的节流时钟，只是因为它与 `reconcile.time`
        是同一个模块对象——那些判例已改为 patch 真正的主人。
        """
        from act import actd
        self.assertFalse(hasattr(actd, "time"))

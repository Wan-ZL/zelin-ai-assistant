"""§80.2 停止的等死窗口按 pid 轮询，进程一死就返回（不再无条件 sleep(2)）。

病灶（issue #450，owner 原话「点击停止后也是需要等待很久才能从 running 进入
review」）：`executor.stop_session` 发出 `claude stop` 之后是一句无条件
`time.sleep(2)`，而 claude 通常 100–300ms 就没了。那 2s 白等还有二次伤害——
它把 `stop_session_confirmed` 紧接着那次 roster 探测推迟到窗口末尾，赶不上就
要再赔一整轮重试（2s 退避 + 一次 30s-timeout 的 roster 探测）。

判例（seam 全部可注入，绝不看真进程、绝不睡真觉）：
  - 进程已经没了 -> 一秒不睡就确认；
  - 第 3 次轮询才死 -> 只睡 3 个粒度，不睡满窗口；
  - 一直不死 -> 窗口用满才返回 False（总长仍是 §46.1 承诺的 2s，没放宽）；
  - 窗口永不超时长（粒度夹到剩余时间）；
  - `_pid_alive` 的三态：ESRCH = 死；EPERM / 坏 pid / 说不清的 OSError
    **一律算活着**——「没确认死」不许当已死（§46.1 探测失败 ≠ 已停同一条
    精神），最坏退化成睡满 2s 的老行为；
  - `stop_session` 拿 roster 上的 pid 去等，而且 `issued` 语义不变（返回 True
    只代表 stop 发出去了，确认死活是 stop_session_confirmed 的活）。

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import executor

SID = "abcd1234-0000-4000-8000-000000000001"


class _Clock:
    def __init__(self):
        self.t = 500.0
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


class AwaitExitTestCase(unittest.TestCase):
    def _await(self, alive_seq, grace_s=2.0, poll_s=0.1):
        clk = _Clock()
        alive = mock.Mock(side_effect=alive_seq) if isinstance(alive_seq, list) \
            else mock.Mock(return_value=alive_seq)
        gone = executor._await_exit(
            4242, grace_s=grace_s, poll_s=poll_s,
            alive=alive, sleeper=clk.sleep, clock=clk)
        return gone, clk

    def test_a_process_already_gone_costs_no_sleep_at_all(self):
        gone, clk = self._await(False)
        self.assertTrue(gone)
        self.assertEqual(clk.slept, [])

    def test_returns_as_soon_as_the_pid_disappears(self):
        # 活活活死：只睡 3 × 100ms，而不是白等 2s
        gone, clk = self._await([True, True, True, False])
        self.assertTrue(gone)
        self.assertAlmostEqual(sum(clk.slept), 0.3, places=6)

    def test_a_process_that_never_dies_uses_the_whole_window(self):
        # 总长不变：§46.1 承诺的 2s 等死窗口没有被放宽
        gone, clk = self._await(True)
        self.assertFalse(gone)
        self.assertAlmostEqual(sum(clk.slept), 2.0, places=6)

    def test_the_window_never_overshoots(self):
        gone, clk = self._await(True, grace_s=0.25, poll_s=0.1)
        self.assertFalse(gone)
        self.assertAlmostEqual(sum(clk.slept), 0.25, places=6)
        self.assertTrue(all(s <= 0.1 + 1e-9 for s in clk.slept))

    def test_a_zero_grace_window_probes_once_and_returns(self):
        gone, clk = self._await(True, grace_s=0)
        self.assertFalse(gone)
        self.assertEqual(clk.slept, [])


class PidAliveTestCase(unittest.TestCase):
    def test_esrch_is_the_only_confirmed_death(self):
        with mock.patch.object(executor.os, "kill",
                               side_effect=ProcessLookupError):
            self.assertFalse(executor._pid_alive(4242))

    def test_a_live_pid_reports_alive(self):
        with mock.patch.object(executor.os, "kill", return_value=None):
            self.assertTrue(executor._pid_alive(4242))

    def test_eperm_counts_as_alive_not_as_dead(self):
        # 别的用户的同号 pid：没确认死就不许当已死（§46.1 精神）
        with mock.patch.object(executor.os, "kill",
                               side_effect=PermissionError):
            self.assertTrue(executor._pid_alive(4242))

    def test_an_unreadable_pid_counts_as_alive(self):
        self.assertTrue(executor._pid_alive("not-a-pid"))
        self.assertTrue(executor._pid_alive(None))


class StopSessionWiringTestCase(unittest.TestCase):
    def test_stop_session_waits_on_the_roster_pid_instead_of_sleeping_blind(self):
        awaited = []
        with mock.patch.object(executor, "_claude_bin", return_value="/bin/true"), \
             mock.patch.object(executor.subprocess, "run") as run, \
             mock.patch.object(executor, "_await_exit",
                               side_effect=lambda pid: awaited.append(pid) or True), \
             mock.patch.object(executor.time, "sleep") as slept:
            self.assertTrue(executor.stop_session(SID, info={"pid": 77}))
        self.assertEqual(awaited, [77])          # 等的是 roster 上那个 pid
        slept.assert_not_called()                # 无条件 sleep(2) 必须不在了
        self.assertEqual(run.call_args[0][0][1:], ["stop", "abcd1234"])

    def test_no_live_pid_still_short_circuits_without_stopping(self):
        with mock.patch.object(executor.subprocess, "run") as run, \
             mock.patch.object(executor, "_await_exit") as await_exit:
            self.assertFalse(executor.stop_session(SID, info={}))
        run.assert_not_called()
        await_exit.assert_not_called()

    def test_issued_semantics_survive_a_process_that_refuses_to_die(self):
        # 返回 True 只代表 stop 发出去了——确认死活是 stop_session_confirmed 的
        # 活（§46.1）。等死窗口的结论刻意不折进返回值。
        with mock.patch.object(executor, "_claude_bin", return_value="/bin/true"), \
             mock.patch.object(executor.subprocess, "run"), \
             mock.patch.object(executor, "_await_exit", return_value=False):
            self.assertTrue(executor.stop_session(SID, info={"pid": 77}))

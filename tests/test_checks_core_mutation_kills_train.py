"""doctor 共享件的两条合同：`launchctl list` 的列数闸门与超时阶梯（CONTRACT §25）。

tests/test_doctor_default_probes.py 钉住了这一层的默认探针实现，tests/
test_doctor.py 钉住了各家族的行——但两边喂的 `launchctl list` 文本永远是三列
齐全的，超时值也从来没人问过。夜间变异（§57）因此在
`launchctl_table` 的 `len(parts) >= 3` 和两个超时常量上留下存活体。

* **三列才是一条记录**：`launchctl list` 的一行是 PID / Status / Label。
  少一列就按下标取 `parts[2]` = IndexError——而这个循环的 `except` 在**整个
  循环外面**，一次越界会把剩下所有 agent 的记录一起吞掉，doctor 于是把后面
  每一个 agent 都印成「没注册」并让 owner 去重装（§55）。闸门在这里是为了让
  扫描**扫完**，不是为了跳过一行。
* **超时阶梯是 owner 要等的秒数**：doctor 是一条人敲的命令，`--fast` 的总时长
  就是这些上限之和。`crontab -l` 在 cron 守护卡住时会挂住，它只值 10 秒；
  一次真模型调用（`claude -p`）冷启动 + 慢网要几十秒，90 秒是 `claude auth`
  那一行的上限，也是 `core.run` 的**唯一**缺省——每个不显式传 timeout 的探针
  都继承它。改这两个数字是一个自觉的决定，不是重构的副作用。
"""
from __future__ import annotations

import inspect
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before any act import

from act.lib.checks import core

ACTD = core.ACTD_LABEL
SYNCD = core.SYNCD_LABEL


def _probes(listing: str):
    return mock.Mock(launchctl_list=lambda: listing)


class LaunchctlTableColumnsTestCase(unittest.TestCase):
    """列数闸门：三列成行，残行跳过且**不许打断后面的扫描**。"""

    def test_a_clipped_line_does_not_swallow_the_rest_of_the_listing(self):
        # 残行在中间：它之后的 agent 必须照样被记下来。越界的 IndexError 会被
        # 循环外的 except 吞掉，表就在残行处静默截断——后面每个 agent 都变成
        # 「没注册」，而机器上它们跑得好好的。
        text = ("PID\tStatus\tLabel\n"
                "-\t0\t%s\n"
                "-\t0\n"                      # 两列：被截断的一行
                "4242\t0\t%s\n" % (ACTD, SYNCD))
        table = core.launchctl_table(_probes(text))
        self.assertEqual(table[ACTD], ("-", "0"))
        self.assertEqual(table[SYNCD], ("4242", "0"))

    def test_the_three_columns_land_in_the_documented_order(self):
        # label → (pid, 上次退出码)：§55 的 crash-loop 判据读的就是这两个位置，
        # 顺序错了「已加载、无 pid、上次非 0」会认成别的东西。
        table = core.launchctl_table(_probes("7\t3\t%s\n" % ACTD))
        self.assertEqual(table[ACTD], ("7", "3"))

    def test_a_seam_that_raises_yields_an_empty_table_not_a_crash(self):
        def boom():
            raise OSError("launchctl: not found")
        self.assertEqual(core.launchctl_table(mock.Mock(launchctl_list=boom)), {})


class ProbeBudgetTestCase(unittest.TestCase):
    """超时阶梯（doctor 是人敲的命令，每一档都是 owner 要等的秒数）。"""

    def test_the_live_call_ceiling_is_also_the_shared_runners_default(self):
        self.assertEqual(core.PROBE_TIMEOUT, 90)
        default = inspect.signature(core.run).parameters["timeout"].default
        self.assertIs(default, core.PROBE_TIMEOUT)

    def test_the_crontab_probe_gets_ten_seconds_not_the_live_call_ceiling(self):
        # `crontab -l` 是一次本地读；cron 守护卡住时它会挂住。10 秒是它在
        # `--fast` 总预算里的份额，远低于活探针那一档。
        calls = []

        def fake_run(cmd, env=None, timeout=None):
            calls.append((cmd, timeout))
            return 0, "* * * * * x\n"

        with mock.patch.object(core, "run", fake_run):
            core.crontab()
        self.assertEqual(calls, [(["crontab", "-l"], 10)])
        self.assertLess(calls[0][1], core.PROBE_TIMEOUT)


if __name__ == "__main__":
    unittest.main()

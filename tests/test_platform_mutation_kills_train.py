"""act/lib/platform.py 电源探针族的变异存活体判例（CONTRACT §71.1；模块另管 §5 / §25 / §28）。

背景：dev train 的 3e783390（issue #311，§71.1「派发前先问机器醒着吗」）给 OS seam 加了三个电源探针
（`_probe_text` / `power_state` / `power_capabilities` / `power_assertions`）。夜报在这些新行上留了
一片存活变异体——大半不是没判例，而是**判例没进靶区**：tests/test_power_probe.py 钉着同一段解析，
却不在 qa/mutation_targets.toml 的 act/lib/platform.py 那一行里（本 PR 一并补上映射）。本文件钉住
把那份判例算进来**仍然活着**的几族，一律按「§71.1 的判据依赖什么」而不是按源码形状写：

* `_probe_text` 的 5 s 探针预算与两条 argv：arm64 上实测活着的只有 `pmset -g powerstate
  IOPMrootDomain` / `ioreg -n IOPMrootDomain -r -d 1` / `pmset -g assertions`（老判据
  IODisplayWrangler / AppleClamshellState 的 tombstone 见模块注释），派发 pass 每 10 s 一轮，
  三个探针的预算不许把一轮拖没。
* `runner is None and not is_darwin()` 的 **and**：非 darwin 上注入了 runner 就照跑（linux CI 要走
  同一段解析）——darwin 开发机上两个操作数同时为假，and/or 在那里无从区分。
* `(runner or _run)` 的 **or**：darwin 开发机上 `_run` 真能跑出 pmset 输出，「解析对了」因此不是
  证据；判据改成「注入的 runner 必须真的被调用」，与本机有没有 pmset 无关。
* `power_state` 的 `(m.group(1), m.group(2))` 下标：真机 fixture 是 `4 4 ON`，两档相等 → 换下标不
  可见；dark wake / 正在睡下去的行两档必须不同，且顺序 = (当前档, 最高档)，反过来就把「正在睡
  下去」读成满醒，§71.1 的主判据当场失效。
* `if head < 0` 的 **<**：真机 fixture 的 system-wide 头前面有一行时间戳（head > 0），头正好坐在 0
  的输出（pmset 没打那行时）会被 `<=` / `< 1` 整块吞掉，assertions 读数恒空。
* 三处 fail-open 出口（`return ""`）：探不到 / 探针炸了 = 「探不到」，不是 None 文本——`None` 会让
  上层 `re.search` 抛 TypeError，把「闸探不到」变成「闸炸了」。

等价变异体（不 force-kill，理由记在这里，夜报再报不用再判）：
* `head < 0 → head < -1`（`power_assertions`）：`str.find` 只返回 -1 或 ≥ 0，唯一有分歧的
  head == -1 恰是「找不到头」；那时变异体走下去拿到的 block 是 `text[-1:]`（≤ 1 字符），而行正则
  要 `\\s+名字\\s+数字` 至少 4 字符，恒无匹配 → 照样返回 `{}`。
* `tail > head → tail >= head`（同一函数）：`tail = text.find(TAIL, head)` 要么 -1（两边都为假 →
  `text[head:]`），要么 ≥ head；等于 head 不可能——head 处坐着的是 `"Assertion status system-wide:"`，
  首字符就与 `"Listed by owning process:"` 不同。
"""
import subprocess
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before any act import

from act.lib import platform, power

# 「正在睡下去 / dark wake」的电源档行：当前档 < 最高档（真机 fixture 是 4 4 ON，两档相等）。
_POWERSTATE_SLEEPING = (
    "\n"
    "      Driver ID  Current State  Max State  Current State Description\n"
    "IOPMrootDomain              2          4  ON\n"
)
_POWERSTATE_HEADER_ONLY = (
    "      Driver ID  Current State  Max State  Current State Description\n"
)

_IOREG_FULL_AWAKE = (
    '      "System Capabilities" = 15\n'
    '      "IOPMUserIsActive" = Yes\n'
)
_IOREG_DARK_WAKE = (
    '      "System Capabilities" = 9\n'
    '      "IOPMUserIsActive" = No\n'
)

# system-wide 头就坐在 index 0；TAIL 之后那一行是诱饵——形状和系统级计数行一模一样，但它属于
# 逐进程明细（进程名与用户文案），永不进读数。
_ASSERTIONS_HEAD_AT_ZERO = (
    "Assertion status system-wide:\n"
    "   UserIsActive                   0\n"
    "   PreventUserIdleDisplaySleep    1\n"
    "Listed by owning process:\n"
    "   SneakyHold                     9\n"
)


class _RecordingRunner:
    """注入的假 subprocess runner：记账 (argv, timeout)，回放固定文本 / 抛固定异常。"""

    def __init__(self, text: str = "", raises: Exception = None):
        self.calls: list = []
        self.text = text
        self.raises = raises

    def __call__(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(args=argv, returncode=0,
                                           stdout=self.text, stderr="")


class ProbeCommandAndBudgetTestCase(unittest.TestCase):
    def test_each_probe_runs_its_verified_command_with_a_five_second_budget(self):
        """三条 argv 是 arm64 上实测还活着的那三条，每条 5 s——注入的 runner 必须真的接到它们。

        预算是 §71.1 的隐含契约：派发 pass 每 10 s 一轮，一轮里三个探针连着起，预算一放大就把
        整条 pass 拖过一轮；缩小则在慢机器上把读数变成恒「探不到」（闸静默 no-op）。
        """
        runner = _RecordingRunner()
        platform.power_state(runner=runner)
        platform.power_capabilities(runner=runner)
        platform.power_assertions(runner=runner)
        self.assertEqual([argv for argv, _ in runner.calls], [
            ["pmset", "-g", "powerstate", "IOPMrootDomain"],
            ["ioreg", "-n", "IOPMrootDomain", "-r", "-d", "1"],
            ["pmset", "-g", "assertions"],
        ])
        self.assertEqual([timeout for _, timeout in runner.calls], [5, 5, 5])


class OffDarwinProbeTestCase(unittest.TestCase):
    """非 darwin 上的两个出口：注入了 runner 照跑，没注入就一个子进程都不起。"""

    def test_an_injected_runner_still_parses_off_darwin(self):
        # linux CI 也要走同一段解析（判例注入真机 fixture 文本）——「不起子进程」的条件是
        # **没有注入 runner**，不是「不在 macOS 上」。
        with mock.patch("sys.platform", "linux"):
            self.assertFalse(platform.is_darwin())
            runner = _RecordingRunner(_POWERSTATE_SLEEPING)
            self.assertEqual(platform.power_state(runner=runner), (2, 4))
            self.assertEqual(len(runner.calls), 1)

    def test_no_runner_off_darwin_is_a_silent_miss(self):
        # 三个读数都是「探不到」（None / {}），且 _run 一次都没被摸——探针在没有 pmset 的机器上
        # 不许起子进程，也不许把「探不到」表达成 None 文本（上层 re.search(None) 会抛）。
        with mock.patch("sys.platform", "linux"), \
                mock.patch.object(platform, "_run") as spawn:
            self.assertIsNone(platform.power_state())
            self.assertEqual(platform.power_capabilities(), {})
            self.assertEqual(platform.power_assertions(), {})
        spawn.assert_not_called()

    def test_a_broken_probe_reads_as_missing_not_as_a_crash(self):
        # §71.1 fail-open：命令缺席 / 超时 = 探不到（闸照常派发），异常绝不漏给调用者。
        boom = _RecordingRunner(raises=OSError("pmset: command not found"))
        self.assertIsNone(platform.power_state(runner=boom))
        self.assertEqual(platform.power_capabilities(runner=boom), {})
        self.assertEqual(platform.power_assertions(runner=boom), {})


class PowerStateRowTestCase(unittest.TestCase):
    def test_the_row_reads_as_current_state_then_ceiling(self):
        """`IOPMrootDomain 2 4 ON` → `(2, 4)`：顺序本身就是判据的一半。"""
        state = platform.power_state(runner=_RecordingRunner(_POWERSTATE_SLEEPING))
        self.assertEqual(state, (2, 4))
        current, ceiling = state
        self.assertLess(current, ceiling)
        # 两档一换，「正在睡下去」就被读成满醒，§71.1 的主判据当场反向
        self.assertEqual(power.verdict({"state": current, "max_state": ceiling}),
                         power.ASLEEP)

    def test_a_table_without_the_row_is_a_miss(self):
        # 格式漂移 = 「探不到」，不是「睡着了」（表头在、数据行没了）
        runner = _RecordingRunner(_POWERSTATE_HEADER_ONLY)
        self.assertIsNone(platform.power_state(runner=runner))


class CapabilitiesTestCase(unittest.TestCase):
    def test_capabilities_is_the_bitmap_and_user_active_is_the_yes_no_flag(self):
        """满醒（15 / Yes）与 dark wake（9 = 缺 Graphics 位 / No）两种真形状逐键读出。"""
        cases = ((_IOREG_FULL_AWAKE, {"capabilities": 15, "user_active": True}),
                 (_IOREG_DARK_WAKE, {"capabilities": 9, "user_active": False}))
        for text, expected in cases:
            with self.subTest(capabilities=expected["capabilities"]):
                caps = platform.power_capabilities(runner=_RecordingRunner(text))
                self.assertEqual(caps, expected)
                self.assertIs(caps["user_active"], expected["user_active"])

    def test_unparsable_output_drops_the_keys_whole(self):
        # 探不到的键**整键不出**（键在、值瞎猜 = 让 verdict 拿假读数下判决）
        runner = _RecordingRunner("ioreg: not found\n")
        self.assertEqual(platform.power_capabilities(runner=runner), {})


class AssertionsBlockTestCase(unittest.TestCase):
    def test_a_system_wide_block_starting_at_index_zero_is_still_read(self):
        """头正好坐在 0 的输出（pmset 没打那行时间戳）照样整块读出，逐进程明细照样不进。"""
        self.assertTrue(_ASSERTIONS_HEAD_AT_ZERO.startswith(platform._ASSERTION_HEAD))
        rows = platform.power_assertions(runner=_RecordingRunner(_ASSERTIONS_HEAD_AT_ZERO))
        self.assertEqual(rows, {"UserIsActive": 0, "PreventUserIdleDisplaySleep": 1})
        self.assertNotIn("SneakyHold", rows)   # "Listed by owning process:" 之后的行

    def test_output_without_the_header_is_a_miss_not_zero_assertions(self):
        # `{}` = 探不到；不是「系统级计数全是 0」（后者会被 held_awake 读成「没人摁着」）
        runner = _RecordingRunner("pmset: unrecognized option\n")
        self.assertEqual(platform.power_assertions(runner=runner), {})


if __name__ == "__main__":
    unittest.main()

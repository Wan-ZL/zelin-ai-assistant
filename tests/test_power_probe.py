"""§71.1 电源探针：真机输出 → awake / asleep / unknown 的判决（act/lib/power.py）。

判例钉的是**真实形状**，不是想象的形状：tests/fixtures/power/ 里四份文件是
2026-09-14 在 owner 同款机器（arm64 / macOS 26.5.2 / MacBook）上逐字节抓下来的
`pmset` / `ioreg` 输出，包括两条**在 Apple Silicon 上已经死掉**的老判据——
`pmset -g powerstate IODisplayWrangler` 打印 "Internal failure" 还 exit 0，
`ioreg -r -k AppleClamshellState -d 4` 零行。§71.1 因此改读 IOPMrootDomain。

覆盖：三个探针的解析（含非 darwin 不起子进程）、判决表（满醒 / dark wake /
探不到 / 有人摁着屏幕不许睡）、fail-open 语义、`require_awake` 旋钮。
"""
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before any act import

from act.lib import config, platform, power

FIXTURES = Path(__file__).parent / "fixtures" / "power"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _probe_on():
    """套件默认关着探针总闸（tests/__init__）——真机 fixture 的解析判例要它开着，
    但仍然只经注入的 runner 走（一个真子进程都不起）。"""
    return mock.patch.dict(os.environ, {power.PROBE_ENV: "1"})


def _runner_for(mapping: dict):
    """argv[0..1] → 固定输出的假 runner（真机 fixture 文本喂进同一段解析）。"""
    def run(argv, timeout):  # noqa: ARG001 - signature mirrors platform.Runner
        key = " ".join(argv[:4])
        return subprocess.CompletedProcess(argv, 0, stdout=mapping.get(key, ""), stderr="")
    return run


AWAKE_RUNNER = _runner_for({
    "pmset -g powerstate IOPMrootDomain": _fixture("pmset_powerstate_rootdomain_arm64.txt"),
    "ioreg -n IOPMrootDomain -r": _fixture("ioreg_rootdomain_arm64.txt"),
    "pmset -g assertions": _fixture("pmset_assertions_arm64.txt"),
})


class RealMachineFixturesTestCase(unittest.TestCase):
    """真机输出逐字解析（这台机器当时是醒着的：4 / 4 ON，capabilities 15）。"""

    def test_root_power_state_row_is_parsed(self):
        self.assertEqual(platform.power_state(runner=AWAKE_RUNNER), (4, 4))

    def test_capabilities_and_user_active_are_parsed(self):
        caps = platform.power_capabilities(runner=AWAKE_RUNNER)
        self.assertEqual(caps["capabilities"], 15)      # CPU|Graphics|Audio|Network
        self.assertTrue(caps["capabilities"] & power.GRAPHICS_BIT)
        self.assertIs(caps["user_active"], True)        # "IOPMUserIsActive" = Yes

    def test_assertions_reads_only_the_system_wide_block(self):
        rows = platform.power_assertions(runner=AWAKE_RUNNER)
        self.assertEqual(rows["PreventUserIdleDisplaySleep"], 1)
        self.assertEqual(rows["UserIsActive"], 0)
        self.assertIn("PreventUserIdleSystemSleep", rows)
        # 逐进程明细（进程名 / 用户文案 / "pid 110(powerd)"）绝不进读数
        self.assertNotIn("pid", rows)
        self.assertFalse([k for k in rows if "caffeinate" in k or "Amphetamine" in k])

    def test_awake_machine_reads_as_awake(self):
        with _probe_on():
            reading = power.read_power(runner=AWAKE_RUNNER)
        self.assertEqual(reading["state"], 4)
        self.assertEqual(power.verdict(reading), power.AWAKE)


class DeadAppleSiliconProbesTestCase(unittest.TestCase):
    """老判据的 tombstone（skeptic 2026-09-14 实测）：两条都恒等于「探不到」，
    §71.1 若还建在它们上面，闸在 owner 那台 MacBook 上永远不会响。"""

    def test_display_wrangler_powerstate_has_no_row(self):
        text = _fixture("pmset_powerstate_displaywrangler_arm64.txt")
        self.assertIn("Internal failure", text)
        self.assertNotIn("IODisplayWrangler ", text.replace("Driver ID", ""))

    def test_clamshell_ioreg_is_empty(self):
        self.assertEqual(_fixture("ioreg_clamshell_arm64.txt").strip(), "")


class VerdictTableTestCase(unittest.TestCase):
    """读数 → 判决（纯函数，垃圾进不去也不炸）。"""

    def test_full_power_is_awake(self):
        self.assertEqual(power.verdict({"state": 4, "max_state": 4}), power.AWAKE)

    def test_below_max_power_is_asleep(self):
        # dark wake / 正在睡下去：主判据说了算，哪怕 Amphetamine 还摁着屏幕
        reading = {"state": 1, "max_state": 4,
                   "assertions": {"PreventUserIdleDisplaySleep": 1}}
        self.assertEqual(power.verdict(reading), power.ASLEEP)

    def test_dark_wake_capabilities_without_graphics_is_asleep(self):
        # 主判据无答案时的兜底：System Capabilities 缺 Graphics 位 = 屏幕没亮
        self.assertEqual(power.verdict({"capabilities": 0x9}), power.ASLEEP)

    def test_display_asleep_but_someone_holds_it_awake_is_awake(self):
        # RISK 护栏：24h 醒着、只是显示器睡了的台式 Mac 绝不被闸饿死
        self.assertEqual(
            power.verdict({"capabilities": 0x9, "user_active": True}), power.AWAKE)
        self.assertEqual(
            power.verdict({"capabilities": 0x9,
                           "assertions": {"UserIsActive": 1}}), power.AWAKE)

    def test_no_answer_is_unknown(self):
        for reading in ({}, None, "garbage", {"state": "x", "max_state": None}):
            self.assertEqual(power.verdict(reading), power.UNKNOWN)


class ProbeDisciplineTestCase(unittest.TestCase):
    def test_no_subprocess_off_darwin_without_an_injected_runner(self):
        with mock.patch.object(platform, "is_darwin", return_value=False), \
                mock.patch.object(platform, "_run") as spawn:
            self.assertIsNone(platform.power_state())
            self.assertEqual(platform.power_capabilities(), {})
            self.assertEqual(platform.power_assertions(), {})
        spawn.assert_not_called()

    def test_probe_never_raises(self):
        def boom(argv, timeout):  # noqa: ARG001
            raise OSError("pmset: command not found")
        self.assertIsNone(platform.power_state(runner=boom))
        self.assertEqual(platform.power_capabilities(runner=boom), {})
        self.assertEqual(platform.power_assertions(runner=boom), {})
        self.assertEqual(power.verdict(power.read_power(runner=boom)), power.UNKNOWN)

    def test_env_kill_switch_short_circuits_the_read(self):
        # 套件默认 AIASSISTANT_POWER_PROBE=0（tests/__init__）——读数恒空
        self.assertFalse(power.probe_enabled())
        self.assertEqual(power.read_power(runner=AWAKE_RUNNER), {})

    def test_memo_answers_within_the_window_then_re_probes(self):
        power.reset_probe_memo()
        self.addCleanup(power.reset_probe_memo)
        calls = []

        def probe():
            calls.append(1)
            return {"state": 4, "max_state": 4}

        self.assertEqual(power.current_verdict(probe=probe, now=1000.0), power.AWAKE)
        self.assertEqual(power.current_verdict(probe=probe, now=1030.0), power.AWAKE)
        self.assertEqual(len(calls), 1)
        self.assertEqual(power.current_verdict(probe=probe, now=1000.0 + power.MEMO_SECONDS),
                         power.AWAKE)
        self.assertEqual(len(calls), 2)

    def test_observed_verdict_never_probes(self):
        power.reset_probe_memo()
        self.addCleanup(power.reset_probe_memo)
        self.assertIsNone(power.observed_verdict(now=5.0))
        power.current_verdict(probe=lambda: {"state": 0, "max_state": 4}, now=5.0)
        self.assertEqual(power.observed_verdict(now=5.0), power.ASLEEP)
        self.assertIsNone(power.observed_verdict(now=5.0 + power.MEMO_SECONDS))


class RequireAwakeKnobTestCase(unittest.TestCase):
    def test_default_is_on_and_config_can_turn_it_off(self):
        power.reset_probe_memo()
        self.addCleanup(power.reset_probe_memo)
        self.assertTrue(power.require_awake(config.Config()))
        off = config.Config(raw={"autodispatch": {"require_awake": False}})
        self.assertFalse(power.require_awake(off))
        # 旋钮关掉 = 连探针都不问
        self.assertFalse(power.machine_asleep(off, probe=_never_called))


def _never_called() -> dict:
    raise AssertionError("require_awake=false 时不许探测")


if __name__ == "__main__":
    unittest.main()

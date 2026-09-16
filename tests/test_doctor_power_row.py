"""§71.1 doctor 的 `power probe` 行：探不出电源状态这件事必须有人说得出来。

派发闸对 `unknown` 是 fail-open 的（宁可多派一张也不饿死自动派发）——所以一个
永远解析不出的探针会把整条 §71.1 变成**没人看得见的 no-op**（这正是本条法的
审查焦点；actd.log 里那一行只在判决**变化**时写，探针恒不可读 = 每次启动一行，
之后再无声息）。doctor 这一行就是那个「看得见」：任何时候敲一次
`python3 -m act.doctor --fast` 都能问出「这台机器上闸到底有没有在工作」。
"""
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before any act import

from act import doctor
from act.lib import platform, power

AWAKE_READING = {"state": 4, "max_state": 4, "capabilities": 15, "user_active": True}
DARK_WAKE_READING = {"state": 4, "max_state": 4, "capabilities": 0x1}


def _row(reading: dict):
    return doctor._check_power(doctor.Probes(power_reading=lambda: dict(reading)))


class PowerRowTestCase(unittest.TestCase):
    def test_readable_probe_is_an_ok_row_that_names_the_verdict(self):
        row = _row(AWAKE_READING)
        self.assertEqual(row.name, "power probe")
        self.assertEqual(row.status, doctor.OK)
        self.assertIn(power.AWAKE, row.detail)

    def test_asleep_is_ok_too(self):
        # 这一行报的是「闸读得出来」，不是「机器状态好」——睡着不是毛病
        row = _row(DARK_WAKE_READING)
        self.assertEqual(row.status, doctor.OK)
        self.assertIn(power.ASLEEP, row.detail)

    def test_unreadable_probe_warns_and_says_the_gate_is_a_no_op(self):
        row = _row({})
        self.assertEqual(row.status, doctor.WARN)
        self.assertIn("§71.1", row.detail + row.fix)
        self.assertIn("require_awake", row.fix)      # 出口写在修法里

    def test_the_row_rides_the_macos_check_list_only(self):
        # 探针是 macOS 的第四件事（§71.1）；linux / windows 上问它等于恒 WARN
        with mock.patch("sys.platform", "darwin"):
            self.assertIn("_check_power",
                          [f.__name__ for f in doctor._checks_for_platform()])
        with mock.patch("sys.platform", "linux"):
            self.assertNotIn("_check_power",
                             [f.__name__ for f in doctor._checks_for_platform()])

    def test_the_default_probe_is_the_real_one_and_stays_hermetic(self):
        # 缺省实现 = power.read_power（总闸 AIASSISTANT_POWER_PROBE=0 在套件里
        # 恒关，非 darwin 也不起子进程）——doctor 不许在判卷机上真跑 pmset
        self.assertIs(doctor.Probes().power_reading, power.read_power)
        with mock.patch.object(platform, "_run") as spawn:
            self.assertEqual(doctor.Probes().power_reading(), {})
        spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""§71.1 电源探针场景的共用件（B-12 / B-13）。

真机 fixture 文本经注入的 runner 喂给 ``act.lib.platform`` 的三个探针（不起任何
子进程），读数再喂给 ``power.verdict`` 与派发闸。executor 用一个只记账的替身，
绝不 spawn 真 claude。
"""
from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.qa.fixtures_b import _harness  # noqa: E402
from act import actd  # noqa: E402
from act.lib import config, power, registry  # noqa: E402
from act.lib.registry import Requirement, State  # noqa: E402

# 探针 argv → fixture 文件（真机 arm64 抓的原文；睡着那一份是本轮新增）
AWAKE = {("pmset", "-g", "powerstate", "IOPMrootDomain"): "power/pmset_powerstate_rootdomain_arm64.txt",
         ("ioreg", "-n", "IOPMrootDomain", "-r", "-d", "1"): "power/ioreg_rootdomain_arm64.txt",
         ("pmset", "-g", "assertions"): "power/pmset_assertions_arm64.txt"}
ASLEEP = {("pmset", "-g", "powerstate", "IOPMrootDomain"):
          "coverage_b/pmset_powerstate_rootdomain_asleep_arm64.txt",
          ("pmset", "-g", "assertions"): "power/pmset_assertions_arm64.txt"}


class FakeExecutor:
    """只记账的 executor 替身（DispatchError 是闸门代码 getattr 得到的形状）。"""

    class DispatchError(Exception):
        pass

    def __init__(self):
        self.calls = []

    def dispatch(self, req, cfg):
        self.calls.append(req.id)


def runner_for(table: dict):
    """注入的 subprocess runner：认得 argv 就回 fixture 文本，认不得回空（探不到）。"""
    def runner(argv, timeout):
        rel = table.get(tuple(argv))
        return _harness.proc(_harness.fixture_text(rel) if rel else "")
    return runner


def read(table: dict) -> dict:
    """走真解析路径读一次电源（``AIASSISTANT_POWER_PROBE=1`` 由场景的 env 打开）。"""
    return power.read_power(runner=runner_for(table))


def approved(rid: str) -> Requirement:
    req = Requirement(id=rid, title=f"{rid} 待派发的卡，标题够长", status=State.APPROVED.value)
    registry.save(req)
    return req


def dispatch_under(reading: dict) -> "tuple[int, FakeExecutor]":
    """在给定读数下跑一次 ``dispatch_approved``；返回（派发数, 假 executor）。"""
    ex = FakeExecutor()
    power.reset_probe_memo()
    with _harness.patched(actd, "executor", ex), \
            _harness.patched(power, "read_power", lambda runner=None: dict(reading)):
        n = actd.dispatch_approved(config.Config(raw={"autodispatch": {}}))
    power.reset_probe_memo()
    return n, ex

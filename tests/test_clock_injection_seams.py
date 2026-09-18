"""§9 追记（`restored_at`，D73）/ §0 第 11 条——盖时刻戳的写者必须留注入缝。

`registry.restore` 原先只读真时钟盖 `execution.restored_at`，而 §70.2 追记的两阶段
老化判例（`tests/test_review_stale_sweep`）用注入的 `NOW` 量这枚戳到窗口末尾的天数：
真日期一过，窗口就少算一天。判例是 2026-09-15 写的，而把真时钟平移着重跑修复前的它可以
量出引信只有三天：2026-09-14/15/16 绿，09-17 起永远红（09-13 及更早也红，方向相反）。
09-17 当天 08:36Z–18:37Z 之间观察到 6 个 run 同时红，其中三个是 dependabot PR（#405 与
另两个）；#405 红的那三道必过门（两个 ubuntu 测试 job + QA gates）就是它，
修复见 ad4f0b71。那次修复补了缝、改了调用点，却**没有留判例**：缝被谁删掉或改名，
仍然只会在某个未来的日期才红一次，而且红在一条看不出与它有关的老化判例上。

本判例把缝本身钉住，红得当场、红在名字说得清的地方：签名收 `now=`、它**真的**决定
戳的值、不给时默认仍是真时钟（ad4f0b71 明写「default unchanged = wall clock；no
caller changes」）、给带时区的时刻则归一到 UTC。

**只用 aware datetime**：naive 值经 `astimezone` 会按**本机**时区折算（PT 机器上
`2026-09-15 03:30` 盖成 `10:30Z`，UTC 机器上盖成 `03:30Z`），钉它等于把判例绑在跑
测试的那台机器上——正是本文件要防的那类漂移，所以 naive 的下场故意不立判例。

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import datetime as _dt
import inspect
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import config, registry
from act.lib.registry import Requirement, State

UTC = _dt.timezone.utc
# 与 tests/test_review_stale_sweep 同一个注入时刻——两边说的是同一件事。
NOW = _dt.datetime(2026, 9, 15, 3, 30, tzinfo=UTC)


class _Case(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()

    def trashed(self, rid="P-1"):
        """一张躺在回收站里、等着被捞回来的待验收卡。"""
        registry.save(Requirement(
            id=rid, title=f"draft {rid} with enough length", status=State.REVIEW.value,
            sources=[{"channel": "meeting", "date": "2026-08-16", "quote": "q"}],
            execution={"review_at": "2026-08-16T09:00:00Z"}))
        return registry.trash(registry.load(rid), "stale:review_stale")


class RestoreClockSeamTestCase(_Case):
    """`registry.restore` 的 `now=` 缝：存在、管事、默认不变、归一到 UTC。"""

    def test_the_seam_exists_and_takes_a_clock(self):
        """缝被删掉 / 改名 = 当场红，而不是等某个未来的日期。"""
        params = inspect.signature(registry.restore).parameters
        self.assertIn("now", params, "registry.restore 丢了 now= 注入缝（ad4f0b71 回归）")
        self.assertIs(params["now"].default, None, "now= 必须可选，默认仍是真时钟")

    def test_the_seam_decides_the_stamp(self):
        """给了时刻就用它——不是「读真时钟然后把参数扔掉」。"""
        self.trashed()
        restored = registry.restore(registry.load("P-1"), now=NOW)
        self.assertEqual(restored.execution["restored_at"], "2026-09-15T03:30:00Z")
        # 盖在盘上的那份也得是它（判例量的是 registry.load 回来的卡）。
        self.assertEqual(registry.load("P-1").execution["restored_at"],
                         "2026-09-15T03:30:00Z")

    def test_an_aware_clock_is_normalised_to_utc(self):
        """戳的格式是 `...Z`：带偏移的时刻先折算成 UTC，不是把本地墙上时间抄进去。"""
        self.trashed()
        pacific = _dt.timezone(_dt.timedelta(hours=-7))
        same_instant = NOW.astimezone(pacific)          # 2026-09-14 20:30 -07:00
        restored = registry.restore(registry.load("P-1"), now=same_instant)
        self.assertEqual(restored.execution["restored_at"], "2026-09-15T03:30:00Z")

    def test_without_the_seam_the_default_is_still_the_wall_clock(self):
        """ad4f0b71 的承诺：缝是**加**的，没传 now= 的既有调用方行为一个字没变。"""
        before = _dt.datetime.now(UTC)
        self.trashed()
        restored = registry.restore(registry.load("P-1"))
        after = _dt.datetime.now(UTC)

        stamp = _dt.datetime.strptime(
            restored.execution["restored_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        # 戳精度是秒，两端各让一秒，避免跨秒边界的假红。
        self.assertGreaterEqual(stamp, before - _dt.timedelta(seconds=1))
        self.assertLessEqual(stamp, after + _dt.timedelta(seconds=1))


if __name__ == "__main__":
    unittest.main()

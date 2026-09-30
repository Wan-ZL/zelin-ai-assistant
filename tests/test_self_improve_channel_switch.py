"""「自动改进本软件」通道的总开关（CONTRACT §65.1 / §70.3 / §70.4 / §15.3；issue
#307，owner 决策 D57）：`self_improve.enabled` **出厂 false**，一把开关关掉整条
「本软件自己改自己」的链路。

钉的行为：

* 三层配置（默认关 > config.yaml `self_improve.enabled` > `settings_overrides.json`
  的扁平键 `self_improve_enabled`）——设置页「开发者」区那一行写的就是最后一层；
* `config.example.yaml` **不钉这个键**（模板被逐字复制成 config.yaml，钉值 = 给每台
  新装机塞一个用户没做过的显式选择）；照模板生成的 config.yaml 读出来是关；
* 关着时每日循环的 `issues` / `prs` / `mutation` 三个读取器**一个都不跑**（零 gh
  调用，`inputs` 里各记 `"off"`），因此不铸 🤖 卡；维护半边与其余读取器照常；
* 关着时 §65.5 巡检直接 `{"skipped": "disabled"}`，连节流时钟都不推进、钩子不出声；
* 关着时 §51 第二条 lane 报 `self_improve:disabled`（常态回落，不上卡）；
* actd 每 pass 现读这把开关（`_refresh_model_knobs`），保存后下一 pass 生效；
* 设置页目录里这一行的落点 / 默认值（server 不 import act，键名镜像 §49）。

假 gh / 假 doctor / 沙箱 AIASSISTANT_HOME——零子进程、零网络。
"""
import datetime as _dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act import actd
from act.lib import config, daily_loop, loop_inputs, policy, registry, self_improve
from act.lib.registry import Requirement, State
from server import settings_catalog

REPO_EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.yaml"
TZ = _dt.timezone(_dt.timedelta(hours=-7))
NOW = _dt.datetime(2026, 9, 2, 3, 31, tzinfo=TZ)
UTC_NOW = _dt.datetime(2026, 9, 2, 10, 0, tzinfo=_dt.timezone.utc)
OWNER_ISSUE = [{"number": 5, "title": "make the loop quieter please", "body": "b",
                "author": {"login": "Wan-ZL"}, "url": "https://github.com/o/r/issues/5",
                "labels": []}]


class _RecordingGh:
    """每日循环注入的 gh runner：记下每一次调用（关着时必须一次都没有）。"""

    def __init__(self, issues=None):
        self.calls: list = []
        self.issues = issues or []

    def __call__(self, args):
        self.calls.append(list(args))
        if args[:2] == ["issue", "list"] and "--search" not in args:
            return json.dumps(self.issues)
        return None


def _doctor_none():
    return "[]"


def _cfg(enabled: bool) -> config.Config:
    return config.Config(self_improve_enabled=enabled)


def _load(yaml_body: str = "", overrides=None) -> config.Config:
    """真 load_config 的三层：config.yaml + settings_overrides.json。"""
    with tempfile.TemporaryDirectory(prefix="si-switch-") as tmp:
        cfg_path = Path(tmp) / "config.yaml"
        cfg_path.write_text(yaml_body, encoding="utf-8")
        ov_path = Path(tmp) / "settings_overrides.json"
        if overrides is not None:
            ov_path.write_text(json.dumps(overrides), encoding="utf-8")
        with mock.patch.object(config, "CONFIG_PATH", cfg_path), \
                mock.patch.object(config, "SETTINGS_OVERRIDES_PATH", ov_path):
            return config.load_config()


class ConfigLayeringTestCase(unittest.TestCase):
    def test_factory_default_is_off(self):
        self.assertFalse(config.Config().self_improve_enabled)
        self.assertFalse(policy.SELF_IMPROVE_DEFAULTS["enabled"])
        self.assertFalse(_load().self_improve_enabled)

    def test_yaml_block_turns_it_on_and_bad_values_stay_off(self):
        self.assertTrue(_load("self_improve:\n  enabled: true\n").self_improve_enabled)
        self.assertTrue(_load("self_improve:\n  enabled: 'yes'\n").self_improve_enabled)
        for body in ("self_improve:\n  enabled: maybe\n", "self_improve: junk\n",
                     "self_improve:\n  tick_minutes: 5\n", ""):
            self.assertFalse(_load(body).self_improve_enabled, body)

    def test_override_key_wins_over_yaml_in_both_directions(self):
        on = _load("self_improve:\n  enabled: false\n", {"self_improve_enabled": True})
        self.assertTrue(on.self_improve_enabled)
        off = _load("self_improve:\n  enabled: true\n", {"self_improve_enabled": "false"})
        self.assertFalse(off.self_improve_enabled)
        # 坏形状 = 整条 override 跳过，保留 yaml 层（§15「wrong types are ignored」）
        kept = _load("self_improve:\n  enabled: true\n", {"self_improve_enabled": 7})
        self.assertTrue(kept.self_improve_enabled)

    def test_the_shipped_template_does_not_pin_the_key(self):
        """模板被 install.sh / setup / App 逐字复制成 config.yaml——在里面钉死
        一个值 = 每台新装机都带着用户没做过的「显式选择」（2026-09-02 到 09-14
        的 `enabled: true` 就是这么上去的）。照着模板生成的 config.yaml 必须是
        **关**，且这一行只能以注释形态存在（§65.1 追记）。"""
        text = REPO_EXAMPLE.read_text(encoding="utf-8")
        self.assertFalse(_load(text).self_improve_enabled)
        block = text.split("\nself_improve:\n", 1)[1].split("\n\n", 1)[0]
        live = [ln for ln in block.splitlines()
                if ln.strip().startswith("enabled:")]
        self.assertEqual(live, [], "config.example.yaml 不许钉 self_improve.enabled")
        self.assertIn("# enabled:", block)    # 但要留着这行文档

    def test_the_block_s_other_keys_still_come_from_raw_yaml(self):
        cfg = _load("self_improve:\n  enabled: true\n  tick_minutes: 5\n  github_repo: o/r\n")
        si = policy.self_improve_config(cfg)
        self.assertEqual((si["enabled"], si["tick_minutes"], si["github_repo"]),
                         (True, 5, "o/r"))


class LaneAdmissionTestCase(unittest.TestCase):
    """§51 第二条 lane：默认态就是 `self_improve:disabled`（常态回落，不上卡）。"""

    def _card(self):
        return Requirement(id="P-7", title="lane", type="self-improvement", tier="T1",
                           status=State.CARD_SENT.value, target_repo=str(config.HOME),
                           target_kind="existing", delivery_mode="repo",
                           sources=[{"who": "loop", "channel": "self_improve",
                                     "date": "2026-09-02", "quote": "q"}])

    def test_default_config_blocks_the_lane_as_a_routine_reason(self):
        got = policy.may_auto_dispatch(self._card(), config.Config(), path_exists=lambda _p: True)
        self.assertEqual(got, (False, "self_improve:disabled"))
        self.assertTrue(policy.is_routine_reason("self_improve:disabled"))

    def test_switch_on_admits_the_same_card(self):
        got = policy.may_auto_dispatch(self._card(), _cfg(True), path_exists=lambda _p: True)
        self.assertEqual(got, (True, "ok:self_improve"))


class DailyLoopReadersTestCase(unittest.TestCase):
    """§70.3：关着 = 三个 GitHub 读取器不跑（零 gh 调用），开着 = 今天的行为。"""

    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        daily_loop.state_path().unlink(missing_ok=True)
        daily_loop.log_path().unlink(missing_ok=True)
        loop_inputs.materials_path().unlink(missing_ok=True)

    def _bot_cards(self):
        return [r for r in registry.load_all() if r.title.startswith(daily_loop.TITLE_PREFIX)]

    def test_collect_signals_off_marks_the_three_readers_and_calls_no_gh(self):
        gh = _RecordingGh(OWNER_ISSUE)
        out = daily_loop.collect_signals([], now=NOW, gh=gh, doctor=_doctor_none,
                                         repo="o/r", github=False)
        for name in daily_loop.GITHUB_READERS:
            self.assertEqual(out["inputs"][name], daily_loop.READER_OFF, name)
        self.assertEqual(gh.calls, [])
        self.assertEqual(out["gh_titles"], [])
        # 其余读取器照跑（计数是 int，不是 "off"）
        for name in ("registry", "analytics", "doctor", "materials"):
            self.assertIsInstance(out["inputs"][name], int, name)

    def test_collect_signals_on_still_reads_github(self):
        gh = _RecordingGh(OWNER_ISSUE)
        out = daily_loop.collect_signals([], now=NOW, gh=gh, doctor=_doctor_none,
                                         repo="o/r", github=True)
        self.assertEqual(out["inputs"]["issues"], 1)
        self.assertNotEqual(out["inputs"]["prs"], daily_loop.READER_OFF)
        self.assertTrue(gh.calls)

    def test_run_with_the_switch_off_files_no_bot_card(self):
        gh = _RecordingGh(OWNER_ISSUE)
        result = daily_loop.run(_cfg(False), now=NOW, gh=gh, doctor=_doctor_none)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["proposals"], 0)
        self.assertEqual(self._bot_cards(), [])
        self.assertEqual(gh.calls, [])
        row = json.loads(daily_loop.log_path().read_text(encoding="utf-8").splitlines()[-1])
        for name in daily_loop.GITHUB_READERS:
            self.assertEqual(row["inputs"][name], daily_loop.READER_OFF, name)

    def test_run_with_the_switch_on_files_the_issue_card(self):
        gh = _RecordingGh(OWNER_ISSUE)
        result = daily_loop.run(_cfg(True), now=NOW, gh=gh, doctor=_doctor_none)
        self.assertEqual(result["proposals"], 1)
        self.assertEqual([r.sources[0]["ref"] for r in self._bot_cards()],
                         ["self_improve:issue:5"])

    def test_plan_reads_the_same_switch(self):
        gh = _RecordingGh(OWNER_ISSUE)
        plan = daily_loop.plan(_cfg(False), now=NOW, gh=gh)
        self.assertEqual(plan["inputs"]["issues"], daily_loop.READER_OFF)
        self.assertEqual(gh.calls, [])

    def test_github_enabled_is_fail_closed_on_a_cfg_without_the_attr(self):
        self.assertFalse(daily_loop.github_enabled(object()))
        self.assertFalse(daily_loop.github_enabled(None))
        self.assertTrue(daily_loop.github_enabled(_cfg(True)))


class TickGateTestCase(unittest.TestCase):
    """§65.5：关着 = 不巡检、不碰节流时钟、钩子不出声。"""

    def setUp(self):
        config.ensure_state_dirs()
        self_improve.lane_state_path().unlink(missing_ok=True)

    def test_tick_off_skips_before_the_throttle_clock(self):
        calls = []

        def gh(args, cwd):
            calls.append(list(args))
            return (0, "")

        self.assertEqual(self_improve.tick(_cfg(False), gh=gh, now=UTC_NOW, force=True),
                         {"skipped": "disabled"})
        self.assertEqual(calls, [])
        self.assertFalse(self_improve.lane_state_path().exists())   # last_tick_at 都没写

    def test_tick_on_runs_the_round(self):
        summary = self_improve.tick(_cfg(True), gh=lambda args, cwd: (0, "[]"),
                                    now=UTC_NOW, force=True)
        self.assertNotIn("skipped", summary)
        self.assertEqual(self_improve.load_state()["last_tick_at"], "2026-09-02T10:00:00Z")

    def test_tick_hook_stays_quiet_while_the_channel_is_off(self):
        logs = []
        self_improve.tick_hook(_cfg(False), log=logs.append)
        self.assertEqual(logs, [])

    def test_board_view_mirrors_the_switch(self):
        self.assertFalse(self_improve.board_view(config.Config())["enabled"])
        self.assertTrue(self_improve.board_view(_cfg(True))["enabled"])


class ActdLiveRefreshTestCase(unittest.TestCase):
    def setUp(self):
        self.addCleanup(lambda: config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True))
        config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True)

    def _write(self, doc):
        config.SETTINGS_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_OVERRIDES_PATH.write_text(json.dumps(doc), encoding="utf-8")

    def test_refresh_picks_the_switch_up_without_a_restart(self):
        frozen = config.Config()
        self.assertFalse(frozen.self_improve_enabled)
        self._write({"self_improve_enabled": True})
        actd._refresh_model_knobs(frozen)
        self.assertTrue(frozen.self_improve_enabled)
        self._write({})                       # diff-write 删键 = 回到出厂默认
        actd._refresh_model_knobs(frozen)
        self.assertFalse(frozen.self_improve_enabled)


class SettingsCatalogRowTestCase(unittest.TestCase):
    """§15.3：设置页「开发者」区的第一行 = 这把开关（server 不 import act）。"""

    def _row(self):
        section = next(s for s in settings_catalog.SECTIONS if s["id"] == "maintainer")
        return section["fields"][0]

    def test_row_is_a_bool_defaulting_to_off_with_the_yaml_landing(self):
        row = self._row()
        self.assertEqual(row["key"], "self_improve_enabled")
        self.assertEqual(row["kind"], "bool")
        self.assertIs(row["default"], False)
        self.assertEqual(row["config"], ("self_improve", "enabled"))
        self.assertEqual(row["override"], "self_improve_enabled")
        self.assertTrue(row["label"]["zh"] and row["label"]["en"])
        self.assertTrue(row["help"]["zh"] and row["help"]["en"])

    def test_the_override_key_is_one_the_pipeline_reads(self):
        self.assertIn("self_improve_enabled", config._OVERRIDE_FIELDS)
        self.assertIs(config._OVERRIDE_FIELDS["self_improve_enabled"]("false"), False)


if __name__ == "__main__":
    unittest.main()

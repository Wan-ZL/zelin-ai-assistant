"""录制日程（issue #27）的两侧互镜与接线判例（CONTRACT §61.7；§61.1 add-only；§61.3 冻结不动）。

行为本身（窗口数学、边界执法、桥词表）由 `shell/tests/run.sh` 的 BridgeHarness [7]/[7b] 钉；
页面渲染由 vitest 钉。这里只钉「结构性的话」——改了一边忘另一边就红：

- 快照 `recording.schedule` 五键 + 方法 `setRecordingSchedule` 在 Swift 桥与 `web/src/shellBridge.ts` 两侧都有，
  且页面用 `schedulePaused` 判据（防腐 #10 逐字镜像）；
- 门长在 `shell/Sources/RecordingSchedule.swift` 的**外面**：`Recording.swift` 仍是 mac/ 冻结副本（§61.3 的
  逐字节判例在 test_shell_engine_mirror.py，这里只再钉一句「日程文件不在 VERBATIM 清单里、也没动引擎文件」）；
- main.swift 四个执法点接了线：启动窗外跳过 autostart、5 s tick 调 enforce、注册醒来观察者；
- 偏好键名冻结、默认关（always-on 现状不变）；
- 三个非录制态在 header 文案表里可区分（关 / 未在录制 / 按日程暂停），且暂停禁用「重启」；
- CONTRACT §61.7 存在且法条引用的判例文件都在。
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHELL = REPO_ROOT / "shell" / "Sources"
MAC = REPO_ROOT / "mac" / "Sources"
WEB = REPO_ROOT / "web" / "src"
HARNESS = REPO_ROOT / "shell" / "tests" / "BridgeHarness.swift"
CONTRACT = REPO_ROOT / "docs" / "CONTRACT.md"

SCHEDULE_KEYS = ["enabled", "start", "end", "days", "paused"]
PREF_KEYS = {
    "enabledKey": "recordingScheduleEnabled",
    "startKey": "recordingScheduleStart",
    "endKey": "recordingScheduleEnd",
    "daysKey": "recordingScheduleDays",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class WireMirrorTestCase(unittest.TestCase):
    def setUp(self):
        self.bridge = _read(SHELL / "ShellBridge.swift")
        self.schedule = _read(SHELL / "RecordingSchedule.swift")
        self.ts = _read(WEB / "shellBridge.ts")

    def test_schedule_block_keys_on_both_sides(self):
        # Swift：四键出自 RecordingScheduleSpec.wireValue，paused 由控制器 wireValue() 另加；桥挂在 recording["schedule"]
        self.assertIn('recording["schedule"] = schedule.wireValue()', self.bridge)
        for key in ["enabled", "start", "end", "days"]:
            self.assertIn('"%s": %s' % (key, key), self.schedule, "RecordingScheduleSpec.wireValue lacks %r" % key)
        self.assertIn('value["paused"] = paused', self.schedule)
        # web：接口逐字镜像 + normalize 永远填满
        iface = re.search(r"export interface ShellRecordingSchedule \{(.*?)\n\}", self.ts, re.DOTALL)
        self.assertIsNotNone(iface)
        for key in SCHEDULE_KEYS:
            self.assertIsNotNone(re.search(r"^\s+%s: " % key, iface.group(1), re.MULTILINE),
                                 "ShellRecordingSchedule lacks %r" % key)
        self.assertIn("schedule?: ShellRecordingSchedule;", self.ts)
        self.assertIn("schedule: normalizeSchedule(rec.schedule),", self.ts)

    def test_set_recording_schedule_method_on_both_sides(self):
        self.assertIn('case "setRecordingSchedule":', self.bridge)
        self.assertIn("try Self.applyRecordingSchedule(dict)", self.bridge)
        self.assertIn('| "setRecordingSchedule"', self.ts)

    def test_paused_hides_engine_diagnosis(self):
        # §61.7：故意停不是故障——paused ⇒ diagnosis null + log_tail ""
        block = self.bridge[self.bridge.index("if schedule.paused {"):self.bridge.index("} else {", self.bridge.index("if schedule.paused {"))]
        self.assertIn('recording["diagnosis"] = NSNull()', block)
        self.assertIn('recording["log_tail"] = ""', block)

    def test_web_has_single_paused_predicate_and_consumers_use_it(self):
        self.assertIn("export function schedulePaused(", self.ts)
        for rel in ("components/shell/RecordingControl.tsx", "components/settings/RecordingSection.tsx",
                    "pages/IngestPage.tsx", "components/permissions/RecordingConsentSection.tsx",
                    "components/setup/FinaleStep.tsx"):
            with self.subTest(file=rel):
                self.assertIn("schedulePaused(", _read(WEB / rel))

    def test_bridge_subscribes_to_schedule_changes(self):
        self.assertIn("RecordingSchedule.active.objectWillChange", self.bridge)


class FrozenEngineUntouchedTestCase(unittest.TestCase):
    def test_gate_lives_outside_the_frozen_engine_file(self):
        self.assertTrue((SHELL / "RecordingSchedule.swift").exists())
        self.assertFalse((MAC / "RecordingSchedule.swift").exists(), "the schedule is shell-only (D3: mac/ is frozen)")
        # 引擎副本仍逐字节 = 冻结参考（test_shell_engine_mirror 也钉；这里是本节的近身一句）
        self.assertEqual((SHELL / "Recording.swift").read_bytes(), (MAC / "Recording.swift").read_bytes())
        # 日程文件里没有任何 "schedule" 字样漏进引擎文件
        self.assertNotIn("RecordingSchedule", _read(SHELL / "Recording.swift"))

    def test_gate_only_borrows_public_engine_hooks_through_seams(self):
        schedule = _read(SHELL / "RecordingSchedule.swift")
        for seam in ("static var now:", "static var calendar:", "static var currentMode:", "static var engineRunning:",
                     "static var stopEngine:", "static var startEngine:"):
            self.assertIn(seam, schedule)
        self.assertIn("RecordingController.shared.applyMode()", schedule)
        self.assertIn("RecordingController.stopEngineBlocking()", schedule)
        # 冻结引擎里这两个 hook 确实是 internal（不是 private）——门借得到
        engine = _read(SHELL / "Recording.swift")
        self.assertIn("func applyMode(rollbackTo previous: String? = nil)", engine)
        self.assertIn("nonisolated static func stopEngineBlocking()", engine)


class MainWiringTestCase(unittest.TestCase):
    def setUp(self):
        self.main = _read(SHELL / "main.swift")

    def test_launch_skips_autostart_outside_the_window(self):
        start = self.main[self.main.index("private func startEngines()"):]
        enforce = start.index('schedule.enforce(reason: "launch")')
        gate = start.index("if schedule.allowsCaptureNow() {")
        autostart = start.index("RecordingController.shared.autostartIfNeeded()")
        self.assertLess(enforce, gate)
        self.assertLess(gate, autostart, "autostart must sit inside the schedule gate")
        self.assertIn("schedule.startObservingWake()", start)

    def test_tick_enforces_after_engine_refresh(self):
        tick = self.main[self.main.index("Timer(timeInterval: 5.0"):]
        refresh = tick.index("RecordingController.shared.refreshEngineState()")
        enforce = tick.index('RecordingSchedule.active.enforce(reason: "tick")')
        self.assertLess(refresh, enforce)

    def test_wake_observer_registered_in_the_gate(self):
        schedule = _read(SHELL / "RecordingSchedule.swift")
        self.assertIn("NSWorkspace.didWakeNotification", schedule)
        self.assertIn('enforce(reason: "wake")', schedule)


class PrefsAndDefaultsTestCase(unittest.TestCase):
    def test_pref_key_names_frozen_and_default_off(self):
        schedule = _read(SHELL / "RecordingSchedule.swift")
        for name, key in PREF_KEYS.items():
            self.assertIn('static let %s = "%s"' % (name, key), schedule)
        self.assertIn('RecordingScheduleSpec(enabled: false, start: "09:00", end: "19:00", days: [2, 3, 4, 5, 6])', schedule)
        ts = _read(WEB / "shellBridge.ts")
        self.assertIn('enabled: false, start: "09:00", end: "19:00", days: [2, 3, 4, 5, 6], paused: false,', ts)

    def test_legacy_prefs_do_not_seed_schedule_keys(self):
        # 原生 app 没有这组键；LegacyPrefs 清单不该长出它们（§61.7「LegacyPrefs 不搬」）
        bridge = _read(SHELL / "ShellBridge.swift")
        keys = re.search(r"static let keys = \[(.*?)\]", bridge[bridge.index("enum LegacyPrefs"):], re.DOTALL).group(1)
        self.assertNotIn("recordingSchedule", keys)


class StatusCopyTestCase(unittest.TestCase):
    def test_three_non_recording_states_are_distinct_in_the_header_table(self):
        control = _read(WEB / "components/shell/RecordingControl.tsx")
        body = re.search(r"export function recordingStateWord\(.*?\n\}", control, re.DOTALL).group(0)
        off = body.index('text("关", "Off")')
        sched = body.index("scheduledPauseWord(text)")
        dead = body.index('text("未在录制", "Not recording")')
        self.assertLess(off, sched)
        self.assertLess(sched, dead, "schedule pause must be decided before the generic 'not recording'")
        copy = _read(WEB / "components/shell/recordingSchedule.ts")
        self.assertIn('text("按日程暂停", "Paused by schedule")', copy)
        # 第三种颜色 + 重启禁用
        self.assertIn('paused ? "sched"', control)
        self.assertIn('disabled={state.mode === "off" || paused}', control)
        self.assertIn(".shell-rec-button.is-sched", _read(WEB / "styles/shell.css"))


class ContractTestCase(unittest.TestCase):
    def test_section_61_7_exists_and_points_at_real_files(self):
        contract = _read(CONTRACT)
        self.assertIn("### 61.7 录制日程", contract)
        section = contract[contract.index("### 61.7 录制日程"):contract.index("## 62.")]
        for rel in ("shell/Sources/RecordingSchedule.swift", "web/src/shellBridgeSchedule.test.ts",
                    "web/src/components/settings/RecordingSection.schedule.test.tsx",
                    "tests/test_shell_recording_schedule.py"):
            with self.subTest(file=rel):
                self.assertIn(rel.split("/")[-1], section)
                self.assertTrue((REPO_ROOT / rel).exists(), rel)
        for key in PREF_KEYS.values():
            self.assertIn(key, section)
        self.assertIn("setRecordingSchedule", section)
        # harness 钉了本节
        harness = _read(HARNESS)
        self.assertIn("func checkRecordingSchedule(", harness)
        self.assertIn("checkRecordingSchedule(bridge)", harness)


if __name__ == "__main__":
    unittest.main()

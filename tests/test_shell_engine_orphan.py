"""引擎归属的结构判例（CONTRACT §61.8 v2.0.0 改写：启动 adopt、退出只停自己派生的；§61.3 / §79）。

行为本身（adopt / foreign / idle 三格启动判决、pgrep 列表解析、退出判决含 engineHandoff、pidfile
内容、TERM→KILL 升级、有界轮询）由 `shell/tests/LaunchHarness.swift` 的 [8]/[9] 段经缝钉死；
installer 一根手指不碰引擎由 `tests/test_install_ui_step.py` 用真 bash + 假 pgrep/pkill 钉。这里只钉
「结构性的话」——改了一边忘另一边就红：

- 归属长在 `shell/Sources/EngineOwnership.swift`，`Recording.swift`（§61.3 的 source of truth，
  哈希判例在 test_shell_engine_mirror.py）里一个字都不提它；kill 模式**逐字复用**
  `RecordingController.enginePattern`；
- recipe 标记两段与 `Recording.swift` 的 startCommand 逐字同源（`screenpipe@0.3.349 record` +
  `--ignored-windows`）；
- **启动路径没有任何 kill**：`adoptAtLaunch` 里不出现 `killEngine`；1.x 的 reclaim / legacy 分支删净；
- 退出凭据是**活性不是历史**（`engineProcess?.isRunning == true`），且 `engineHandoff` 在场时不停；
- main.swift 的接线顺序：adopt 判定在后台 → hop 回 MainActor → 日程执法 → autostart → 才装 5 s tick；
  `applicationWillTerminate` 第一句是 stopAtExit；tick 里同步 pidfile；
- install.sh 不再有 1.x 的孤儿扫除；
- CONTRACT §61.8 是 v2.0.0 改写后的样子，法条引用的判例文件都在。
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHELL = REPO_ROOT / "shell" / "Sources"
INSTALL = REPO_ROOT / "install.sh"
CONTRACT = REPO_ROOT / "docs" / "CONTRACT.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _body(text: str, signature: str) -> str:
    """The body of a Swift func whose declaration line contains `signature` (to the next 4-space `}`)."""
    start = text.index(signature)
    return text[start:text.index("\n    }", start)]


class OwnershipModuleTestCase(unittest.TestCase):
    def setUp(self):
        self.ownership = _read(SHELL / "EngineOwnership.swift")
        self.engine = _read(SHELL / "Recording.swift")

    def test_ownership_lives_outside_the_source_of_truth_engine_file(self):
        self.assertTrue((SHELL / "EngineOwnership.swift").exists())
        self.assertNotIn("EngineOwnership", self.engine,
                         "nothing about ownership may leak into Recording.swift (§61.3)")

    def test_kill_pattern_is_the_engine_constant_never_retyped(self):
        self.assertIn("RecordingController.enginePattern", self.ownership)
        m = re.search(r'nonisolated static let enginePattern = "([^"]+)"', self.engine)
        self.assertIsNotNone(m, "Recording.swift lost enginePattern")
        self.assertEqual(m.group(1), "screenpipe.*[r]ecord")
        self.assertNotIn(m.group(1), self.ownership,
                         "EngineOwnership must borrow enginePattern, never re-spell it")

    def test_recipe_markers_come_from_the_start_command(self):
        m = re.search(r'static let recipeMarkers = \["([^"]+)", "([^"]+)"\]', self.ownership)
        self.assertIsNotNone(m, "EngineOwnership lost recipeMarkers")
        version_marker, exclusion_marker = m.group(1), m.group(2)
        self.assertEqual(version_marker, "screenpipe@0.3.349 record")
        self.assertEqual(exclusion_marker, "--ignored-windows")
        self.assertIn('"npx %s' % version_marker, self.engine,
                      "the version marker must be what ScreenpipeRecipe.startCommand runs")
        self.assertIn('" %s \'"' % exclusion_marker, self.engine,
                      "the exclusion marker must be what ScreenpipeRecipe.exclusionArgs emits")

    def test_only_seams_touch_the_system(self):
        for seam in ("static var listEngines:", "static var engineRunning:", "static var engineSpawnedByUs:",
                     "static var spawnedPid:", "static var handoffPending:", "static var clearHandoff:",
                     "static var killEngine:", "static var writePidfile:", "static var logLine:",
                     "static var pause:"):
            with self.subTest(seam=seam):
                self.assertIn(seam, self.ownership)
        self.assertIn('p.arguments = ["-lf", RecordingController.enginePattern]', self.ownership)
        self.assertIn("RecordingController.isEngineRunning()", self.ownership)

    def test_the_launch_path_never_kills(self):
        adopt = _body(self.ownership, "static func adoptAtLaunch()")
        self.assertNotIn("killEngine", adopt, "§61.8 v2.0.0: nothing is signalled at launch")
        self.assertIn("decideAtLaunch(engines: listEngines())", adopt)
        self.assertIn("if handoff { clearHandoff() }", adopt, "the one-shot handoff is cleared after the decision")
        for retired in ("reclaimAtLaunch", "legacyOwns", "legacyAppRunning", "ZelinAIEngineer", "reclaimBudget"):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, self.ownership, "1.x reclaim / §54 legacy guard retired (tag full)")

    def test_the_exit_credential_is_liveness_and_the_handoff_wins(self):
        self.assertIn("RecordingController.engineProcess?.isRunning == true", self.ownership)
        self.assertNotIn("RecordingController.engineProcess != nil", self.ownership,
                         "an engine handle that is merely non-nil proves nothing (issue #318 review)")
        assigns = re.findall(r"^\s*engineProcess = ", self.engine, flags=re.M)
        self.assertEqual(len(assigns), 1,
                         "Recording.swift assigns engineProcess exactly once and never clears it — "
                         "that is why the credential must read isRunning")
        stop = _body(self.ownership, "static func stopAtExit() {")
        self.assertIn("handoffPending()", stop)
        self.assertLess(stop.index("handoffPending()"), stop.index('killEngine("-TERM")'),
                        "the handoff is consulted before any signal")
        self.assertIn('static let handoffKey = "engineHandoff"', self.ownership)

    def test_the_exit_path_is_bounded_and_the_pidfile_lives_outside_the_repo(self):
        exit_budget = re.search(r"static let exitBudget: TimeInterval = ([\d.]+)", self.ownership)
        self.assertIsNotNone(exit_budget)
        self.assertLessEqual(float(exit_budget.group(1)), 2.0,
                             "willTerminate gets ~5 s from macOS; the engine stop must fit well inside")
        self.assertIn('"~/Library/Application Support/ZelinAIAssistant/engine.pid"', self.ownership)


class MainWiringTestCase(unittest.TestCase):
    """§61.8 的接线就是顺序。"""

    def setUp(self):
        self.main = _read(SHELL / "main.swift")
        self.start = self.main[self.main.index("private func startEngines()"):]

    def test_adopt_runs_off_the_main_thread_and_hops_back(self):
        block = self.start[:self.start.index("private func startRecordingAfterAdopt()")]
        self.assertIn("DispatchQueue.global(qos: .userInitiated).async", block,
                      "pgrep blocks — never on the main thread")
        self.assertIn("EngineOwnership.adoptAtLaunch()", block)
        adopt = block.index("EngineOwnership.adoptAtLaunch()")
        hop = block.index("DispatchQueue.main.async", adopt)
        self.assertIn("MainActor.assumeIsolated", block[hop:])
        self.assertIn("self.startRecordingAfterAdopt()", block[hop:])

    def test_schedule_autostart_and_the_tick_all_sit_after_the_adopt_decision(self):
        after = self.start[self.start.index("private func startRecordingAfterAdopt()"):]
        enforce = after.index('schedule.enforce(reason: "launch")')
        autostart = after.index("RecordingController.shared.autostartIfNeeded()")
        tick = after.index("Timer(timeInterval: 5.0")
        self.assertLess(enforce, autostart)
        self.assertLess(autostart, tick)
        self.assertIn("EngineOwnership.tickQueue.async { EngineOwnership.tickPidfile() }",
                      after[tick:], "the 5 s tick keeps the pidfile honest from a FRESH liveness read")
        self.assertNotIn("syncPidfile(engineAlive: RecordingController.shared.engineRunning)", after,
                         "the cached engineRunning is still the initial false on the first tick outside "
                         "the schedule window and would drop the adopted engine's pidfile")
        before = self.start[:self.start.index("private func startRecordingAfterAdopt()")]
        for forbidden in ("autostartIfNeeded()", 'enforce(reason: "launch")', "Timer(timeInterval: 5.0"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, before,
                                 "%r must not run before the adopt decision: autostart would spawn a "
                                 "second engine beside the one we are about to adopt" % forbidden)

    def test_will_terminate_stops_the_engine_first(self):
        body = self.main[self.main.index("func applicationWillTerminate("):]
        body = body[:body.index("\n    }")]
        self.assertIn("EngineOwnership.stopAtExit()", body)
        self.assertLess(body.index("EngineOwnership.stopAtExit()"), body.index("engineTick?.invalidate()"))

    def test_the_connection_probe_is_the_health_route(self):
        self.assertIn('URL(string: "http://127.0.0.1:\\(port)/api/health")!', self.main)
        self.assertNotIn("/api/board", self.main, "§54.2 v2.0.0: the probe is GET /api/health")


class InstallerHandsOffTestCase(unittest.TestCase):
    def test_install_sh_has_no_engine_sweep(self):
        install = _read(INSTALL)
        self.assertNotIn("ui_sweep_orphan_engine()", install)
        self.assertIsNone(re.search(r"^UI_ENGINE_PATTERN=", install, re.M))
        self.assertIsNone(re.search(r"pkill[^\n]*screenpipe", install),
                          "the installer never signals the screenpipe engine (§61.8 v2.0.0)")


class ContractTestCase(unittest.TestCase):
    def setUp(self):
        self.contract = _read(CONTRACT)

    def test_section_61_8_is_the_v2_rewrite_and_points_at_real_files(self):
        head = "### 61.8 引擎归属：启动 adopt、退出只停自己派生的那台"
        self.assertIn(head, self.contract)
        section = self.contract[self.contract.index(head):self.contract.index("## 62. ")]
        for rel in ("shell/Sources/EngineOwnership.swift", "shell/tests/LaunchHarness.swift",
                    "tests/test_shell_engine_orphan.py", "tests/test_install_ui_step.py"):
            with self.subTest(file=rel):
                self.assertIn(rel.split("/")[-1], section)
                self.assertTrue((REPO_ROOT / rel).exists(), rel)
        for phrase in ("screenpipe@0.3.349 record", "--ignored-windows", "engineHandoff", "engine.pid",
                       "升级与重启永不打断录制"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, section)
        self.assertNotIn("ui_sweep_orphan_engine()", section)


if __name__ == "__main__":
    unittest.main()

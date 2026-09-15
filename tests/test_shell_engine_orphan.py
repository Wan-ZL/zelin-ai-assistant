"""引擎归属与孤儿回收的结构判例（CONTRACT §61.8；§15 / §56.5 追记；§61.3 冻结不动）。

行为本身（四格启动判决、两格退出判决、TERM→KILL 升级、有界轮询）由
`shell/tests/LaunchHarness.swift` 的 [8]/[9] 段经缝钉死；`ui` 步扫除的每一格由
`tests/test_install_ui_step.py` 用真 bash + 假 pgrep/pkill 钉。这里只钉「结构性的话」
——改了一边忘另一边就红：

- 回收长在 `shell/Sources/EngineOwnership.swift` 的**外面**：`Recording.swift` 仍是 mac/
  冻结副本（逐字节判例在 test_shell_engine_mirror.py，这里是本节的近身一句），新模块
  只经缝借它三样公开的东西，且 pkill 的模式**逐字复用** `RecordingController.enginePattern`；
- 退出凭据是**活性不是历史**（`engineProcess?.isRunning == true`，绝不是 `!= nil`——冻结的
  `Recording.swift` 只赋一次值、从不置回 nil，判例连这一点一起钉），退出路径也有 §54 守卫；
- main.swift 的接线顺序 = §61.8 的全部要点：回收在后台 → 完成回调 hop 回 MainActor →
  日程执法 → autostart → 才装 5 s tick；`applicationWillTerminate` 第一句是 stopAtExit；
- install.sh 的三个常量与壳逐字互镜（`UI_ENGINE_PATTERN` / `UI_LEGACY_EXEC_NAME` /
  `UI_EXEC_NAME`），`pkill -KILL` 是显式分支，扫除不长在 `relaunch_shell_app` 里
  （它在「壳还活着吗」那一问上早退，覆盖不了崩溃那一格）；
- CONTRACT §61.8 存在、法条引用的判例文件都在，§15 / §56.5 各有一处交叉引用。
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHELL = REPO_ROOT / "shell" / "Sources"
MAC = REPO_ROOT / "mac" / "Sources"
HARNESS = REPO_ROOT / "shell" / "tests" / "LaunchHarness.swift"
INSTALL = REPO_ROOT / "install.sh"
CONTRACT = REPO_ROOT / "docs" / "CONTRACT.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class FrozenEngineUntouchedTestCase(unittest.TestCase):
    """§61.3：回收站在冻结引擎外面，只经缝借它的公开面。"""

    def setUp(self):
        self.ownership = _read(SHELL / "EngineOwnership.swift")
        self.engine = _read(SHELL / "Recording.swift")

    def test_ownership_lives_outside_the_frozen_engine_file(self):
        self.assertTrue((SHELL / "EngineOwnership.swift").exists())
        self.assertFalse((MAC / "EngineOwnership.swift").exists(),
                         "engine ownership is shell-only (D3: mac/ is frozen)")
        self.assertEqual((SHELL / "Recording.swift").read_bytes(), (MAC / "Recording.swift").read_bytes(),
                         "Recording.swift must stay byte-identical to the frozen mac/ reference (§61.3)")
        self.assertNotIn("EngineOwnership", self.engine,
                         "nothing about ownership may leak into the frozen engine file")

    def test_kill_pattern_is_the_frozen_engine_constant_never_retyped(self):
        # §15 契约停法只有一个模式来源；本模块借它，绝不另写一个字面量
        self.assertIn("RecordingController.enginePattern", self.ownership)
        m = re.search(r'nonisolated static let enginePattern = "([^"]+)"', self.engine)
        self.assertIsNotNone(m, "Recording.swift lost enginePattern")
        self.assertEqual(m.group(1), "screenpipe.*[r]ecord")
        self.assertNotIn(m.group(1), self.ownership,
                         "EngineOwnership must borrow enginePattern, never re-spell it")

    def test_only_seams_touch_the_system(self):
        for seam in ("static var engineRunning:", "static var legacyAppRunning:",
                     "static var engineSpawnedByUs:", "static var killEngine:",
                     "static var logLine:", "static var pause:"):
            with self.subTest(seam=seam):
                self.assertIn(seam, self.ownership)
        # 默认实现直连冻结引擎的三样公开面
        self.assertIn("RecordingController.isEngineRunning()", self.ownership)

    def test_the_exit_credential_is_liveness_not_history(self):
        # `Recording.swift` 只在 startEngineBlocking 里给 engineProcess 赋一次值、从不置回
        # nil，所以 `!= nil` 是「这个壳曾经起过引擎」的终身通行证：日程停过 / 切 off /
        # 引擎崩过之后，退出路径会拿它去 pkill -f 一台外人的引擎（§54 冻结原生 app 那台
        # 就在射程里）。凭据必须是活性。行为判例 = LaunchHarness [9](h)/(i)。
        self.assertIn("RecordingController.engineProcess?.isRunning == true", self.ownership)
        self.assertNotIn("RecordingController.engineProcess != nil", self.ownership,
                         "an engine handle that is merely non-nil proves nothing (issue #318 review)")
        assigns = re.findall(r"^\s*engineProcess = ", self.engine, flags=re.M)
        self.assertEqual(len(assigns), 1,
                         "Recording.swift still assigns engineProcess exactly once and never "
                         "clears it — that is why the credential must read isRunning")
        # 退出路径也让 §54 那个冻结 app 的引擎（`pkill -f` 分不清归属）
        stop = self.ownership[self.ownership.index("static func stopAtExit() {"):]
        stop = stop[:stop.index("\n    }")]
        self.assertIn("legacyAppRunning()", stop,
                      "the exit path mirrors the launch path's §54 guard before it fires")
        self.assertLess(stop.index("legacyAppRunning()"), stop.index('killEngine("-TERM")'),
                        "the guard must be consulted before any signal")

    def test_legacy_guard_names_the_frozen_app_executable(self):
        self.assertIn('static let legacyExecName = "ZelinAIEngineer"', self.ownership)
        self.assertIn('Shell.run("/usr/bin/pgrep", ["-x", legacyExecName])', self.ownership)

    def test_both_paths_are_bounded(self):
        # 没有无界等待：两个预算都是常量，退出那条必须留在 macOS 的 ~5 s 里
        reclaim = re.search(r"static let reclaimBudget: TimeInterval = ([\d.]+)", self.ownership)
        exit_budget = re.search(r"static let exitBudget: TimeInterval = ([\d.]+)", self.ownership)
        self.assertIsNotNone(reclaim)
        self.assertIsNotNone(exit_budget)
        self.assertLessEqual(float(exit_budget.group(1)), 2.0,
                             "willTerminate gets ~5 s from macOS; the engine stop must fit well inside")
        self.assertLessEqual(float(reclaim.group(1)), 5.0)


class MainWiringTestCase(unittest.TestCase):
    """§61.8 的全部要点就是顺序。"""

    def setUp(self):
        self.main = _read(SHELL / "main.swift")
        self.start = self.main[self.main.index("private func startEngines()"):]

    def test_reclaim_runs_off_the_main_thread_and_hops_back(self):
        block = self.start[:self.start.index("private func startRecordingAfterReclaim()")]
        self.assertIn("DispatchQueue.global(qos: .userInitiated).async", block,
                      "pgrep/pkill block — never on the main thread")
        self.assertIn("EngineOwnership.reclaimAtLaunch()", block)
        reclaim = block.index("EngineOwnership.reclaimAtLaunch()")
        hop = block.index("DispatchQueue.main.async", reclaim)
        self.assertIn("MainActor.assumeIsolated", block[hop:],
                      "RecordingSchedule / autostart are MainActor-isolated — the completion must hop back")
        self.assertIn("self.startRecordingAfterReclaim()", block[hop:])

    def test_schedule_autostart_and_the_tick_all_sit_after_the_reclaim(self):
        after = self.start[self.start.index("private func startRecordingAfterReclaim()"):]
        enforce = after.index('schedule.enforce(reason: "launch")')
        autostart = after.index("RecordingController.shared.autostartIfNeeded()")
        tick = after.index("Timer(timeInterval: 5.0")
        self.assertLess(enforce, autostart)
        self.assertLess(autostart, tick,
                        "the 5 s tick is installed last: §61.7's anti-flap windows must not see the reclaim's pkill")
        # 回收之前的那一半里一个都不许出现
        before = self.start[:self.start.index("private func startRecordingAfterReclaim()")]
        for forbidden in ("autostartIfNeeded()", 'enforce(reason: "launch")', "Timer(timeInterval: 5.0"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, before,
                                 "%r must not run before the orphan reclaim (issue #318)" % forbidden)

    def test_will_terminate_stops_the_engine_first(self):
        body = self.main[self.main.index("func applicationWillTerminate("):]
        body = body[:body.index("\n    }")]
        self.assertIn("EngineOwnership.stopAtExit()", body,
                      "quitting the shell must stop the engine it spawned (§15 2026-09-14 追记)")
        self.assertLess(body.index("EngineOwnership.stopAtExit()"), body.index("engineTick?.invalidate()"),
                        "stop the engine before tearing the ticks down")


class InstallSweepTestCase(unittest.TestCase):
    """installer 侧：常量互镜 + 扫除住在够得着崩溃那一格的地方。"""

    def setUp(self):
        self.install = _read(INSTALL)
        self.ownership = _read(SHELL / "EngineOwnership.swift")
        self.engine = _read(SHELL / "Recording.swift")

    def _fn(self, name):
        m = re.search(r"^%s\(\) \{.*?^\}" % re.escape(name), self.install, flags=re.S | re.M)
        self.assertIsNotNone(m, "install.sh no longer defines %s()" % name)
        return m.group(0)

    def test_pattern_and_exec_names_mirror_the_shell_verbatim(self):
        m = re.search(r'^UI_ENGINE_PATTERN="([^"]+)"', self.install, re.M)
        self.assertIsNotNone(m, "install.sh lost UI_ENGINE_PATTERN")
        swift = re.search(r'nonisolated static let enginePattern = "([^"]+)"', self.engine)
        self.assertEqual(m.group(1), swift.group(1),
                         "the installer's sweep pattern must be the shell's enginePattern verbatim")
        legacy = re.search(r'^UI_LEGACY_EXEC_NAME="([^"]+)"', self.install, re.M)
        self.assertIsNotNone(legacy, "install.sh lost UI_LEGACY_EXEC_NAME")
        self.assertIn('static let legacyExecName = "%s"' % legacy.group(1), self.ownership)

    def test_sweep_is_guarded_by_shell_absent_and_legacy_absent(self):
        fn = self._fn("ui_sweep_orphan_engine")
        self.assertIn('[ "$(uname -s)" = "Darwin" ] || return 0', fn)
        self.assertIn('if pgrep -x "$UI_EXEC_NAME" >/dev/null 2>&1; then return 0; fi', fn)
        self.assertIn('if pgrep -x "$UI_LEGACY_EXEC_NAME" >/dev/null 2>&1; then return 0; fi', fn)
        self.assertIn('pkill -f "$UI_ENGINE_PATTERN"', fn)
        self.assertNotIn("NON_INTERACTIVE", fn,
                         "a crashed shell leaves an orphan in interactive runs too")

    def test_the_ui_step_calls_the_sweep_before_building(self):
        fn = self._fn("install_ui")
        self.assertIn("ui_sweep_orphan_engine", fn)
        self.assertLess(fn.index("ui_sweep_orphan_engine"), fn.index("install_web_ui"),
                        "sweep first: the orphan must not keep recording for the whole build budget")

    def test_relaunch_kills_only_when_needed_and_sweeps_after_a_sigkill(self):
        fn = self._fn("relaunch_shell_app")
        kill = fn.index('pkill -KILL -x "$UI_EXEC_NAME"')
        guard = fn.rindex('if pgrep -x "$UI_EXEC_NAME" >/dev/null 2>&1; then', 0, kill)
        self.assertLess(guard, kill, "the SIGKILL is an explicit branch, not an unconditional shot")
        self.assertIn("ui_sweep_orphan_engine", fn[kill:],
                      "after a SIGKILL applicationWillTerminate never ran — sweep the engine there")
        self.assertLess(fn.index("ui_sweep_orphan_engine"), fn.index("open -g"),
                        "sweep before relaunching, so the new shell starts from a clean slate")


class ContractTestCase(unittest.TestCase):
    def setUp(self):
        self.contract = _read(CONTRACT)

    def test_section_61_8_exists_and_points_at_real_files(self):
        self.assertIn("### 61.8 引擎归属与孤儿回收", self.contract)
        section = self.contract[self.contract.index("### 61.8 引擎归属与孤儿回收"):
                                self.contract.index("## 62. ")]
        for rel in ("shell/Sources/EngineOwnership.swift", "shell/tests/LaunchHarness.swift",
                    "tests/test_shell_engine_orphan.py", "tests/test_install_ui_step.py"):
            with self.subTest(file=rel):
                self.assertIn(rel.split("/")[-1], section)
                self.assertTrue((REPO_ROOT / rel).exists(), rel)
        # 诚实边界必须成文（崩溃那一格不被退出路径覆盖）
        self.assertIn("崩溃", section)
        self.assertIn("ui_sweep_orphan_engine", section)
        self.assertIn("ZelinAIEngineer", section)

    def test_the_behaviour_change_is_recorded_in_section_15_and_56_5(self):
        self.assertIn("2026-09-14 追记（issue #318，§61.8 引擎归属", self.contract,
                      "§15 录制三态 must say out loud that quitting now stops recording")
        self.assertIn("§56.5 追记（2026-09-14，add-only；issue #318", self.contract,
                      "§56.5's relaunch rule needs the cross-reference")

    def test_the_decision_row_quotes_the_issue(self):
        # D 号按「origin/dev 上最大 D + 1」现铸，rebase 时可能被别的 PR 占掉而要改号——
        # 所以判例按**内容**找那一行（issue #318 + 本模块），再要求那个号在表里唯一、
        # 且 CONTRACT 的三处引用与它一致。改号不再需要改判例。
        plan = _read(REPO_ROOT / "docs" / "design" / "vnext2-plan.md")
        rows = [ln for ln in plan.splitlines()
                if re.match(r"^\| D\d+ \|", ln) and "issue #318" in ln
                and "EngineOwnership.swift" in ln]
        self.assertEqual(len(rows), 1, "exactly one decision row owns issue #318")
        row = rows[0]
        self.assertIn("Claude 按 owner 授权代拍", row)
        self.assertIn("不要静默认领", row, "the delegation quote is the issue's Expected, verbatim")
        d = re.match(r"^\| (D\d+) \|", row).group(1)
        same = [ln for ln in plan.splitlines() if ln.startswith("| %s |" % d)]
        self.assertEqual(len(same), 1, "%s is minted twice — renumber (max D on dev + 1)" % d)
        self.assertIn("owner 决策 **%s**" % d, self.contract,
                      "§61.8's heading must cite the same decision row")
        self.assertEqual(self.contract.count("issue #318，§61.8 引擎归属；owner 决策 %s" % d), 1,
                         "the §15 追记 must cite the same decision row")
        self.assertEqual(self.contract.count("issue #318 / 决策 %s" % d), 1,
                         "the §56.5 追记 must cite the same decision row")


if __name__ == "__main__":
    unittest.main()

"""壳桥 §61.1 追记（parity batch shell-recording-bridge）的两侧互镜判例。

原生两处 setMode「开启」（Onboarding.record / Permissions 状态行）在 setMode 前都先
`if !hasScreenPermission { requestScreenPermission() }`；web 移植把这句丢了。本批在桥里补回
（`RecordingActions.turnOn`）。SetupWizard 终章「启动引擎」带同一句守卫但接的是 restartEngine——
对应桥的 `restartRecording`，守卫留在 web 半边（FinaleStep 自己先发 requestPermission），桥的
restartRecording 与其余原生 restart 按钮一样不带守卫。另 add-only 地把原生录制 / 字幕 / 权限面还在
用的几个值推给页面：
`recording.self_heal_note` / `recording.log_tail`、`captions.translation_note` /
`translation_active` / `source_note` / `apple_engine_available`、`permissions.screen_requested`，
加方法 `refreshRecording`。这里钉：

- 每个新键 Swift 快照与 `web/src/shellBridge.ts` 类型两侧都有（防腐 #10 逐字镜像）；
- `refreshRecording` 两侧词表都有，`getState` 仍是纯读（不经 RecordingActions.refresh）；
- `setRecording on:true` 走 `RecordingActions.turnOn`，turnOn = 缺授权先请求、再 setMode（顺序）；
  `restartRecording` 直连 restartEngine、不经 RecordingActions（向导终章的守卫在 FinaleStep.tsx）；
- `translation_note` 不是活性信号：冻结引擎里翻译关着它也是 ""、Ark 报错时 translationActive 仍 true——
  「在不在翻」只看 `translation_active`（§61.1 追记 (c) 的措辞据此）；
- 缝长在 ShellBridge.swift 外面的引擎文件一个字不动（§61.3 逐字节判例在 test_shell_engine_mirror.py）；
- §61.4 追记：LegacyPrefs 的悬浮窗 frame 键 = NSWindow 对 CaptionOverlay 同名 autosave 的记录键，
  且带自己的一次性标记（不复用 `legacyPrefsSeeded`）；
- `PermissionsProbe.screenRequestedKey` 与原生 Permissions.swift 的 UserDefaults 键同名。

行为本身（缝注入下的请求次数 / 顺序 / frame 只种一次）由 `shell/tests/run.sh` 的 BridgeHarness 钉。
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHELL = REPO_ROOT / "shell" / "Sources"
MAC = REPO_ROOT / "mac" / "Sources"
HARNESS = REPO_ROOT / "shell" / "tests" / "BridgeHarness.swift"
TS = REPO_ROOT / "web" / "src" / "shellBridge.ts"

NEW_KEYS = {
    "recording": ["self_heal_note", "log_tail"],
    "captions": ["translation_note", "translation_active", "source_note", "apple_engine_available"],
    "permissions": ["screen_requested"],
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class AddOnlyKeysMirrorTestCase(unittest.TestCase):
    def setUp(self):
        self.swift = _read(SHELL / "ShellBridge.swift")
        self.ts = _read(TS)

    def test_every_new_key_exists_on_both_sides(self):
        for block, keys in NEW_KEYS.items():
            for key in keys:
                with self.subTest(block=block, key=key):
                    self.assertIn('"%s"' % key, self.swift, "ShellBridge.stateSnapshot lacks %r" % key)
                    self.assertIsNotNone(
                        re.search(r"^\s+%s\??: " % re.escape(key), self.ts, re.MULTILINE),
                        "web/src/shellBridge.ts lacks wire key %r" % key)

    def test_new_keys_read_the_frozen_engine_fields_verbatim(self):
        # 值的来源是逐字节冻结的引擎副本里的 @Published（Recording.swift / LiveCaptions.swift）
        self.assertIn('"self_heal_note": rec.selfHealNote', self.swift)
        self.assertIn('"log_tail": rec.diagnosis?.logTail ?? ""', self.swift)
        self.assertIn('"translation_note": cap.translationNote', self.swift)
        self.assertIn('"translation_active": cap.translationActive', self.swift)
        self.assertIn('"source_note": cap.sourceNote', self.swift)
        self.assertIn('"apple_engine_available": appleCaptionEngineAvailable()', self.swift)
        self.assertIn('"screen_requested": Prefs.bool(PermissionsProbe.screenRequestedKey, default: false)',
                      self.swift)
        recording = _read(SHELL / "Recording.swift")
        self.assertIn("@Published private(set) var selfHealNote", recording)
        self.assertIn("let logTail: String", recording)
        captions = _read(SHELL / "LiveCaptions.swift")
        for field in ("sourceNote", "translationNote", "translationActive"):
            self.assertIn("@Published private(set) var %s" % field, captions)
        self.assertIn("func appleCaptionEngineAvailable() -> Bool", captions)

    def test_translation_note_is_not_an_activity_signal(self):
        # §61.1 追记 (c) 的措辞据此：冻结引擎的 recomputeTranslation 在翻译关着时也把 note 置 ""，
        # Ark 途中报错把错误句写进 note 而 translationActive 不动——所以 "" ≠ 在翻、非空 ≠ 没在翻，
        # 「在不在翻」只看 translation_active。原生设置区也只在非空时渲染 note。
        captions = _read(SHELL / "LiveCaptions.swift")
        body = re.search(r"private func recomputeTranslation\(\) \{(.*?)\n    \}", captions, re.DOTALL).group(1)
        off_branch = re.search(r"guard translateEnabled else \{(.*?)\}", body, re.DOTALL).group(1)
        self.assertIn("translationActive = false", off_branch)
        self.assertIn('translationNote = ""', off_branch)
        on_error = re.search(r"onError: \{ \[weak self\] message in\s*(.*?)\}", captions, re.DOTALL).group(1)
        self.assertIn("translationNote = message", on_error)
        self.assertNotIn("translationActive", on_error)
        settings = _read(MAC / "SettingsLiveCaptions.swift")
        self.assertIsNotNone(re.search(r"if !cap\.translationNote\.isEmpty \{\s*statusRow\(cap\.translationNote",
                                       settings))
        # 桥 / web 两侧的注释都不许再写「"" = 在翻」
        for text in (self.swift, self.ts):
            self.assertNotIn('"" = 在翻', text)

    def test_web_normalize_defaults_every_new_key(self):
        # 老壳缺席 → "" / false（asString / asBool 默认），页面永不读到 undefined。
        # 唯一的三态是 apple_engine_available（§61.1 追记，batch captions-settings-notes）：缺席 / 非布尔 → null，
        # 页面据此不出引擎脚注——「不知道」不能被补成「这台 Mac 低于 macOS 26」。
        for key in NEW_KEYS["recording"]:
            self.assertIn("%s: asString(rec.%s)" % (key, key), self.ts)
        self.assertIn("translation_note: asString(cap.translation_note)", self.ts)
        self.assertIn("translation_active: asBool(cap.translation_active)", self.ts)
        self.assertIn("source_note: asString(cap.source_note)", self.ts)
        self.assertIn("apple_engine_available: asBoolOrNull(cap.apple_engine_available)", self.ts)
        self.assertIn("apple_engine_available?: boolean | null;", self.ts)
        self.assertIn("screen_requested: asBool(perm.screen_requested)", self.ts)


class RefreshAndTurnOnTestCase(unittest.TestCase):
    def setUp(self):
        self.swift = _read(SHELL / "ShellBridge.swift")
        self.ts = _read(TS)

    def test_refresh_recording_method_on_both_sides(self):
        self.assertIn('case "refreshRecording":', self.swift)
        self.assertIn('| "refreshRecording"', self.ts)
        # 执行体 = 5 s tick 的两步（§61.3 启动序列），经缝可注入
        refresh = re.search(r"static var refresh: \(\) -> Void = \{(.*?)\n    \}", self.swift, re.DOTALL)
        self.assertIsNotNone(refresh)
        self.assertIn("RecordingController.shared.pollScreenPermission()", refresh.group(1))
        self.assertIn("RecordingController.shared.refreshEngineState()", refresh.group(1))

    def test_get_state_stays_pure(self):
        # `case "getState":` 下一行就是 break——不刷新、不碰 TCC（startShellBridge 连上就拉它）
        m = re.search(r'case "getState":\s*\n\s*break', self.swift)
        self.assertIsNotNone(m, "getState must stay a pure snapshot read")

    def test_turn_on_requests_screen_permission_before_set_mode(self):
        # setRecording on:true → RecordingActions.turnOn(requested)；on:false → RecordingActions.setMode("off")
        case = self.swift[self.swift.index('case "setRecording":'):self.swift.index('case "refreshRecording":')]
        self.assertIn("RecordingActions.turnOn(requested)", case)
        self.assertIn('RecordingActions.setMode("off")', case)
        self.assertNotIn("RecordingController.shared.setMode", case)
        turn_on = re.search(r"static func turnOn\(_ mode: String\) \{(.*?)\n    \}", self.swift, re.DOTALL)
        self.assertIsNotNone(turn_on)
        body = turn_on.group(1)
        request = body.index("if !hasScreenPermission() { requestScreenPermission() }")
        set_mode = body.index("setMode(mode)")
        self.assertLess(request, set_mode, "the TCC prompt must precede setMode (native Onboarding.record order)")
        # 缝默认直连引擎的两个 nonisolated 静态（Recording.swift 冻结，不长缝）
        self.assertIn("static var hasScreenPermission: () -> Bool = { RecordingController.hasScreenPermission() }",
                      self.swift)
        self.assertIn("static var requestScreenPermission: () -> Void = { RecordingController.requestScreenPermission() }",
                      self.swift)
        self.assertIn("static var setMode: (String) -> Void = { RecordingController.shared.setMode($0) }", self.swift)

    # 冻结参考里的那句守卫；`granted, ` 是 Onboarding.record 的前置条件
    GUARD = (r"if (?:granted, )?!RecordingController\.hasScreenPermission\(\) \{\s*"
             r"RecordingController\.requestScreenPermission\(\)\s*\}\s*(?://[^\n]*\n\s*)*")

    def test_native_set_mode_turn_on_sites_share_the_guard(self):
        # 冻结参考：原生两处 setMode「开启」确实都是「守卫 → setMode」（本批复原的依据）
        guard_then_set_mode = re.compile(self.GUARD + r"(?:RecordingController\.shared|rec)\.setMode\(")
        for name in ("Onboarding.swift", "Permissions.swift"):
            with self.subTest(file=name):
                self.assertIsNotNone(guard_then_set_mode.search(_read(MAC / name)),
                                     "%s lost the guard → setMode pair" % name)

    def test_setup_wizard_start_engine_is_a_restart_site_not_a_set_mode_site(self):
        # SetupWizard 终章「启动引擎」= 守卫 → restartEngine（不是 setMode）：它对应桥的 restartRecording，
        # 不归 setRecording 的守卫管；SetupWizard 里根本没有 setMode 调用
        wizard = _read(MAC / "SetupWizard.swift")
        self.assertIsNotNone(re.search(self.GUARD + r"rec\.restartEngine\(\)", wizard))
        self.assertNotIn(".setMode(", wizard)

    def test_restart_recording_stays_unguarded_like_every_native_restart_button(self):
        # 桥的 restartRecording 直连 restartEngine、不经 RecordingActions（原生 DashboardView / Pages /
        # Diagnostics / Doctor 的 restart 都不带守卫）；向导终章的守卫在 web 半边 FinaleStep 自己那里
        case = self.swift[self.swift.index('case "restartRecording":'):
                          self.swift.index('case "openScreenRecordingSettings":')]
        self.assertIn("RecordingController.shared.restartEngine()", case)
        self.assertNotIn("RecordingActions", case)
        for name in ("DashboardView.swift", "Pages.swift", "Diagnostics.swift", "Doctor.swift"):
            with self.subTest(file=name):
                self.assertNotIn("requestScreenPermission", _read(MAC / name))
        finale = _read(REPO_ROOT / "web" / "src" / "components" / "setup" / "FinaleStep.tsx")
        start_engine = finale[finale.index('text("启动引擎"'):]
        request = start_engine.index('shellCall("requestPermission", { kind: "screen" })')
        restart = start_engine.index('shellCall("restartRecording")')
        self.assertLess(request, restart, "FinaleStep keeps its own guard before restartRecording")

    def test_harness_pins_the_seams(self):
        harness = _read(HARNESS)
        for needle in ("RecordingActions.hasScreenPermission", "RecordingActions.requestScreenPermission",
                       "RecordingActions.setMode", "RecordingActions.refresh",
                       '"method": "refreshRecording"', "LegacyPrefs.overlayFrameKey"):
            with self.subTest(needle=needle):
                self.assertIn(needle, harness)


class LegacyOverlayFrameTestCase(unittest.TestCase):
    def test_frame_key_derives_from_the_overlay_autosave_name(self):
        swift = _read(SHELL / "ShellBridge.swift")
        overlay = _read(SHELL / "CaptionOverlay.swift")
        name = re.search(r'setFrameAutosaveName\("([^"]+)"\)', overlay).group(1)
        self.assertEqual(name, "liveCaptionsPanel")
        self.assertIn('static let overlayFrameKey = "NSWindow Frame %s"' % name, swift)
        # 原生同一个 autosave 名（同一条 UserDefaults 记录才搬得过来）
        self.assertIn('setFrameAutosaveName("%s")' % name, _read(MAC / "CaptionOverlay.swift"))

    def test_frame_rides_its_own_one_shot_marker(self):
        swift = _read(SHELL / "ShellBridge.swift")
        legacy = swift[swift.index("enum LegacyPrefs"):]
        # 两个一次性标记从源码里抓出来比：并成同一把键就会让已播种的壳永远收不到 frame
        first_seed = re.search(r'static let marker = "([^"]+)"', legacy).group(1)
        frame_seed = re.search(r'static let overlayFrameMarker = "([^"]+)"', legacy).group(1)
        self.assertEqual(frame_seed, "legacyOverlayFrameSeeded")
        self.assertNotEqual(frame_seed, first_seed, "the frame seed must not reuse the first-run marker")
        # 首批 keys 清单不含 frame（已播种的壳靠第二个标记补收）
        keys = re.search(r"static let keys = \[(.*?)\]", legacy, re.DOTALL).group(1)
        self.assertNotIn("NSWindow Frame", keys)
        self.assertIn("if !target.bool(forKey: overlayFrameMarker)", legacy)
        self.assertIn("target.set(true, forKey: overlayFrameMarker)", legacy)


class ScreenRequestedKeyTestCase(unittest.TestCase):
    def test_pref_key_matches_native_permissions_model(self):
        system = _read(SHELL / "ShellSystem.swift")
        self.assertIn('static let screenRequestedKey = "screenPermissionRequested"', system)
        self.assertIn('Prefs.bool("screenPermissionRequested", default: false)', _read(MAC / "Permissions.swift"))
        # request("screen") 写的与快照读的是同一把键
        self.assertIn("UserDefaults.standard.set(true, forKey: Self.screenRequestedKey)", system)


if __name__ == "__main__":
    unittest.main()

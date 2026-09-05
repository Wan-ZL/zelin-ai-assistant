"""壳桥 §61.1 追记（parity batch shell-recording-bridge）的两侧互镜判例。

原生三处「开启」（Onboarding.record / Permissions 状态行 / SetupWizard 终章）在 setMode 前都
先 `if !hasScreenPermission { requestScreenPermission() }`；web 移植把这句丢了。本批在桥里补回
（`RecordingActions.turnOn`），并 add-only 地把原生录制 / 字幕 / 权限面还在用的几个值推给页面：
`recording.self_heal_note` / `recording.log_tail`、`captions.translation_note` /
`translation_active` / `source_note` / `apple_engine_available`、`permissions.screen_requested`，
加方法 `refreshRecording`。这里钉：

- 每个新键 Swift 快照与 `web/src/shellBridge.ts` 类型两侧都有（防腐 #10 逐字镜像）；
- `refreshRecording` 两侧词表都有，`getState` 仍是纯读（不经 RecordingActions.refresh）；
- `setRecording on:true` 走 `RecordingActions.turnOn`，turnOn = 缺授权先请求、再 setMode（顺序）；
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

    def test_web_normalize_defaults_every_new_key(self):
        # 老壳缺席 → "" / false（asString / asBool 默认），页面永不读到 undefined
        for key in NEW_KEYS["recording"]:
            self.assertIn("%s: asString(rec.%s)" % (key, key), self.ts)
        self.assertIn("translation_note: asString(cap.translation_note)", self.ts)
        self.assertIn("translation_active: asBool(cap.translation_active)", self.ts)
        self.assertIn("source_note: asString(cap.source_note)", self.ts)
        self.assertIn("apple_engine_available: asBool(cap.apple_engine_available)", self.ts)
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

    def test_native_three_turn_on_sites_share_the_guard(self):
        # 冻结参考：原生三处「开启」确实都带这句（本批复原的依据）
        guard = re.compile(r"if (?:granted, )?!RecordingController\.hasScreenPermission\(\) \{\s*"
                           r"RecordingController\.requestScreenPermission\(\)\s*\}")
        for name in ("Onboarding.swift", "Permissions.swift", "SetupWizard.swift"):
            with self.subTest(file=name):
                self.assertIsNotNone(guard.search(_read(MAC / name)), "%s lost the TCC guard" % name)

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
        self.assertIn('static let overlayFrameMarker = "legacyOverlayFrameSeeded"', swift)
        self.assertNotEqual("legacyOverlayFrameSeeded", "legacyPrefsSeeded")
        legacy = swift[swift.index("enum LegacyPrefs"):]
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

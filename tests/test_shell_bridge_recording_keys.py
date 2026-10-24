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
- 缝长在 ShellBridge.swift 外面的引擎文件一个字不动（§61.3 哈希判例在 test_shell_engine_mirror.py）；
- v2.0.0：`captions.*` 四键、`translation_note` 的活性判例与 §61.4 悬浮窗 frame 补种随 Live captions 退役
  （§61.1 / §61.4 tombstone，全文见 tag full）——两侧都不再有它们；
- `PermissionsProbe.screenRequestedKey` 与原生 Permissions.swift 的 UserDefaults 键同名。

行为本身（缝注入下的请求次数 / 顺序）由 `shell/tests/run.sh` 的 BridgeHarness 钉。

v2.0.0：对照原生源（mac/Sources 的 Onboarding / Permissions / SetupWizard 等）的那几句随 mac/ 删除退役
（tombstone，全文见 tag full）；上面列的原生事实是这些判例当初的依据，自此只钉壳与 web 两侧。
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHELL = REPO_ROOT / "shell" / "Sources"
HARNESS = REPO_ROOT / "shell" / "tests" / "BridgeHarness.swift"
TS = REPO_ROOT / "web" / "src" / "shellBridge.ts"

NEW_KEYS = {
    "recording": ["self_heal_note", "log_tail"],
    "permissions": ["screen_requested"],
}
# v2.0.0（§61.1 tombstone）：captions 块随 Live captions 在两侧一起删
RETIRED_CAPTION_KEYS = ["translation_note", "translation_active", "source_note", "apple_engine_available"]


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

    def test_neither_side_has_the_captions_block(self):
        # v2.0.0（§61.1 tombstone）：实时字幕随 Live captions 退役——壳不再发 captions.*，页面不再读
        swift_code = re.sub(r"//[^\n]*", "", self.swift)
        for key in RETIRED_CAPTION_KEYS:
            self.assertIsNone(re.search(r"^\s+%s\??: " % re.escape(key), self.ts, re.MULTILINE), key)
            self.assertNotIn('"%s"' % key, swift_code, key)
        self.assertNotIn("captions", re.sub(r"//[^\n]*", "", self.ts))
        self.assertNotIn('"captions"', swift_code)
        self.assertFalse((SHELL / "LiveCaptions.swift").exists())

    def test_new_keys_read_the_frozen_engine_fields_verbatim(self):
        # 值的来源是 Recording.swift（§61.3 source of truth）里的 @Published
        self.assertIn('"self_heal_note": rec.selfHealNote', self.swift)
        self.assertIn('"log_tail": rec.diagnosis?.logTail ?? ""', self.swift)
        self.assertIn('"screen_requested": Prefs.bool(PermissionsProbe.screenRequestedKey, default: false)',
                      self.swift)
        recording = _read(SHELL / "Recording.swift")
        self.assertIn("@Published private(set) var selfHealNote", recording)
        self.assertIn("let logTail: String", recording)

    def test_web_normalize_defaults_every_new_key(self):
        # 老壳缺席 → "" / false（asString / asBool 默认），页面永不读到 undefined。
        # v2.0.0：captions.* 四键（含三态的 apple_engine_available）随 Live captions 从页面一侧退役。
        for key in NEW_KEYS["recording"]:
            self.assertIn("%s: asString(rec.%s)" % (key, key), self.ts)
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

    def test_restart_recording_stays_unguarded_like_every_native_restart_button(self):
        # 桥的 restartRecording 直连 restartEngine、不经 RecordingActions（原生 DashboardView / Pages /
        # Diagnostics / Doctor 的 restart 都不带守卫）；向导终章的守卫在 web 半边 FinaleStep 自己那里
        case = self.swift[self.swift.index('case "restartRecording":'):
                          self.swift.index('case "openScreenRecordingSettings":')]
        self.assertIn("RecordingController.shared.restartEngine()", case)
        self.assertNotIn("RecordingActions", case)
        finale = _read(REPO_ROOT / "web" / "src" / "components" / "setup" / "FinaleStep.tsx")
        start_engine = finale[finale.index('text("启动引擎"'):]
        request = start_engine.index('shellCall("requestPermission", { kind: "screen" })')
        restart = start_engine.index('shellCall("restartRecording")')
        self.assertLess(request, restart, "FinaleStep keeps its own guard before restartRecording")

    def test_harness_pins_the_seams(self):
        harness = _read(HARNESS)
        for needle in ("RecordingActions.hasScreenPermission", "RecordingActions.requestScreenPermission",
                       "RecordingActions.setMode", "RecordingActions.refresh",
                       '"method": "refreshRecording"'):
            with self.subTest(needle=needle):
                self.assertIn(needle, harness)


class ScreenRequestedKeyTestCase(unittest.TestCase):
    def test_pref_key_matches_native_permissions_model(self):
        system = _read(SHELL / "ShellSystem.swift")
        self.assertIn('static let screenRequestedKey = "screenPermissionRequested"', system)
        # request("screen") 写的与快照读的是同一把键
        self.assertIn("UserDefaults.standard.set(true, forKey: Self.screenRequestedKey)", system)


if __name__ == "__main__":
    unittest.main()

"""shell/ 承接录制引擎的判例（CONTRACT §61.3 / §61.5）。

v2.0.0 起 shell/Sources/Recording.swift 是录制引擎的 **source of truth**（§61.3 修订）：
mac/ 冻结参考随 lite 2.0 退役，「shell 副本与 mac 原件逐字节相同」的镜像判例改为 tombstone
——这里只钉 shell 文件自己的 SHA-256（改它 = 改法：同 PR 改 §61.3 与这里的哈希）。

- Recording.swift / Helpers/VaultSyncHelper.swift：钉 SHA-256（字节不动）。
- 退役（全文见 tag full）：CaptionCore / LiveCaptions / CaptionOverlay 的镜像与差异判例
  （Live captions 退役，§36 tombstone）、framegrab 的逐字节判例（随 Slack radar 退役）、
  NotifyRelay 对 mac/ 的差异判例（mac/ 退役；§28 常量仍在下面钉）。
- ShellSupport.swift 的 FailureCatalog 引擎子集：每句与 act/lib/failures.py 的
  plain_zh / plain_en 逐字一致（§25 的第二个 Swift 镜像，不许各说各话）。
- shell/Info.plist 带 NSMicrophoneUsageDescription（「屏幕 + 音频」模式 requestAccess 缺它即被
  macOS 杀进程）、不再带 NSAppleEventsUsageDescription；shell/build.sh 编 shell/Sources/*.swift
  （I18n.swift 已在其中）、链接引擎需要的框架、不再编 framegrab。
- ShellBridge.swift 的 wire 词表与 web/src/shellBridge.ts 的类型逐字互镜（防腐 #10），
  v2.0.0 退役的面在两边都不许再出现。
"""
import hashlib
import re
import unittest
from pathlib import Path

from act.lib import failures

REPO_ROOT = Path(__file__).resolve().parent.parent
SHELL = REPO_ROOT / "shell" / "Sources"

# §61.3（v2.0.0 修订）：source of truth 的字节指纹。改这两个文件 = 同 PR 改 §61.3 与这里。
PINNED_SHA256 = {
    "shell/Sources/Recording.swift": "f1015bf30e1498b9773dcc9d126a79bf64b2b90c53da371dfb12114a5380ee18",
    "shell/Helpers/VaultSyncHelper.swift": "b3c6b49bedd5418b9643bc283c5cc3ab9650fd14bfc3a9792773fd43ec81000f",
}

# FailureCatalog ids the shell needs (Recording.swift flashNote / postSystemNotice
# + the bridge's diagnosis vocabulary). Everything else stays in Doctor.swift.
SHELL_FAILURE_IDS = [
    "node_missing", "engine_dead", "engine_npm_download", "engine_crashed",
    "engine_ffmpeg_missing", "screen_tcc_lost",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class PinnedSourceOfTruthTestCase(unittest.TestCase):
    """§61.3 tombstone of the mac/ mirror: the shell files are the truth, pinned by hash."""

    def test_source_of_truth_files_are_byte_stable(self):
        for rel, digest in PINNED_SHA256.items():
            with self.subTest(file=rel):
                got = hashlib.sha256((REPO_ROOT / rel).read_bytes()).hexdigest()
                self.assertEqual(got, digest,
                                 "%s changed — it is the §61.3 source of truth; a change is a law "
                                 "change (amend §61.3 and this pin in the same PR)" % rel)

    def test_retired_engine_files_are_gone_from_the_shell(self):
        for name in ("CaptionCore.swift", "LiveCaptions.swift", "CaptionOverlay.swift",
                     "TerminalLauncher.swift", "TerminalRelay.swift"):
            with self.subTest(file=name):
                self.assertFalse((SHELL / name).exists(), "%s retired in v2.0.0 (tag full)" % name)
        self.assertFalse((REPO_ROOT / "shell" / "Helpers" / "framegrab.swift").exists())

    def test_notify_relay_keeps_the_shell_click_target_and_section_28_constants(self):
        shell_text = _read(SHELL / "NotifyRelay.swift")
        self.assertNotIn("MainWindowController.shared", shell_text)
        self.assertIn("ShellWindow.show?()", shell_text)
        self.assertIn("static let staleAfter: TimeInterval = 600", shell_text)
        self.assertIn("static let burstCap = 5", shell_text)


class FailureCatalogMirrorTestCase(unittest.TestCase):
    def test_shell_catalog_sentences_match_python_verbatim(self):
        swift = _read(SHELL / "ShellSupport.swift")
        for fid in SHELL_FAILURE_IDS:
            with self.subTest(failure_id=fid):
                entry = failures.FAILURES[fid]
                block = re.search(
                    r'case "%s":\s*return L\("((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"\)'
                    % re.escape(fid), swift, re.DOTALL)
                self.assertIsNotNone(block, "ShellSupport.FailureCatalog lacks %r" % fid)
                zh = block.group(1).replace('\\"', '"')
                en = block.group(2).replace('\\"', '"')
                self.assertEqual(zh, entry["plain_zh"])
                self.assertEqual(en, entry["plain_en"])

    def test_shell_catalog_is_the_engine_subset_only(self):
        # every id the shell knows must exist in python (no invented ids)
        swift = _read(SHELL / "ShellSupport.swift")
        catalog = swift[swift.index("enum FailureCatalog"):]
        catalog = catalog[:catalog.index("// MARK: - Language")]
        for fid in re.findall(r'case "([a-z_]+)":', catalog):
            self.assertIn(fid, failures.FAILURES, "unknown failure id %r in shell" % fid)


class BundleAndBuildTestCase(unittest.TestCase):
    def test_info_plist_declares_microphone_usage(self):
        plist = _read(REPO_ROOT / "shell" / "Info.plist")
        self.assertIn("<key>NSMicrophoneUsageDescription</key>", plist)
        # v2.0.0: no Apple Events (the terminal takeover retired with TerminalLauncher)
        self.assertNotIn("NSAppleEventsUsageDescription", plist)
        self.assertIn("<string>com.zelin.ai-board</string>", plist, "bundle id unchanged (TCC identity)")
        # Dock-only (D3): the shell must NOT be a UI-element (menu-bar) app
        self.assertNotIn("LSUIElement", plist)

    def test_build_script_compiles_every_shell_source_with_engine_frameworks(self):
        build = _read(REPO_ROOT / "shell" / "build.sh")
        self.assertIn('"$SRC_DIR"/*.swift', build)
        self.assertNotIn("shared/Sources", build, "I18n.swift lives in shell/Sources since v2.0.0")
        self.assertTrue((SHELL / "I18n.swift").exists())
        for fw in ["AVFoundation", "ScreenCaptureKit", "UserNotifications", "WebKit", "SwiftUI",
                   "ServiceManagement", "Carbon"]:
            self.assertIn("-framework %s" % fw, build)
        # 签名（§54.3 2026-09-12 修正：稳定身份优先、有界、缺证书才回落 ad-hoc）
        # 不在本文件——判例见 tests/test_shell_build_codesign_identity.py 与
        # tests/integration/test_shell_build_codesign_budget.py
        # §68.13 helper CLI rides in the shell bundle (vault-sync-helper); framegrab retired
        self.assertIn('VAULTSYNC_SRC="$HELPERS_DIR/VaultSyncHelper.swift"', build)
        self.assertNotIn("FRAMEGRAB_SRC", build)
        self.assertIn('"$BUILD_DIR/vault-sync-helper" "$APP_DIR/Contents/MacOS/vault-sync-helper"', build)
        # the stable signing identity's setup script moved with the shell (§54.3)
        self.assertIn("shell/scripts/make-signing-cert.sh", build)
        self.assertTrue((REPO_ROOT / "shell" / "scripts" / "make-signing-cert.sh").exists())

    def test_helper_lookup_is_the_shell_only(self):
        # ingest/vault-sync.sh（§68.13；v2.0.0 起旧菜单栏 app 退役，"(old)" 路径与它的 bundle id
        # 不再查找）：只认产品路径上的壳；存在性判例在 tests/test_vault_sync_helper_resolution.py
        sh = _read(REPO_ROOT / "ingest" / "vault-sync.sh")
        self.assertIn('"$dir/Zelin\'s AI Assistant.app/Contents/MacOS/vault-sync-helper"', sh)
        self.assertNotIn('(old).app"', sh)
        self.assertNotIn("com.zelin.ai-engineer", sh)
        # (the radar_slack framegrab lookup and framegrab itself retired in v2.0.0)

    def test_shell_swift_files_respect_the_file_cap(self):
        # 防腐 #1（hygiene gate enforces this too; this is the readable twin）
        for path in SHELL.glob("*.swift"):
            with self.subTest(file=path.name):
                self.assertLessEqual(sum(1 for _ in _read(path).splitlines()), 1500)


class BridgeWireMirrorTestCase(unittest.TestCase):
    """The JS surface is a wire contract (§61.1): Swift snapshot keys ⇔ TS types."""

    RECORDING_KEYS = ["available", "on", "mode", "engine_running", "diagnosis", "note",
                      "tcc_lost", "screen_permission", "resume_mode"]
    PERMISSION_KEYS = ["screen", "microphone", "notifications",
                       "vault"]   # §68.13 笔记库（Documents）授权（原生 PermissionsModel.vault）
    TOP_KEYS = ["launch_at_login",
                "launch_at_login_available"]   # §28 追记 D39（向导终章「登录时自动启动」行的可用判据）
    METHODS = ["getState", "setRecording", "restartRecording",
               "openScreenRecordingSettings", "setLanguage",
               # §68.13
               "getPermissions", "requestPermission", "openPane", "setLaunchAtLogin",
               # §68.1 目录字段的文件对话框
               "chooseFolder"]
    # v2.0.0（§61.1 tombstone，art.6 v2.0 条款）：页面一侧不再读 / 不再调的桥面——实时字幕的快照块与两种设置方法、
    # Dock 徽章、全局快速捕获快捷键（Swift 一侧随 LiveCaptions / QuickCaptureHotkey / DockBadge 一起删）
    WEB_RETIRED_KEYS = ["captions", "hotkey", "translate_direction", "apple_locale", "ark_model", "font_size", "opacity"]
    WEB_RETIRED_METHODS = ["setCaptions", "setCaptionPrefs", "setBadge", "probeCaptionKey"]

    def setUp(self):
        self.swift = _read(SHELL / "ShellBridge.swift")
        self.ts = _read(REPO_ROOT / "web" / "src" / "shellBridge.ts")

    def test_snapshot_keys_exist_on_both_sides(self):
        for key in self.RECORDING_KEYS + self.PERMISSION_KEYS + self.TOP_KEYS:
            with self.subTest(key=key):
                self.assertIn('"%s"' % key, self.swift)
                self.assertIsNotNone(
                    re.search(r"^\s+%s\??: " % re.escape(key), self.ts, re.MULTILINE),
                    "web/src/shellBridge.ts lacks wire key %r" % key)

    def test_method_vocabulary_matches(self):
        for method in self.METHODS:
            with self.subTest(method=method):
                self.assertIn('case "%s":' % method, self.swift)
                self.assertIn('"%s"' % method, self.ts)

    def test_swift_no_longer_serves_the_retired_bridge_surface(self):
        code = re.sub(r"//[^\n]*", "", self.swift)
        for key in ("captions", "hotkey"):
            with self.subTest(key=key):
                self.assertNotIn('"%s"' % key, code)
        for method in self.WEB_RETIRED_METHODS:
            with self.subTest(method=method):
                self.assertNotIn('case "%s":' % method, code)
        self.assertNotIn("LegacyPrefs", code, "§61.4 one-time native-prefs seed retired")

    def test_web_no_longer_reads_the_retired_bridge_surface(self):
        code = re.sub(r"//[^\n]*|/\*[\s\S]*?\*/", "", self.ts)
        for key in self.WEB_RETIRED_KEYS:
            with self.subTest(key=key):
                self.assertIsNone(re.search(r"\b%s\b" % re.escape(key), code), key)
        for method in self.WEB_RETIRED_METHODS:
            with self.subTest(method=method):
                self.assertNotIn('"%s"' % method, code)

    def test_handler_and_event_names_match(self):
        self.assertIn('static let handlerName = "zaiShell"', self.swift)
        self.assertIn('static let eventName = "zai-shell-state"', self.swift)
        self.assertIn('static let commandEventName = "zai-shell-command"', self.swift)
        self.assertIn('export const SHELL_STATE_EVENT = "zai-shell-state"', self.ts)
        self.assertIn('export const SHELL_HANDLER_NAME = "zaiShell"', self.ts)
        self.assertIn('export const SHELL_COMMAND_EVENT = "zai-shell-command"', self.ts)

    def test_permission_and_pane_vocabularies_match(self):
        system = _read(SHELL / "ShellSystem.swift")
        self.assertIn('static let kinds = ["screen", "microphone", "notifications", "vault"]', system)
        self.assertIn('static let vaultGrantedKey = "vaultAccessGranted"', system)   # 原生 UserDefaults 同名（§66.2）
        for pane in ("full_disk", "screen", "microphone", "notifications", "files_folders"):
            self.assertIn('"%s":' % pane, system)
            self.assertIn('"%s"' % pane, self.ts)
        # server 侧 panes 深链与壳侧同一张表（权限体检页两半共用词表）
        from server import permissions as server_permissions
        for pane, url in server_permissions.PANES.items():
            self.assertIn('"%s": "%s"' % (pane, url), system)


if __name__ == "__main__":
    unittest.main()

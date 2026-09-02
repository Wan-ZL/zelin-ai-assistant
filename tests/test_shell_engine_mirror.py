"""shell/ 承接录制 + 实时字幕引擎的判例（CONTRACT §61.3 / §61.4 / §61.5）。

mac/Sources 在 D3 下是冻结的行为规范（只读参考），引擎文件搬进 shell/Sources 的
要求是**逻辑零改动**——本文件把「零改动」钉成机器可查：

- Recording.swift / CaptionCore.swift / LiveCaptions.swift：shell 副本与 mac 原件
  逐字节相同（谁改一边不改另一边就红）。P8 删 mac/ 时把这条改成 tombstone。
- CaptionOverlay.swift：允许且只允许两处差异——文件头注释、齿轮按钮从原生
  MainNav 改走 ShellNavigation.openSettings（web 设置页）。
- ShellSupport.swift 的 FailureCatalog 引擎子集：每句与 act/lib/failures.py 的
  plain_zh / plain_en 逐字一致（§25 的第二个 Swift 镜像，不许各说各话）。
- shell/Info.plist 带 NSMicrophoneUsageDescription（字幕 requestAccess 缺它即被
  macOS 杀进程）；shell/build.sh 链接引擎需要的框架并只借 shared I18n.swift。
- ShellBridge.swift 的 wire 词表与 web/src/shellBridge.ts 的类型逐字互镜（防腐 #10）。
"""
import difflib
import re
import unittest
from pathlib import Path

from act.lib import failures

REPO_ROOT = Path(__file__).resolve().parent.parent
MAC = REPO_ROOT / "mac" / "Sources"
SHELL = REPO_ROOT / "shell" / "Sources"

VERBATIM = ["Recording.swift", "CaptionCore.swift", "LiveCaptions.swift"]

# FailureCatalog ids the shell needs (Recording.swift flashNote / postSystemNotice
# + the bridge's diagnosis vocabulary). Everything else stays in Doctor.swift.
SHELL_FAILURE_IDS = [
    "node_missing", "engine_dead", "engine_npm_download", "engine_crashed",
    "engine_ffmpeg_missing", "screen_tcc_lost",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class VerbatimCopyTestCase(unittest.TestCase):
    def test_engine_files_are_byte_identical_to_the_frozen_native_reference(self):
        for name in VERBATIM:
            with self.subTest(file=name):
                self.assertEqual(
                    (SHELL / name).read_bytes(), (MAC / name).read_bytes(),
                    "shell/Sources/%s drifted from mac/Sources/%s — the engines move "
                    "AS-IS (§61.3); fix the shell copy, never the frozen mac/ reference"
                    % (name, name))

    def test_caption_overlay_differs_only_in_header_and_gear_action(self):
        mac_lines = _read(MAC / "CaptionOverlay.swift").splitlines()
        shell_lines = _read(SHELL / "CaptionOverlay.swift").splitlines()
        allowed = re.compile(
            r"^\s*$|^\s*//|MainNav|openMainWindow|ShellNavigation|"
            r"^@MainActor$|^enum ShellNavigation \{$|static var openSettings|^\}$")
        offending = []
        for line in difflib.unified_diff(mac_lines, shell_lines, n=0, lineterm=""):
            if line.startswith(("---", "+++", "@@")):
                continue
            body = line[1:]
            if not allowed.search(body):
                offending.append(line)
        self.assertEqual(offending, [],
                         "CaptionOverlay.swift shell copy changed logic beyond the "
                         "documented gear-button rewire:\n" + "\n".join(offending))
        shell_text = "\n".join(shell_lines)
        self.assertNotIn("MainNav.shared", shell_text)
        self.assertIn('ShellNavigation.openSettings?("live_captions")', shell_text)


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
        # Dock-only (D3): the shell must NOT be a UI-element (menu-bar) app
        self.assertNotIn("LSUIElement", plist)

    def test_build_script_compiles_every_shell_source_with_engine_frameworks(self):
        build = _read(REPO_ROOT / "shell" / "build.sh")
        self.assertIn('"$SRC_DIR"/*.swift', build)
        self.assertIn("shared/Sources/I18n.swift", build)
        for fw in ["AVFoundation", "ScreenCaptureKit", "UserNotifications", "WebKit", "SwiftUI"]:
            self.assertIn("-framework %s" % fw, build)
        # ad-hoc signing stays (task constraint) — no stable identity yet
        self.assertIn("codesign --force --deep -s -", build)

    def test_shell_swift_files_respect_the_file_cap(self):
        # 防腐 #1（hygiene gate enforces this too; this is the readable twin）
        for path in SHELL.glob("*.swift"):
            with self.subTest(file=path.name):
                self.assertLessEqual(sum(1 for _ in _read(path).splitlines()), 1500)


class BridgeWireMirrorTestCase(unittest.TestCase):
    """The JS surface is a wire contract (§61.1): Swift snapshot keys ⇔ TS types."""

    RECORDING_KEYS = ["available", "on", "mode", "engine_running", "diagnosis", "note",
                      "tcc_lost", "screen_permission", "resume_mode"]
    CAPTIONS_KEYS = ["available", "on", "engine", "paused", "engine_dead",
                     "status_text", "status_is_error"]
    METHODS = ["getState", "setRecording", "restartRecording",
               "openScreenRecordingSettings", "setCaptions", "setLanguage"]

    def setUp(self):
        self.swift = _read(SHELL / "ShellBridge.swift")
        self.ts = _read(REPO_ROOT / "web" / "src" / "shellBridge.ts")

    def test_snapshot_keys_exist_on_both_sides(self):
        for key in self.RECORDING_KEYS + self.CAPTIONS_KEYS:
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

    def test_handler_and_event_names_match(self):
        self.assertIn('static let handlerName = "zaiShell"', self.swift)
        self.assertIn('static let eventName = "zai-shell-state"', self.swift)
        self.assertIn('export const SHELL_STATE_EVENT = "zai-shell-state"', self.ts)
        self.assertIn('export const SHELL_HANDLER_NAME = "zaiShell"', self.ts)


if __name__ == "__main__":
    unittest.main()

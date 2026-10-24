// ShellSupport.swift — 壳内最小原生残留（R2.2.3）的公共底座：AppPaths / Analytics /
// SettingsIO（只读）/ Shell / Prefs / SecretsIO（只读）/ FailureCatalog（引擎子集）/
// LanguageStore + 窗口三条纯策略（ExternalLinkPolicy / ReopenPolicy / WindowTitlePolicy，
// §54 追记；判例 shell/tests/PolicyHarness.swift）+ 启动来源策略（LaunchPolicy，D38，
// §56.5 / §61 追记；判例 shell/tests/LaunchHarness.swift）+ 主菜单纯表（MenuSpec，
// §54 追记「菜单 l10n」；判例 shell/tests/MenuHarness.swift）。
//
// 为什么这些名字与已退役的 mac/Sources/Utils.swift、Doctor.swift、L10n.swift 同名：
// 录制引擎 Recording.swift（CONTRACT §61.3 的 source of truth，字节不动）调用的正是
// 这组 helper。这里只带读侧，写侧（SettingsIO.writeOverrides）刻意不带——壳只读配置，
// 设置页的写者是 server（§59.5 / R2.10.5）。判例：tests/test_shell_engine_mirror.py
// 钉住 FailureCatalog 句子与 act/lib/failures.py 逐字一致。
//
// 管我的法条：CONTRACT §54（薄壳）、§61（桥 + 引擎落户 shell/）、§25（失败分类句子）、
// §78（v2.0 零遥测：Analytics 是 no-op 桩）。

import AppKit
import SwiftUI
import Foundation

// MARK: - Paths（与 mac AppPaths 同一解析顺序；CONTRACT §19）

enum AppPaths {
    /// env AIASSISTANT_HOME → home.txt 指针 → canonical 默认值。默认值与
    /// server/paths.py DEFAULT_HOME / act/lib/config._home() 逐字同一——这不是
    /// 「猜路径」（§54 只禁止为 spawn server 猜路径），而是三方共享的同一常量：
    /// 引擎只在这里读 config.yaml / secrets，读不到就按缺席处理。
    static let stateRoot: String = {
        if let env = ProcessInfo.processInfo.environment["AIASSISTANT_HOME"],
           !env.isEmpty {
            return (env as NSString).expandingTildeInPath
        }
        let pointer = ("~/Library/Application Support/ZelinAIAssistant/home.txt"
                       as NSString).expandingTildeInPath
        if let text = try? String(contentsOfFile: pointer, encoding: .utf8) {
            let home = (text.trimmingCharacters(in: .whitespacesAndNewlines)
                        as NSString).expandingTildeInPath
            var isDir: ObjCBool = false
            if !home.isEmpty,
               FileManager.default.fileExists(atPath: home, isDirectory: &isDir),
               isDir.boolValue {
                return home
            }
        }
        return ("~/Projects/zelin-ai-assistant" as NSString).expandingTildeInPath
    }()

    static var settingsOverridesPath: String { stateRoot + "/state/settings_overrides.json" }
}

// MARK: - Analytics（v2.0.0 起 no-op 桩；CONTRACT §78 / art.9 零遥测）
//
// Recording.swift 与 RecordingSchedule.swift 仍按原词表调用 `Analytics.log` /
// `Analytics.firstReach`（前者字节不动，§61.3）。v2.0.0 起壳不写 state/analytics/、
// 不读 features.analytics——三个入口保留签名、什么都不做，调用方零改动。
// 原 JSONL 写者与 §16 隐私门全文见 tag full。

enum Analytics {
    static func log(_ event: String, fields: [String: Any] = [:]) {}

    static func flush() {}

    static func firstReach(_ feature: String) {}
}

// MARK: - SettingsIO（只读子集；写者是 server，§59.5）

enum SettingsIO {
    /// Read state/settings_overrides.json as a dictionary ([:] if absent/bad).
    static func readOverrides() -> [String: Any] {
        guard let data = FileManager.default.contents(atPath: AppPaths.settingsOverridesPath),
              let obj = try? JSONSerialization.jsonObject(with: data),
              let dict = obj as? [String: Any]
        else { return [:] }
        return dict
    }

    /// The file EXISTS but does not parse as a JSON object.
    static func overridesUnparseable() -> Bool {
        guard let data = FileManager.default.contents(atPath: AppPaths.settingsOverridesPath)
        else { return false }
        guard let obj = try? JSONSerialization.jsonObject(with: data),
              obj is [String: Any]
        else { return true }
        return false
    }

    /// Naive line-scan of config.yaml (then config.example.yaml) for a scalar key.
    static func configScalar(_ key: String) -> String? {
        for file in [AppPaths.stateRoot + "/config.yaml",
                     AppPaths.stateRoot + "/config.example.yaml"] {
            guard let text = try? String(contentsOfFile: file, encoding: .utf8) else { continue }
            for rawLine in text.split(separator: "\n", omittingEmptySubsequences: true) {
                let line = rawLine.trimmingCharacters(in: .whitespaces)
                guard line.hasPrefix(key + ":") else { continue }
                var v = String(line.dropFirst(key.count + 1)).trimmingCharacters(in: .whitespaces)
                if v.hasPrefix("\"") {
                    let inner = String(v.dropFirst())
                    if let end = inner.firstIndex(of: "\"") {
                        v = String(inner[..<end])
                    } else {
                        v = inner
                    }
                } else if let hash = v.range(of: " #") {
                    v = String(v[..<hash.lowerBound]).trimmingCharacters(in: .whitespaces)
                }
                if !v.isEmpty { return v }
            }
        }
        return nil
    }

    /// §15 v2.0.0 add-only：设置页写的列表 override 先于 config.yaml（键 = config.yaml 叶名 →
    /// overrides 扁平键）。今天只有敏感应用排除表：`recording.ignored_apps` ⇐ `recording_ignored_apps`。
    static let overrideListKeys: [String: String] = ["ignored_apps": "recording_ignored_apps"]

    /// overrides 里的列表值，按 act/lib/config._clean_str_list 同一清洗（字串 / 整数、去空白、丢空项）；
    /// 键缺席或不是列表 = nil（落到 config.yaml）。server 的 diff-write 清空字段 = 删键 = 回到出厂表。
    static func overrideList(_ key: String, overrides: [String: Any]? = nil) -> [String]? {
        guard let flat = overrideListKeys[key],
              let raw = (overrides ?? readOverrides())[flat] as? [Any] else { return nil }
        return raw.compactMap { item -> String? in
            if CFGetTypeID(item as CFTypeRef) == CFBooleanGetTypeID() { return nil }
            if let s = item as? String { return s }
            if let i = item as? Int { return String(i) }
            return nil
        }
        .map { $0.trimmingCharacters(in: .whitespaces) }
        .filter { !$0.isEmpty }
    }

    /// Naive line-scan for a YAML block sequence (config.yaml → example fallback);
    /// nil = key absent from both files, [] = explicit `key: []`. A settings-page
    /// list override (`overrideList`) wins over both files.
    static func configList(_ key: String) -> [String]? {
        if let list = overrideList(key) { return list }
        for file in [AppPaths.stateRoot + "/config.yaml",
                     AppPaths.stateRoot + "/config.example.yaml"] {
            guard let text = try? String(contentsOfFile: file, encoding: .utf8) else { continue }
            var inBlock = false
            var items: [String] = []
            for rawLine in text.components(separatedBy: "\n") {
                let line = rawLine.trimmingCharacters(in: .whitespaces)
                if !inBlock {
                    guard line.hasPrefix(key + ":") else { continue }
                    var rest = String(line.dropFirst(key.count + 1)).trimmingCharacters(in: .whitespaces)
                    if let hash = rest.range(of: "#") { rest = String(rest[..<hash.lowerBound]).trimmingCharacters(in: .whitespaces) }
                    if rest == "[]" { return [] }
                    inBlock = true
                    continue
                }
                if line.isEmpty || line.hasPrefix("#") { continue }
                guard line.hasPrefix("- ") else { break }
                var v = String(line.dropFirst(2)).trimmingCharacters(in: .whitespaces)
                if v.hasPrefix("\"") || v.hasPrefix("'") {
                    let quote = v.first!
                    let inner = String(v.dropFirst())
                    v = inner.firstIndex(of: quote).map { String(inner[..<$0]) } ?? inner
                } else if let hash = v.range(of: " #") {
                    v = String(v[..<hash.lowerBound]).trimmingCharacters(in: .whitespaces)
                }
                if !v.isEmpty { items.append(v) }
            }
            if inBlock { return items }
        }
        return nil
    }
}

// MARK: - Shell helpers (blocking — run OFF the main actor)

enum Shell {
    /// True if `cmd` exits 0 under a login zsh (PATH as in a user terminal).
    static func ok(_ cmd: String) -> Bool {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/zsh")
        p.arguments = ["-lc", cmd]
        p.standardOutput = Pipe()
        p.standardError = Pipe()
        do { try p.run() } catch { return false }
        p.waitUntilExit()
        return p.terminationStatus == 0
    }

    /// Run an executable with args; returns (exit code, combined output tail).
    static func run(_ launchPath: String, _ args: [String]) -> (Int32, String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: launchPath)
        p.arguments = args
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        do { try p.run() } catch {
            return (127, error.localizedDescription)
        }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        let out = String(data: data, encoding: .utf8) ?? ""
        return (p.terminationStatus, String(out.suffix(400)))
    }
}

// MARK: - UserDefaults helpers

enum Prefs {
    static func bool(_ key: String, default def: Bool) -> Bool {
        let d = UserDefaults.standard
        return d.object(forKey: key) == nil ? def : d.bool(forKey: key)
    }

    static func string(_ key: String, default def: String) -> String {
        UserDefaults.standard.string(forKey: key) ?? def
    }
}

// v2.0.0：SecretsIO 随实时字幕退役——壳不再读 config/secrets/ 里的任何文件（火山两把 key
// 是它唯一的读者，§36 tombstone，全文见 tag full）。

// MARK: - Failure catalog（§25 引擎子集；句子与 act/lib/failures.py 逐字一致）
//
// 只带录制引擎会引用的 id（Recording.swift 的 flashNote / postSystemNotice
// 与桥的 diagnosis 文案）。全表镜像仍在 mac/Sources/Doctor.swift；web 面的
// 其余失败句由 server 投影（§49），壳不再是第二份全表。判例：
// tests/test_shell_engine_mirror.py 逐 id 比对 plain_zh / plain_en。

enum FailureCatalog {
    static func message(_ id: String?) -> String? {
        switch id ?? "" {
        case "node_missing":
            return L("缺少 Node.js——录制引擎无法启动",
                     "Node.js is missing — the recording engine cannot start")
        case "engine_dead":
            return L("录制引擎没有在运行——屏幕内容不会被记录",
                     "The recording engine is not running — nothing on screen is being captured")
        case "engine_npm_download":
            return L("录制引擎首次下载中（约 1-3 分钟）——不用做任何事，下载完会自动开始录制",
                     "The recording engine is downloading for the first time (~1-3 min) — nothing to do; recording starts automatically when it finishes")
        case "engine_crashed":
            return L("录制引擎意外停了——点「重启引擎」再试；反复失败就看下面的引擎日志",
                     "The recording engine stopped unexpectedly — click Restart engine; if it keeps happening, check the engine log lines below")
        case "engine_ffmpeg_missing":
            return L("「屏幕+音频」需要 ffmpeg，这台电脑上还没有——装一个（brew install ffmpeg）或切回「仅屏幕」",
                     "Screen + Audio needs ffmpeg, which this Mac does not have — install it (brew install ffmpeg) or switch back to Screen Only")
        case "screen_tcc_lost":
            return L("「屏幕录制」授权被 macOS 收回了（系统更新或重装应用后常见）——重新授权一次即可恢复",
                     "macOS revoked the Screen Recording permission (common after a macOS update or app reinstall) — grant it once more to resume")
        default:
            return nil
        }
    }
}

// MARK: - Language（界面语言 "zh" | "en"）
//
// overrides "language" 显式值优先，缺席跟随系统 locale。刻意**不**做首启持久化——
// 壳不写 overrides（server 是 web 侧写者）。主菜单观察它以便随语言重建；桥的
// `setLanguage` 让页面把 zai.lang 同步过来（§61.1），两边看到同一种语言。

@MainActor
final class LanguageStore: ObservableObject {
    static let shared = LanguageStore()
    @Published var lang: String {
        didSet { LanguageMirror.current = lang }
    }
    private init() {
        let v: String
        if let stored = SettingsIO.readOverrides()["language"] as? String {
            v = stored == "en" ? "en" : "zh"
        } else {
            v = Self.systemDefault
        }
        lang = v
        LanguageMirror.current = v
    }

    nonisolated static var systemDefault: String {
        (Locale.preferredLanguages.first ?? "en").hasPrefix("zh") ? "zh" : "en"
    }
}

// MARK: - 窗口策略（纯函数，无 AppKit 状态；§54 追记「外链 / Dock 重开 / 标题」）
//
// main.swift 的 AppDelegate 只做「问策略 → 执行副作用」两步，判断本身住在这里，
// 好让 shell/tests/PolicyHarness.swift 不用 WKWebView / NSWindow 就能钉住每一格。

/// 外链一律交系统浏览器（原生 Pages.swift DepAction.url / Doctor.swift
/// FailureCatalog.perform 都是 `NSWorkspace.shared.open`）。WKWebView 不实现
/// WKUIDelegate 时 target=_blank / window.open 会被静默取消——壳自 §54 追记起
/// 按本策略分流：看板 SPA 留在同一个 webView，其余 http(s) / mailto 交系统处理者，
/// 别的 scheme 一律不理（allow-list，不是 deny-list）。
enum ExternalLinkPolicy {
    enum Verdict: Equatable {
        /// http://127.0.0.1|localhost|::1:<port>/ 且路径就是 `/`（看板每一页都是
        /// `?page=` query，见 web/src/route.ts）— 看板 SPA 自己，留在壳内加载。
        case board
        /// 其余 http(s)（含看板 origin 上路径不是 `/` 的 server 文件 / API 面：
        /// `/files/…` 交付物、`/api/…`、markdown 相对链接解析出的路径）与 `mailto:`
        /// — `NSWorkspace.shared.open`，系统浏览器 / 邮件客户端接手。壳只有一个
        /// webView、没有后退，永不把它导航到看板之外的页面。
        case external
        /// about:blank / javascript: / data: / blob: / file: / 自定义 scheme / 空 URL
        /// — 什么都不做。页面能发出的只有 http(s) / mailto（markdown sanitizeUrl 白名单）；
        /// 其余 scheme 交 `NSWorkspace.open` 会直接启动处理者（file:///…app、
        /// shortcuts://…），浏览器都不会不问就放行，壳更不。
        case ignore
    }

    private static let loopbackHosts: Set<String> = ["127.0.0.1", "localhost", "::1", "[::1]"]

    static func classify(_ url: URL?, port: Int) -> Verdict {
        guard let url = url, let scheme = url.scheme?.lowercased(), !scheme.isEmpty else {
            return .ignore
        }
        if scheme == "mailto" { return .external }
        guard scheme == "http" || scheme == "https" else { return .ignore }
        // origin = scheme + host + port，三者全对（ShellConfig.boardURL 是明文 http；
        // https://127.0.0.1:<port> 不是同一个 origin）**且**路径是 `/`（或空）才是
        // 看板 SPA；同 origin 的其他路径是 server 的文件 / API 面，按 external。
        let host = (url.host ?? "").lowercased()
        if scheme == "http", loopbackHosts.contains(host), (url.port ?? 80) == port,
           url.path.isEmpty || url.path == "/" {
            return .board
        }
        return .external
    }
}

/// 壳 → 页面换页（菜单 关于 / 设置… ⌘, / 权限体检…；CONTRACT §54.4 D40 追记）：
/// 看板 SPA 已经载入（webView 停在看板 origin 的 `/`，ExternalLinkPolicy 判 `.board`，且不在加载中——
/// `WKWebView.url` 在 `load()` 一发出就是目标 URL，文档还没到、页面还没挂监听）→ 推 `open_page {page, anchor?}`
/// 命令，让页面自己 pushState 换页——不重载文档，store / SSE / 未决的「合并中…」章都留着（原生 openAboutPage
/// 等只是 `MainNav.section = .about`，进程内换页）；还停在内嵌 splash / 失败页（或任何不是看板的 URL）、
/// 或看板正在加载 → 整页加载 `?page=…` 深链：server 已上线就落到想去的那页，还没上线则加载失败、splash 原地
/// 不动（原行为）。第二道保险不在这里：命令推出去没人接（React 树死了 / 老 web 构建）时 `ShellBridge.pushCommand`
/// 的回执是 false，调用方同样退回整页加载。
enum PageOpenPolicy {
    enum Action: Equatable {
        /// 看板在场：`ShellBridge.pushCommand("open_page", args:)`
        case pushCommand(page: String, anchor: String?)
        /// 看板不在场：`webView.load(ShellConfig.pageURL(page, anchor:))`
        case load(page: String, anchor: String?)
    }

    static func action(currentURL: URL?, port: Int, isLoading: Bool, page: String, anchor: String?) -> Action {
        if !isLoading, ExternalLinkPolicy.classify(currentURL, port: port) == .board {
            return .pushCommand(page: page, anchor: anchor)
        }
        return .load(page: page, anchor: anchor)
    }
}

/// Dock 重开只看主窗口，不看 AppKit 的 hasVisibleWindows（原生 AppDelegate.swift
/// applicationShouldHandleReopen + MainWindowController.isWindowOpen 同义）：任何别的
/// 面板（系统弹窗、日后的 NSPanel）都会把 hasVisibleWindows 顶成 true，按它判 Dock
/// 点击就成了空操作。三分：主窗口不在 → show；最小化 → 壳自己 deminiaturize（AppKit
/// 的默认重开只在 hasVisibleWindows == false 时才还原最小化窗口）；可见 → 什么都不做。
enum ReopenPolicy {
    enum Action: Equatable {
        case show
        case deminiaturize
        case none
    }

    static func action(boardVisible: Bool, boardMiniaturized: Bool) -> Action {
        if boardMiniaturized { return .deminiaturize }
        return boardVisible ? .none : .show
    }
}

/// 窗口标题跟随页面（原生 MainWindow.installTitleSink：标题随 section / 语言重算）：
/// 壳这半 KVO 观察 `webView.title`，页面没给标题（内嵌 splash / 空串 / 全空白）时
/// 回落产品名。
enum WindowTitlePolicy {
    static func resolve(pageTitle: String?, fallback: String) -> String {
        let trimmed = (pageTitle ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? fallback : trimmed
    }
}

/// 启动来源决定窗口要不要在启动时前置（D38；原生 AppDelegate.swift「Do NOT show the main
/// window on launch: background restarts (login items, install scripts) would pop it over
/// whatever Zelin is doing」的壳版，CONTRACT §56.5 / §61 追记）。两种**后台启动**——
/// (a) install.sh 自动部署 relaunch：`open -g <bundle> --args --background`（argv 里有
/// `--background`）；(b) 登录时启动：loginwindow 发来的 `oapp` 启动 Apple Event 带
/// `keyAELaunchedAsLogInItem`（`LaunchAtLogin.launchedAsLoginItem()`，ShellSystem.swift）——
/// 窗口照建、**不 orderFront、不 activate**；其余（Dock / Finder / 用户手敲 `open`）照旧前置。
/// 后台启动后的重开入口不变：Dock 点击（ReopenPolicy 见主窗口不在 → show）、
/// 通知点击、菜单深链。纯函数：argv 与登录标记都由 main.swift 采集后喂进来。
enum LaunchPolicy {
    enum Presentation: Equatable {
        /// 建窗 + makeKeyAndOrderFront + activate（用户亲手启动）。
        case foreground
        /// 建窗但不前置、不 activate；`reason` 进 board-shell.log 取证。
        case background(reason: String)
    }

    /// install.sh `relaunch_shell_app` 传的唯一旗标（逐字；`--background=…` 一类变体不认）。
    static let backgroundFlag = "--background"

    /// `arguments` = `CommandLine.arguments` 全量（argv[0] 是可执行路径、永不等于旗标，所以
    /// 不用特判；`open --args` 之后的词从 argv[1] 起原样透传，位置不限、其余参数不理）；
    /// `launchedAsLoginItem` = 启动 Apple Event 的登录项标记。两者任一为真 → background
    /// （argv 先判：它是确定性的、登录标记是系统给的）。
    static func presentation(arguments: [String], launchedAsLoginItem: Bool) -> Presentation {
        if arguments.contains(backgroundFlag) {
            return .background(reason: "argv \(backgroundFlag)")
        }
        if launchedAsLoginItem {
            return .background(reason: "login item")
        }
        return .foreground
    }

    /// 两个失败弹窗（server 连不上 / repo 未配置）的时机。NSAlert 面板住在 modal-panel 层、盖在
    /// 所有 app 的普通窗口之上——登录项启动时 server 冷启动超过 10 s，它就会成为 owner 登录后看到的
    /// 第一件东西，正是 D38 要挡的那种弹窗。
    enum AlertTiming: Equatable {
        /// 立刻 runModal（前台启动、或看板窗口已经在屏上）。
        case now
        /// 压到下一次 showWindow()（Dock 点击 / 通知点击）再 runModal；全文先进 board-shell.log。
        case deferUntilShown
    }

    /// 后台启动且看板窗口还没露过面 → 压后；其余（前台启动、或窗口已可见）→ 照旧立刻弹。
    static func failureAlertTiming(presentation: Presentation, boardVisible: Bool) -> AlertTiming {
        if case .background = presentation, !boardVisible {
            return .deferUntilShown
        }
        return .now
    }
}

// MARK: - 主菜单纯表（§54 追记「菜单 l10n」；原生 AppDelegate.installMainMenu 的壳版）
//
// 原生主菜单每个标题都走 L()，并在语言切换时整个重建（Store.swift `/lang` 与设置页保存后
// `app.installMainMenu()`——NSMenu 不观察任何东西）。壳把「菜单长什么样」抽成这张纯表，
// `MenuSpec.build` 把表装成 NSMenu（不碰 NSApp，判例不起 NSApplication 就能检查装好的项），
// main.swift 的 `installMainMenu(lang:)` 只负责挂到 NSApp、并订阅 `LanguageStore.$lang`
// 在每次切换时重装。表**显式吃 lang、不读 LanguageMirror**：@Published 在 willSet 发值，订阅
// sink 跑到时镜像（也就是 L()）还是旧语言。双语字面量与 L() 同一形式（zh, en 一对），不是
// 第二套 i18n——菜单在 server 连上之前就得在，server-owned 文案目录此刻拿不到。
// 判例 shell/tests/MenuHarness.swift 钉每个标题、键位与动作（含「没有 ⌘F / ⌘1..7 / ⌥⌘S」）
// 与装配结果（target / selector / 修饰键逐项落地）。

enum MenuSpec {
    /// 壳自己处理的动作——每个都是原生 AppDelegate 同名 @objc 方法的壳版，main.swift 逐一映射到
    /// 显式 target = AppDelegate 的 selector。
    enum ShellAction: Equatable, CaseIterable {
        /// 关于 → 看板 `?page=about`（原生 openAboutPage；**不是**系统 About 面板）
        case about
        /// 设置… ⌘, → `?page=settings`（不带 anchor：原生 openSettingsPage 落在设置页顶部；web 目录序是
        /// 显示 / 模型 / 通用，带 `anchor=general` 会滚到第三区）
        case settings
        /// 权限体检… → `?page=permissions`（原生 openPermissionsWindow；权限页文案「之后随时可从菜单
        /// 「权限体检」再打开」自此在壳里为真）
        case permissions
        /// 重新载入 ⌘R（壳独有：已在页面上 reload；还停在 splash / 失败页则重走连接序）
        case reload
        // 聚焦捕获框 ⌘L（`quick_capture`）随捕获框在 v2.0.0 退役（§61.1 tombstone，全文见 tag full）。
    }

    /// 菜单项动作三分：壳动作（显式 target）、AppKit first-responder 链 selector（nil target，随焦点
    /// 走——webview 里的输入框吃 ⌘C/⌘V/⌘Z，窗口吃 ⌘W/⌘M/缩放，NSApp 吃 隐藏/退出）、分隔线。
    enum Action: Equatable {
        case shell(ShellAction)
        case responder(String)
        case separator
    }

    struct Item: Equatable {
        let title: String
        /// NSMenuItem.keyEquivalent：`""` = 无快捷键；大写字母 = 带 ⇧（AppKit 约定，重做 = "Z"）。
        let key: String
        /// 除 ⌘ 之外再带 ⌥——只有「隐藏其他」（⌥⌘H）。
        let option: Bool
        let action: Action

        init(_ title: String, key: String = "", option: Bool = false, action: Action) {
            self.title = title
            self.key = key
            self.option = option
            self.action = action
        }

        static let separator = Item("", action: .separator)
    }

    struct Menu: Equatable {
        /// 顶层标题；app 菜单为 `""`（AppKit 用进程名显示）。
        let title: String
        let items: [Item]
        /// 装成 `NSApp.windowsMenu`（AppKit 自动在里面列出打开的窗口）——只有「窗口」。
        let isWindowsMenu: Bool

        init(_ title: String, items: [Item], isWindowsMenu: Bool = false) {
            self.title = title
            self.items = items
            self.isWindowsMenu = isWindowsMenu
        }
    }

    /// 整张主菜单（App / 文件 / 编辑 / 显示 / 窗口），顺序与原生 installMainMenu 一致。
    /// `lang`：`"en"` → 英文，其余一律中文（L() 同一判定）。`appName` = ShellConfig.displayName。
    ///
    /// 刻意**没有**的：Find（⌘F 不被菜单截胡，落到 WKWebView 再进页面）；⌘1..4 切页（归 web
    /// NavRail，同理）；⌥⌘S 折叠/展开侧栏（tombstone 在 §54.4 2026-09-05 追记 (c)）；
    /// 聚焦捕获框 ⌘L（v2.0.0 退役）。
    static func menus(lang: String, appName: String) -> [Menu] {
        func t(_ zh: String, _ en: String) -> String { lang == "en" ? en : zh }
        return [
            Menu("", items: [
                Item(t("关于 \(appName)", "About \(appName)"), action: .shell(.about)),
                .separator,
                Item(t("设置…", "Settings…"), key: ",", action: .shell(.settings)),
                Item(t("权限体检…", "Permissions Checkup…"), action: .shell(.permissions)),
                .separator,
                Item(t("隐藏 \(appName)", "Hide \(appName)"), key: "h", action: .responder("hide:")),
                Item(t("隐藏其他", "Hide Others"), key: "h", option: true,
                     action: .responder("hideOtherApplications:")),
                Item(t("全部显示", "Show All"), action: .responder("unhideAllApplications:")),
                .separator,
                Item(t("退出", "Quit"), key: "q", action: .responder("terminate:")),
            ]),
            Menu(t("文件", "File"), items: [
                Item(t("关闭窗口", "Close Window"), key: "w", action: .responder("performClose:")),
            ]),
            Menu(t("编辑", "Edit"), items: [
                Item(t("撤销", "Undo"), key: "z", action: .responder("undo:")),
                Item(t("重做", "Redo"), key: "Z", action: .responder("redo:")),
                .separator,
                Item(t("剪切", "Cut"), key: "x", action: .responder("cut:")),
                Item(t("拷贝", "Copy"), key: "c", action: .responder("copy:")),
                Item(t("粘贴", "Paste"), key: "v", action: .responder("paste:")),
                Item(t("全选", "Select All"), key: "a", action: .responder("selectAll:")),
            ]),
            Menu(t("显示", "View"), items: [
                Item(t("重新载入", "Reload"), key: "r", action: .shell(.reload)),
            ]),
            Menu(t("窗口", "Window"), items: [
                Item(t("最小化", "Minimize"), key: "m", action: .responder("performMiniaturize:")),
                Item(t("缩放", "Zoom"), action: .responder("performZoom:")),
            ], isWindowsMenu: true),
        ]
    }

    /// 把表逐项装成 NSMenu（原生 installMainMenu 的装配半边；不碰 NSApp——挂到 `NSApp.mainMenu` /
    /// `NSApp.windowsMenu` 是 main.swift 的事，所以判例不起 NSApplication 就能逐项检查）。
    /// 壳动作 `selector(action)` + 显式 `target`；AppKit 标准动作 `Selector(name)`、nil target 走
    /// first-responder 链；`option` 对两种动作一视同仁（⌥⌘ 不因为是壳动作就悄悄掉成 ⌘）。
    /// 返回主菜单与 `isWindowsMenu` 那一份（没有则 nil）。
    static func build(_ menus: [Menu], target: AnyObject,
                      selector: (ShellAction) -> Selector) -> (main: NSMenu, windows: NSMenu?) {
        let main = NSMenu()
        var windows: NSMenu?
        for spec in menus {
            let top = NSMenuItem()
            main.addItem(top)
            let menu = NSMenu(title: spec.title)
            top.submenu = menu
            for item in spec.items {
                let mi: NSMenuItem
                switch item.action {
                case .separator:
                    mi = .separator()
                case .responder(let name):
                    mi = NSMenuItem(title: item.title, action: Selector(name), keyEquivalent: item.key)
                case .shell(let action):
                    mi = NSMenuItem(title: item.title, action: selector(action), keyEquivalent: item.key)
                    mi.target = target
                }
                if item.option { mi.keyEquivalentModifierMask = [.command, .option] }
                menu.addItem(mi)
            }
            if spec.isWindowsMenu { windows = menu }
        }
        return (main, windows)
    }
}

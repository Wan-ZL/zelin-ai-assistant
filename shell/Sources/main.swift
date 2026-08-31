// main.swift — "Zelin AI Board" 薄壳 app（AppKit + WKWebView，单文件）
//
// 职责刻意做薄：解析 PORT/HOME/SERVER_REPO → 探活 /api/board → 必要时拉起
// `python3 -m server` → 一个 WKWebView 窗口加载 http://127.0.0.1:PORT/。
// 板子本体（React board）活在 web/dist，由 server/ 静态托管；这里没有业务逻辑。
//
// 生命周期诚实原则：只 terminate 我们自己 spawn 的 child server；对一个我们
// 仅仅 attach 上去的既有 server 绝不动手（它可能属于 actd 或另一个 shell）。

import AppKit
import WebKit

// MARK: - ShellConfig（启动期一次性解析，全部只读）

enum ShellConfig {
    /// PORT：env ZAI_PORT → 默认 47820（与 server/app.py 同一默认值）。
    static let port: Int = {
        if let raw = ProcessInfo.processInfo.environment["ZAI_PORT"],
           let p = Int(raw.trimmingCharacters(in: .whitespaces)),
           (1...65535).contains(p) {
            return p
        }
        return 47820
    }()

    /// HOME：env AIASSISTANT_HOME → home.txt pointer（CONTRACT §19，与
    /// mac/Sources/Utils.swift AppPaths.stateRoot 同一解析顺序）→ 本机 repo 兜底。
    static let homeDir: String = {
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
        return "/Volumes/Storage/Server/Projects/zelin-ai-assistant"
    }()

    /// SERVER_REPO（`python3 -m server` 的 cwd）：UserDefaults "serverRepo"
    /// （`defaults write com.zelin.ai-board serverRepo <path>` 可改）→
    /// Info.plist ZAIServerRepo（preview shell 默认指向 v-next worktree）。
    static let serverRepo: String = {
        if let d = UserDefaults.standard.string(forKey: "serverRepo"), !d.isEmpty {
            return (d as NSString).expandingTildeInPath
        }
        if let p = Bundle.main.object(forInfoDictionaryKey: "ZAIServerRepo")
            as? String, !p.isEmpty {
            return (p as NSString).expandingTildeInPath
        }
        return "/Volumes/Storage/Server/Projects/zelin-ai-assistant"
    }()

    static var boardURL: URL { URL(string: "http://127.0.0.1:\(port)/")! }
    static var probeURL: URL { URL(string: "http://127.0.0.1:\(port)/api/board")! }

    static let logDir: String =
        ("~/Library/Logs/zelin-ai-assistant" as NSString).expandingTildeInPath
    static var logPath: String { logDir + "/board-shell.log" }
}

// MARK: - ServerManager（探活 + 拉起 + 诚实退场）

final class ServerManager {
    /// 只在我们亲手 spawn 时非 nil——这是「退出时是否 terminate」的唯一依据。
    private(set) var spawned: Process?

    /// 探活：GET /api/board，1s timeout；任何 2xx 视为 server 在班。
    /// 回调保证回到 main queue（调用方全是 UI 流程）。
    func probe(_ completion: @escaping (Bool) -> Void) {
        var req = URLRequest(url: ShellConfig.probeURL, timeoutInterval: 1.0)
        req.cachePolicy = .reloadIgnoringLocalCacheData
        URLSession.shared.dataTask(with: req) { _, resp, _ in
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            let ok = (200...299).contains(code)
            DispatchQueue.main.async { completion(ok) }
        }.resume()
    }

    /// 拉起 `python3 -m server`：cwd=SERVER_REPO，env 注入 AIASSISTANT_HOME +
    /// ZAI_PORT；child 的 stdout/err 追加进 board-shell.log（唯一排障入口）。
    func spawnServer() {
        let fm = FileManager.default
        try? fm.createDirectory(atPath: ShellConfig.logDir,
                                withIntermediateDirectories: true)
        if !fm.fileExists(atPath: ShellConfig.logPath) {
            fm.createFile(atPath: ShellConfig.logPath, contents: nil)
        }
        let log = FileHandle(forWritingAtPath: ShellConfig.logPath)
        log?.seekToEndOfFile()
        // 启动横幅：每次 spawn 一行时间戳，方便在 append-only log 里切段。
        let banner = "\n==== board-shell spawn \(ISO8601DateFormatter().string(from: Date())) " +
            "port=\(ShellConfig.port) home=\(ShellConfig.homeDir) repo=\(ShellConfig.serverRepo) ====\n"
        log?.write(banner.data(using: .utf8)!)

        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        p.arguments = ["-m", "server"]
        p.currentDirectoryURL = URL(fileURLWithPath: ShellConfig.serverRepo,
                                    isDirectory: true)
        var env = ProcessInfo.processInfo.environment
        env["AIASSISTANT_HOME"] = ShellConfig.homeDir
        env["ZAI_PORT"] = String(ShellConfig.port)
        p.environment = env
        if let log = log {
            p.standardOutput = log
            p.standardError = log
        }
        do {
            try p.run()
            spawned = p
        } catch {
            log?.write("board-shell: failed to spawn python3 -m server: \(error)\n"
                .data(using: .utf8)!)
        }
    }

    /// 退出清理：只杀自己的孩子（SIGTERM）；attach 上去的 server 一根汗毛不碰。
    func stopIfSpawned() {
        guard let p = spawned, p.isRunning else { return }
        p.terminate()
    }
}

// MARK: - 内嵌等待/失败页（避免 blank window；正式页面由 server 托管）

private func splashHTML(_ message: String) -> String {
    // 极简 system-ui 页，跟随 prefers-color-scheme，与 board 的暗色不打架。
    return """
    <!doctype html><meta charset="utf-8">
    <style>
      :root { color-scheme: light dark; }
      body { display:flex; align-items:center; justify-content:center; height:96vh;
             font: 15px/1.6 -apple-system, system-ui; color: #6b7280;
             background: Canvas; margin:0; }
      code { font-size: 12px; }
    </style>
    <body><div style="text-align:center">\(message)</div></body>
    """
}

// MARK: - AppDelegate

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private let server = ServerManager()

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenu()
        buildWindow()
        // 先探活：有人在班就直接 attach，否则 spawn + 最多 10s 轮询。
        server.probe { [weak self] up in
            guard let self = self else { return }
            if up {
                self.loadBoard()
            } else {
                self.server.spawnServer()
                self.pollUntilUp(deadline: Date().addingTimeInterval(10.0))
            }
        }
    }

    func applicationWillTerminate(_ note: Notification) {
        server.stopIfSpawned()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool {
        return true
    }

    // MARK: window / webview

    private func buildWindow() {
        let config = WKWebViewConfiguration()
        webView = WKWebView(frame: .zero, configuration: config)
        webView.allowsMagnification = true
        if #available(macOS 13.3, *) {
            webView.isInspectable = true   // preview shell：允许 Safari Web Inspector
        }
        webView.loadHTMLString(splashHTML("Starting board server\u{2026}"), baseURL: nil)

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1280, height: 820),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false)
        window.title = "Zelin AI Board"
        window.contentMinSize = NSSize(width: 900, height: 600)
        window.tabbingMode = .disallowed
        window.isReleasedWhenClosed = false
        window.contentView = webView
        // frameAutosaveName：记住上次位置/大小；首启（无存档）时居中。
        if !window.setFrameUsingName("ZAIBoardWindow") { window.center() }
        window.setFrameAutosaveName("ZAIBoardWindow")
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func loadBoard() {
        webView.load(URLRequest(url: ShellConfig.boardURL))
        window.makeFirstResponder(webView)   // ⌘F / 键盘事件直达页面
    }

    /// spawn 后每 0.5s 探一次，直到 server 上线或超时（10s）。
    private func pollUntilUp(deadline: Date) {
        server.probe { [weak self] up in
            guard let self = self else { return }
            if up { self.loadBoard(); return }
            if Date() >= deadline { self.showStartFailure(); return }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                self.pollUntilUp(deadline: deadline)
            }
        }
    }

    /// 起不来 = 明说 + 给排障线索，绝不留一扇白窗。
    private func showStartFailure() {
        webView.loadHTMLString(splashHTML(
            "Board server 未能启动。<br>日志：<code>~/Library/Logs/zelin-ai-assistant/board-shell.log</code>"),
            baseURL: nil)
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Board server 未能启动"
        alert.informativeText = """
        10 秒内未能连上 http://127.0.0.1:\(ShellConfig.port)/api/board。

        排障：
        • 日志：~/Library/Logs/zelin-ai-assistant/board-shell.log
        • Server repo：\(ShellConfig.serverRepo)
        • 手动试跑：cd 到 server repo 后执行 ZAI_PORT=\(ShellConfig.port) /usr/bin/python3 -m server
        """
        alert.addButton(withTitle: "好")
        alert.runModal()
    }

    // MARK: menu

    @objc private func reloadPage(_ sender: Any?) {
        // ⌘R：已经在 board 上就 reload；还停在内嵌 splash/失败页则重走探活。
        if let url = webView.url, url.host == "127.0.0.1" {
            webView.reload()
        } else {
            server.probe { [weak self] up in
                guard let self = self else { return }
                if up {
                    self.loadBoard()
                } else if self.server.spawned == nil {
                    self.server.spawnServer()
                    self.pollUntilUp(deadline: Date().addingTimeInterval(10.0))
                } else {
                    self.pollUntilUp(deadline: Date().addingTimeInterval(10.0))
                }
            }
        }
    }

    /// 标准菜单骨架。刻意不放 Find 菜单项——⌘F 不被菜单截胡，落到 WKWebView
    /// 再进页面（board 自己绑定了 Cmd+F 搜索）。
    private func buildMenu() {
        let main = NSMenu()

        // App menu：About / Hide / Quit ⌘Q
        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appItem.submenu = appMenu
        appMenu.addItem(NSMenuItem(
            title: "About Zelin AI Board",
            action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)),
            keyEquivalent: ""))
        appMenu.addItem(.separator())
        appMenu.addItem(NSMenuItem(
            title: "Hide Zelin AI Board",
            action: #selector(NSApplication.hide(_:)), keyEquivalent: "h"))
        appMenu.addItem(.separator())
        appMenu.addItem(NSMenuItem(
            title: "Quit Zelin AI Board",
            action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))

        // File menu：Close ⌘W
        let fileItem = NSMenuItem()
        main.addItem(fileItem)
        let fileMenu = NSMenu(title: "File")
        fileItem.submenu = fileMenu
        fileMenu.addItem(NSMenuItem(
            title: "Close Window",
            action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w"))

        // Edit menu：nil-target 标准编辑链——webview 里的输入框要吃 ⌘C/⌘V/⌘Z。
        let editItem = NSMenuItem()
        main.addItem(editItem)
        let editMenu = NSMenu(title: "Edit")
        editItem.submenu = editMenu
        editMenu.addItem(NSMenuItem(
            title: "Undo", action: Selector(("undo:")), keyEquivalent: "z"))
        editMenu.addItem(NSMenuItem(
            title: "Redo", action: Selector(("redo:")), keyEquivalent: "Z"))
        editMenu.addItem(.separator())
        editMenu.addItem(NSMenuItem(
            title: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x"))
        editMenu.addItem(NSMenuItem(
            title: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c"))
        editMenu.addItem(NSMenuItem(
            title: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v"))
        editMenu.addItem(NSMenuItem(
            title: "Select All",
            action: #selector(NSText.selectAll(_:)), keyEquivalent: "a"))

        // View menu：Reload ⌘R（显式 target 到 delegate，不赌 responder chain）
        let viewItem = NSMenuItem()
        main.addItem(viewItem)
        let viewMenu = NSMenu(title: "View")
        viewItem.submenu = viewMenu
        let reload = NSMenuItem(
            title: "Reload", action: #selector(reloadPage(_:)), keyEquivalent: "r")
        reload.target = self
        viewMenu.addItem(reload)

        // Window menu：标准最小化/前置
        let winItem = NSMenuItem()
        main.addItem(winItem)
        let winMenu = NSMenu(title: "Window")
        winItem.submenu = winMenu
        winMenu.addItem(NSMenuItem(
            title: "Minimize",
            action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m"))
        NSApp.windowsMenu = winMenu

        NSApp.mainMenu = main
    }
}

// MARK: - bootstrap（top-level，单文件 main.swift 允许顶层语句）

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)   // 常规窗口 app（有 Dock 图标；主 app 才是 LSUIElement）

// SIGTERM/SIGINT → 改走 NSApp.terminate：AppKit 只在正规 Quit（⌘Q / Apple
// Event）时触发 applicationWillTerminate，裸 signal 会绕过它、把我们 spawn 的
// child server 泄漏成孤儿。DispatchSource 把 signal 拉回 main queue 的正规退场。
signal(SIGTERM, SIG_IGN)
signal(SIGINT, SIG_IGN)
let sigtermSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
sigtermSource.setEventHandler { NSApp.terminate(nil) }
sigtermSource.resume()
let sigintSource = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
sigintSource.setEventHandler { NSApp.terminate(nil) }
sigintSource.resume()

app.run()

// main.swift — "Zelin's AI Assistant" 壳 app（AppKit + WKWebView）的 bootstrap + AppDelegate
// 壳即产品（owner 2026-09-02）：bundle "Zelin's AI Assistant.app"、显示名同名；
// bundle id 仍是 com.zelin.ai-board（与旧 app 的 com.zelin.ai-engineer 各持各的
// TCC 身份；旧 app 装机版改名 "Zelin's AI Assistant (old)"，CONTRACT §54）。
//
// 职责刻意做薄：解析 PORT/HOME/SERVER_REPO → 探活 /api/board → **连接**
// launchd 托管的 server（com.zelin.aiassistant.server，install.sh 渲染/加载）→
// 一个 WKWebView 窗口加载 http://127.0.0.1:PORT/。板子本体（React board）活在
// web/dist，由 server/ 静态托管；壳里没有看板业务逻辑。壳**必须**原生承载的
// 最小残留（R2.2.3 / CONTRACT §61）：录制引擎的进程归属（screenpipe 是壳的直接
// 子进程——TCC 屏幕录制授权按 GUI 父进程归属）、实时字幕引擎 + 悬浮窗；两者经
// ShellBridge（`zaiShell`）暴露给页面 header 的两个开关。v0.48.x P4 余量（§68.13）：
// §28 通知中继消费（NotifyRelay，5 s tick）、TCC 探针 + 系统设置深链、登录时启动、
// Dock 徽章、全局快速捕获快捷键（ShellSystem.swift）。Dock-only（D3）：无菜单栏
// 图标；关窗不退出（引擎还在跑），点 Dock 图标重开窗口（只看看板窗口，不看
// hasVisibleWindows——字幕悬浮窗会把它顶成 true）；⌘Q 正常退出。窗口三条纯策略
// （外链交系统浏览器 / Dock 重开 / 标题跟随页面）住在 ShellSupport.swift，§54 追记。
//
// server 为什么不再是壳的子进程（2026-09-02 live 事故）：GUI app 是它 spawn 的
// 每个子进程的 TCC responsible process，而壳 bundle 没有任何磁盘授权（ad-hoc
// 签名，授权也不会跟着 build 走）——repo 在外置卷上时子进程读不到 checkout，
// 以 "No module named server" 死掉。launchd 用的是 §55 探针验过的守护解释器。
// 壳保留 spawn 兜底，但**只在探活失败且 launchd 没加载该 label 时**才 spawn——
// 两个 server 绝不能抢同一个端口（launchd 那份会 crash-loop，doctor 报 FAIL）。
//
// 生命周期诚实原则：只 terminate 我们自己 spawn 的 child server；对一个我们
// 仅仅 attach 上去的既有 server 绝不动手（它属于 launchd 或另一个 shell）。

import AppKit
import WebKit

// MARK: - ShellConfig（启动期一次性解析，全部只读）

enum ShellConfig {
    /// launchd label of the resident board server（install.sh 渲染
    /// act/launchd/com.zelin.aiassistant.server.plist；§54 / §55）。
    static let serverLabel = "com.zelin.aiassistant.server"

    /// 用户看到的名字（Dock / 窗口标题 / app 菜单）：Info.plist
    /// CFBundleDisplayName；未打包运行时回落到产品名。
    static let displayName: String = {
        if let n = Bundle.main.object(forInfoDictionaryKey: "CFBundleDisplayName")
            as? String, !n.isEmpty {
            return n
        }
        return "Zelin's AI Assistant"
    }()

    /// PORT：env ZAI_PORT → defaults serverPort → Info.plist ZAIServerPort
    /// （shell/build.sh 以 config.yaml server.port 盖章）→ 默认 47820（与
    /// server/app.py 同一默认值）。每一层都只收 1..65535 的整数。
    static let port: Int = {
        func valid(_ raw: String?) -> Int? {
            guard let raw = raw,
                  let p = Int(raw.trimmingCharacters(in: .whitespaces)),
                  (1...65535).contains(p) else { return nil }
            return p
        }
        if let p = valid(ProcessInfo.processInfo.environment["ZAI_PORT"]) { return p }
        if let p = valid(UserDefaults.standard.string(forKey: "serverPort")) { return p }
        if let p = valid(Bundle.main.object(forInfoDictionaryKey: "ZAIServerPort")
                         as? String) { return p }
        return 47820
    }()

    /// HOME：env AIASSISTANT_HOME → home.txt pointer（CONTRACT §19，与
    /// mac/Sources/Utils.swift AppPaths.stateRoot 同一解析顺序）。两者都缺 =
    /// nil：spawn 时不注入 env，由 server 侧 canonical 默认值接手
    /// （server/paths.py DEFAULT_HOME）——绝不在壳里猜一台机器的路径。
    static let homeDir: String? = {
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
        return nil
    }()

    /// SERVER_REPO（`python3 -m server` 的 cwd）：UserDefaults "serverRepo"
    /// （`defaults write com.zelin.ai-board serverRepo <path>` 可改）→
    /// Info.plist ZAIServerRepo（shell/build.sh 构建时以实际 repo root 盖章，
    /// 同版本号机制）。两者都缺 = nil：需要 spawn 时以礼貌弹窗收场（附日志
    /// 与修复方式），绝不猜路径起 server。
    static let serverRepo: String? = {
        if let d = UserDefaults.standard.string(forKey: "serverRepo"), !d.isEmpty {
            return (d as NSString).expandingTildeInPath
        }
        if let p = Bundle.main.object(forInfoDictionaryKey: "ZAIServerRepo")
            as? String, !p.isEmpty {
            return (p as NSString).expandingTildeInPath
        }
        return nil
    }()

    static var boardURL: URL { URL(string: "http://127.0.0.1:\(port)/")! }
    static var probeURL: URL { URL(string: "http://127.0.0.1:\(port)/api/board")! }
    /// 设置页深链（web route.ts `?page=settings`；anchor 由页面自己滚动）。
    static func settingsURL(anchor: String) -> URL {
        var c = URLComponents(url: boardURL, resolvingAgainstBaseURL: false)!
        c.queryItems = [URLQueryItem(name: "page", value: "settings"),
                        URLQueryItem(name: "anchor", value: anchor)]
        return c.url ?? boardURL
    }

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

    /// 打开（必要时创建）append-only 排障日志，游标已在文件尾。
    private func openLog() -> FileHandle? {
        let fm = FileManager.default
        try? fm.createDirectory(atPath: ShellConfig.logDir,
                                withIntermediateDirectories: true)
        if !fm.fileExists(atPath: ShellConfig.logPath) {
            fm.createFile(atPath: ShellConfig.logPath, contents: nil)
        }
        let log = FileHandle(forWritingAtPath: ShellConfig.logPath)
        log?.seekToEndOfFile()
        return log
    }

    /// 落一行取证（弹窗被关掉之后 log 是唯一入口）。
    func logLine(_ line: String) {
        let log = openLog()
        log?.write((line + "\n").data(using: .utf8)!)
        try? log?.close()
    }

    /// launchd 是否已加载 com.zelin.aiassistant.server（`launchctl print
    /// gui/<uid>/<label>` 退出 0 = 已加载；进程此刻可能正在 KeepAlive 节流窗口
    /// 里重启，所以「已加载」≠「在班」——那是 probe 的事）。同步调用，亚秒级。
    /// 出错（launchctl 缺失等）按「未加载」处理：宁可多一次兜底 spawn。
    func launchdHosted() -> Bool {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        p.arguments = ["print", "gui/\(getuid())/\(ShellConfig.serverLabel)"]
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        do {
            try p.run()
        } catch {
            return false
        }
        p.waitUntilExit()
        return p.terminationStatus == 0
    }

    /// spawn 兜底用的解释器（§19/§55）：repo 的 config/runtime.json `python`
    /// （install.sh 验过两道闸门的那个）；读不到 / 不是绝对路径 / 不可执行时
    /// 回落 /usr/bin/python3（Apple 随系统发的、最可能带着用户授权的 binary）。
    func interpreter(repo: String) -> String {
        let fallback = "/usr/bin/python3"
        let url = URL(fileURLWithPath: repo).appendingPathComponent("config/runtime.json")
        guard let data = try? Data(contentsOf: url),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let py = obj["python"] as? String,
              py.hasPrefix("/"),
              FileManager.default.isExecutableFile(atPath: py) else {
            return fallback
        }
        return py
    }

    /// 兜底拉起 `<python> -m server`：cwd=repo（调用方已解析），env 注入
    /// AIASSISTANT_HOME（解析到了才注入）+ ZAI_PORT；child 的 stdout/err
    /// 追加进 board-shell.log（唯一排障入口）。调用方保证此时 launchd 没有
    /// 加载该 label（§54：两个 server 绝不抢端口）。
    func spawnServer(repo: String) {
        let log = openLog()
        let python = interpreter(repo: repo)
        // 启动横幅：每次 spawn 一行时间戳，方便在 append-only log 里切段。
        let banner = "\n==== board-shell spawn (fallback: \(ShellConfig.serverLabel) not loaded) " +
            "\(ISO8601DateFormatter().string(from: Date())) " +
            "port=\(ShellConfig.port) home=\(ShellConfig.homeDir ?? "(server default)") " +
            "repo=\(repo) python=\(python) ====\n"
        log?.write(banner.data(using: .utf8)!)

        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = ["-m", "server"]
        p.currentDirectoryURL = URL(fileURLWithPath: repo, isDirectory: true)
        var env = ProcessInfo.processInfo.environment
        if let home = ShellConfig.homeDir {
            env["AIASSISTANT_HOME"] = home
        }
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
            log?.write("board-shell: failed to spawn \(python) -m server: \(error)\n"
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

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private let server = ServerManager()
    /// 页面 ⇄ 壳 桥（§61.1）；与 webView 同寿命。
    private let bridge = ShellBridge()
    /// 5 s 引擎巡检（镜像 mac AppDelegate.refresh 的录制半边：TCC 自愈 + pgrep 活性）。
    private var engineTick: Timer?
    /// `webView.title` → `window.title` 的 KVO 句柄（§54 追记：标题跟随页面）。
    private var titleObservation: NSKeyValueObservation?

    func applicationDidFinishLaunching(_ note: Notification) {
        // 一次性把原生 app 的录制/字幕偏好接过来（同一位 owner 的既有 consent，§61.4）
        let seeded = LegacyPrefs.seedFromNativeAppIfNeeded()
        if !seeded.isEmpty {
            server.logLine("board-shell: seeded prefs from \(LegacyPrefs.nativeSuite): "
                + seeded.joined(separator: ","))
        }
        _ = LanguageStore.shared   // L() 镜像就位（悬浮窗/通知文案）
        ShellNavigation.openSettings = { [weak self] anchor in
            self?.openSettingsPage(anchor: anchor)
        }
        ShellWindow.show = { [weak self] in self?.showWindow() }
        buildMenu()
        buildWindow()
        connectOrSpawn()
        startEngines()
        startNativeResidue()
    }

    /// §65.13 其余原生残留：通知中继（§28 唯一 native 通道，点击 = 前置窗口）、TCC 探针初读、
    /// 全局快速捕获快捷键（⌃⌥Space → 前置窗口 + 向页面推 quick_capture）。
    private func startNativeResidue() {
        NotifyRelayDelegate.install()
        PermissionsProbe.shared.refresh()
        QuickCaptureHotkey.shared.onFire = { [weak self] in
            guard let self else { return }
            self.showWindow()
            if self.webView.url?.host == "127.0.0.1" {
                self.bridge.pushCommand("quick_capture")
            } else {
                self.loadBoard()
            }
        }
        QuickCaptureHotkey.shared.register()
    }

    /// §54 生命周期：先探活——有人在班就直接 attach；没有则看 launchd 有没有
    /// 加载 server label：**已加载 = 不 spawn**（它在 KeepAlive 重启，等它；两
    /// 个 server 抢端口只会让 launchd 那份 crash-loop），只轮询 ≤10s；**未加载**
    /// 才走 spawn 兜底（SERVER_REPO 解析不到 = 礼貌报错，绝不猜本机路径）。
    private func connectOrSpawn() {
        server.probe { [weak self] up in
            guard let self = self else { return }
            if up {
                self.loadBoard()
                return
            }
            if self.server.launchdHosted() {
                self.server.logLine("board-shell: no answer on 127.0.0.1:\(ShellConfig.port) "
                    + "but \(ShellConfig.serverLabel) is loaded in launchd — waiting, not spawning")
                self.pollUntilUp(deadline: Date().addingTimeInterval(10.0))
            } else if self.server.spawned != nil {
                self.pollUntilUp(deadline: Date().addingTimeInterval(10.0))
            } else if let repo = ShellConfig.serverRepo {
                self.server.spawnServer(repo: repo)
                self.pollUntilUp(deadline: Date().addingTimeInterval(10.0))
            } else {
                self.showConfigFailure()
            }
        }
    }

    /// 引擎落户壳后的启动序列（逐字对应 mac AppDelegate 的同名调用；P0-11：
    /// 无 recordingMode = 尚未 consent = off，autostart 自然不动）。
    private func startEngines() {
        RecordingController.shared.autostartIfNeeded()
        LiveCaptionsController.shared.restoreOnLaunch()
        let timer = Timer(timeInterval: 5.0, repeats: true) { _ in
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    RecordingController.shared.pollScreenPermission()
                    RecordingController.shared.refreshEngineState()
                    NotifyRelay.drain()   // §28：5 s 节拍消费 state/notify_queue（原生 refresh tick 同款）
                }
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        engineTick = timer
    }

    func applicationWillTerminate(_ note: Notification) {
        engineTick?.invalidate()
        QuickCaptureHotkey.shared.unregister()
        server.stopIfSpawned()
    }

    /// D3 / R2.2.1：关窗不退出——screenpipe 是本进程的子进程、字幕悬浮窗住在本
    /// 进程，窗口关了它们还得活着。⌘Q 才是退出。
    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool {
        return false
    }

    /// 点 Dock 图标重开窗口（无菜单栏图标，这是唯一的重开入口）。刻意**不看**
    /// `flag`（hasVisibleWindows）：字幕悬浮 NSPanel 在场时它恒为 true，Dock 点击
    /// 就成了空操作（原生 AppDelegate.swift 同一处的教训）——同一个原因，最小化的
    /// 看板也得壳自己还原：AppKit 的默认重开只在 flag == false 时 deminiaturize。
    /// 只看看板窗口自己（ReopenPolicy，判例 shell/tests/PolicyHarness.swift）。
    func applicationShouldHandleReopen(_ sender: NSApplication,
                                       hasVisibleWindows flag: Bool) -> Bool {
        switch ReopenPolicy.action(boardVisible: window.isVisible,
                                   boardMiniaturized: window.isMiniaturized) {
        case .show:
            showWindow()
        case .deminiaturize:
            window.deminiaturize(nil)
            NSApp.activate(ignoringOtherApps: true)
        case .none:
            break
        }
        return true
    }

    private func showWindow() {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    /// 字幕悬浮窗齿轮 → 看板设置页（`?page=settings&anchor=<anchor>`）。
    private func openSettingsPage(anchor: String) {
        showWindow()
        webView.load(URLRequest(url: ShellConfig.settingsURL(anchor: anchor)))
    }

    // MARK: window / webview

    private func buildWindow() {
        let config = WKWebViewConfiguration()
        bridge.install(into: config)   // 必须在 WKWebView 创建前注册 handler
        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self          // target=_blank / window.open → 外链分流（§54 追记）
        webView.allowsMagnification = true
        if #available(macOS 13.3, *) {
            webView.isInspectable = true   // preview shell：允许 Safari Web Inspector
        }
        bridge.attach(to: webView)
        webView.loadHTMLString(splashHTML(
            L("正在启动 board server\u{2026}", "Starting board server\u{2026}")), baseURL: nil)

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1280, height: 820),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false)
        window.title = ShellConfig.displayName
        // 窗口下限镜像原生 MainWindow（分屏 / 小屏要能缩；看板泳道本来就横向滚动）：
        // truth = ui/tokens/native-tokens.json layout.window.min_width / min_height
        window.contentMinSize = NSSize(width: 720, height: 480)
        window.tabbingMode = .disallowed
        window.isReleasedWhenClosed = false
        window.contentView = webView
        // frameAutosaveName：记住上次位置/大小；首启（无存档）时居中。
        if !window.setFrameUsingName("ZAIBoardWindow") { window.center() }
        window.setFrameAutosaveName("ZAIBoardWindow")
        // 标题跟随页面（原生 MainWindow.installTitleSink 的壳半边）：WKWebView.title
        // 是 KVO-compliant 的；页面每次换 document.title（切页 / 换语言）都到这里，
        // 空标题（内嵌 splash）回落产品名。web 半（每页各自的 document.title）另批。
        titleObservation = webView.observe(\.title, options: [.initial, .new]) { [weak self] wv, _ in
            guard let self else { return }
            self.window.title = WindowTitlePolicy.resolve(pageTitle: wv.title,
                                                          fallback: ShellConfig.displayName)
        }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    // MARK: 外链（§54 追记：一律交系统浏览器；原生 DepAction.url / FailureCatalog.perform 同款）

    /// 按 ExternalLinkPolicy 执行副作用：看板 SPA（origin + 路径 `/`）留在本 webView，
    /// 其余 http(s) / mailto 交系统处理者（含同 origin 的 `/files/…` 交付物——壳没有
    /// 后退，永不把唯一的 webView 导航到看板之外），别的 scheme 什么都不做。
    private func route(_ url: URL?) {
        switch ExternalLinkPolicy.classify(url, port: ShellConfig.port) {
        case .board:
            if let url { webView.load(URLRequest(url: url)) }
        case .external:
            if let url { NSWorkspace.shared.open(url) }
        case .ignore:
            break
        }
    }

    /// target=_blank / window.open：不实现时 WebKit 直接取消该导航（页面上每一处
    /// target="_blank" / window.open 全成空操作）。壳永远只有一个 webView——这里分流后
    /// 返回 nil，绝不开第二个窗口。
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction,
                 windowFeatures: WKWindowFeatures) -> WKWebView? {
        route(navigationAction.request.url)
        return nil
    }

    /// 同 frame 的普通 `<a href>` / location 跳转：主 frame 要离开看板 SPA（别的 http(s)
    /// 主机，或同 origin 的 `/files/…` / `/api/…` 路径）→ 取消 + 交系统浏览器（看板永不
    /// 被导航走）。子 frame 与「新窗口」请求（targetFrame == nil，随后进
    /// createWebViewWith）一律放行。
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let target = navigationAction.targetFrame, target.isMainFrame,
              ExternalLinkPolicy.classify(navigationAction.request.url,
                                          port: ShellConfig.port) == .external
        else {
            decisionHandler(.allow)
            return
        }
        route(navigationAction.request.url)
        decisionHandler(.cancel)
    }

    private func loadBoard() {
        webView.load(URLRequest(url: ShellConfig.boardURL))
        window.makeFirstResponder(webView)   // ⌘F / 键盘事件直达页面
    }

    /// 页面加载完成 → 推一份当前快照（页面 mount 时也会自己 getState；这一推
    /// 让 reload / 深链切页后的 header 无需等下一次状态变化）。
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        bridge.pushState()
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

    /// 起不来 = 明说 + 给排障线索，绝不留一扇白窗。第一条永远是 launchd 的
    /// 修法（§54：server 由 launchd 托管，壳只是连接方）。文案随界面语言
    /// L(zh, en)（原生 AppDelegate / Pages 的每个 NSAlert 同款）；命令、label、
    /// 路径逐字不译。
    private func showStartFailure() {
        let hosted = server.launchdHosted()
        let kickstart = "launchctl kickstart -k gui/\(getuid())/\(ShellConfig.serverLabel)"
        let serverLog = "~/Library/Logs/zelin-ai-assistant/server.launchd.log"
        let shellLog = "~/Library/Logs/zelin-ai-assistant/board-shell.log"
        webView.loadHTMLString(splashHTML(
            L("Board server 未能连上。<br>server 由 launchd 托管：<code>\(kickstart)</code>"
              + "<br>日志：<code>\(serverLog)</code>",
              "Could not reach the board server.<br>The server is managed by launchd: <code>\(kickstart)</code>"
              + "<br>Log: <code>\(serverLog)</code>")),
            baseURL: nil)
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = L("Board server 未能连上", "Could not reach the board server")
        let spawnLine = server.spawned == nil
            ? L("• 壳未 spawn 兜底（launchd 已加载该 label，避免两个 server 抢端口）",
                "• The shell did not spawn a fallback (launchd has this label loaded; two servers must not fight over the port)")
            : L("• 壳已 spawn 兜底 child（launchd 未加载该 label），它也没在 10 秒内答话——看 board-shell.log",
                "• The shell spawned a fallback child (label not loaded in launchd) and it did not answer within 10 s either — see board-shell.log")
        let labelState = hosted
            ? L("已加载", "loaded")
            : L("未加载——先 bash install.sh 渲染并加载它", "not loaded — run bash install.sh first to render and load it")
        alert.informativeText = L("""
        10 秒内未能连上 http://127.0.0.1:\(ShellConfig.port)/api/board。

        • server 由 launchd 托管：\(kickstart)
          （label \(labelState)）
        • server 日志：\(serverLog)
        • 壳日志：\(shellLog)
        \(spawnLine)
        • Server repo：\(ShellConfig.serverRepo ?? "(未配置)")
        • 手动试跑：cd 到 server repo 后执行 ZAI_PORT=\(ShellConfig.port) <config/runtime.json 的 python> -m server
        """, """
        No answer from http://127.0.0.1:\(ShellConfig.port)/api/board within 10 s.

        • The server is managed by launchd: \(kickstart)
          (label \(labelState))
        • Server log: \(serverLog)
        • Shell log: \(shellLog)
        \(spawnLine)
        • Server repo: \(ShellConfig.serverRepo ?? "(not configured)")
        • Manual run: cd into the server repo, then ZAI_PORT=\(ShellConfig.port) <python from config/runtime.json> -m server
        """)
        alert.addButton(withTitle: L("好", "OK"))
        alert.runModal()
    }

    /// SERVER_REPO 解析不到 = 明说怎么修（弹窗 + log 各一份），绝不猜路径。
    private func showConfigFailure() {
        server.logLine("board-shell: no running server on 127.0.0.1:\(ShellConfig.port) "
            + "and no server repo configured (defaults serverRepo / Info.plist ZAIServerRepo both empty) — not spawning.")
        let shellLog = "~/Library/Logs/zelin-ai-assistant/board-shell.log"
        let kickstart = "launchctl kickstart -k gui/\(getuid())/\(ShellConfig.serverLabel)"
        webView.loadHTMLString(splashHTML(
            L("找不到 board server 的 repo 路径。<br>日志：<code>\(shellLog)</code>",
              "Cannot find the board server's repo path.<br>Log: <code>\(shellLog)</code>")),
            baseURL: nil)
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = L("找不到 board server 的 repo", "Board server repo not found")
        alert.informativeText = L("""
        127.0.0.1:\(ShellConfig.port) 上没有在班的 server，launchd 也没有加载 \(ShellConfig.serverLabel)，而本壳不知道去哪里拉起兜底的 python3 -m server。

        修复（任选其一）：
        • server 由 launchd 托管：bash install.sh（渲染并加载 \(ShellConfig.serverLabel)），之后 \(kickstart)
        • defaults write com.zelin.ai-board serverRepo <repo 路径>
        • 重新运行 bash install.sh / shell/build.sh（构建时会把 repo 路径盖进 app）

        日志：\(shellLog)
        """, """
        No server is answering on 127.0.0.1:\(ShellConfig.port), launchd has not loaded \(ShellConfig.serverLabel), and this shell does not know where to start the fallback python3 -m server.

        Fix (any one of these):
        • Let launchd manage the server: bash install.sh (renders and loads \(ShellConfig.serverLabel)), then \(kickstart)
        • defaults write com.zelin.ai-board serverRepo <repo path>
        • Re-run bash install.sh / shell/build.sh (the build stamps the repo path into the app)

        Log: \(shellLog)
        """)
        alert.addButton(withTitle: L("好", "OK"))
        alert.runModal()
    }

    // MARK: menu

    @objc private func reloadPage(_ sender: Any?) {
        // ⌘R：已经在 board 上就 reload；还停在内嵌 splash/失败页则重走同一套
        // 探活 → launchd 门 → 兜底 spawn 流程（已有自己的 child 时只等它）。
        if let url = webView.url, url.host == "127.0.0.1" {
            webView.reload()
        } else {
            connectOrSpawn()
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
            title: "About \(ShellConfig.displayName)",
            action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)),
            keyEquivalent: ""))
        appMenu.addItem(.separator())
        appMenu.addItem(NSMenuItem(
            title: "Hide \(ShellConfig.displayName)",
            action: #selector(NSApplication.hide(_:)), keyEquivalent: "h"))
        appMenu.addItem(.separator())
        appMenu.addItem(NSMenuItem(
            title: "Quit \(ShellConfig.displayName)",
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

// EngineOwnership.swift — 引擎归属：启动时 adopt、退出时只停自己派生的那台（CONTRACT §61.8；
// §15 录制三态 2026-09-14 追记；§79 升级的壳半边）。
//
// lite 2.0 的规矩是「升级与重启永不打断录制」。1.x 的做法（issue #318）是启动时把一切活着的
// 引擎当孤儿 pkill 掉、再由 autostart 重新拉起——那一下就是录制断档，而且新引擎拿的是新壳的 TCC
// 身份，旧引擎（例如从终端起的）手里的授权一起丢了。2.0 反过来：
//
//   启动：活着的引擎 argv 带 recipe 标记（`screenpipe@0.3.349 record` + `--ignored-windows`，
//         即 Recording.swift ScreenpipeRecipe.startCommand 产出的那条命令）→ **adopt**：记下 pid
//         写进 pidfile，绝不 kill；autostart 的 pgrep 看见它就不再 spawn 第二台。argv 不带标记的
//         引擎（别的版本 / 别的参数 / owner 手工起的）→ 只记一行日志，不接管、也不 kill。
//   退出：只停**我们 spawn 的、此刻还活着的**那一台（凭据是活性不是历史：`Recording.swift` 从不把
//         `engineProcess` 置回 nil）；并且升级握手 `engineHandoff` 偏好键在场时一台都不停——升级
//         脚本在 relaunch 前写它，新壳 adopt 之后清它。用户在页面上显式「关闭录制」走 Recording.swift
//         的 setMode("off") → pkill，照旧生效。
//   pidfile：`~/Library/Application Support/ZelinAIAssistant/engine.pid`，一行 `<pid> spawned|adopted`；
//         引擎没了就删。它只是取证（doctor / 排障），归属判决不读它——判决只信 argv 与活性。
//
// 为什么长在这里而不是 Recording.swift：那份文件是录制引擎的 source of truth、字节不动（§61.3，
// tests/test_shell_engine_mirror.py 钉它的哈希）。本模块站在它外面，只借三样公开的东西——
// `enginePattern`（§15 契约停法的那个模式，逐字复用）、`engineProcess`（我们 spawn 的那台的
// 句柄）、`isEngineRunning()` 的同一个 pgrep——全部经缝注入，harness 绝不真 pgrep / pkill。
//
// 「detached」的含义：引擎的寿命不绑在壳进程上。壳崩溃 / SIGKILL / 升级 relaunch 时引擎照录
// （ppid 变 1），下一个壳 adopt 它；只有用户手动退出、且没有升级握手时，退出路径才停它。
// 判例：shell/tests/LaunchHarness.swift [8]（纯判决）/ [9]（缝上的调用序列）、
// tests/test_shell_engine_orphan.py（源码层的结构钉）。

import Foundation

enum EngineOwnership {

    // MARK: - 纯判决（无 I/O，harness 直接钉）

    /// pgrep 列出的一台引擎进程：pid + 整条命令行。
    struct EngineProcess: Equatable {
        let pid: Int32
        let command: String
    }

    /// 启动那一刻对「现在跑着的引擎归谁」的判决。
    enum LaunchVerdict: Equatable {
        /// 没有引擎在跑——autostart 照常。
        case idle
        /// argv 带 recipe 标记：adopt，记 pid，永不由本壳的退出路径停它。
        case adopt(pid: Int32)
        /// 有引擎、但 argv 不带 recipe 标记：只记日志，不接管、不 kill。
        case foreign
    }

    /// recipe 标记：两段都在同一条命令行里才算本 app 的引擎（Recording.swift startCommand 的产物；
    /// npx 的 `npm exec screenpipe@0.3.349 record … --ignored-windows …` 那一层带全两段）。
    static let recipeMarkers = ["screenpipe@0.3.349 record", "--ignored-windows"]

    static func isRecipeEngine(_ command: String) -> Bool {
        recipeMarkers.allSatisfy { command.contains($0) }
    }

    /// 启动判决：没有引擎 → idle；任一台带 recipe 标记 → adopt（取第一台带标记的 pid）；否则 foreign。
    static func decideAtLaunch(engines: [EngineProcess]) -> LaunchVerdict {
        guard !engines.isEmpty else { return .idle }
        if let ours = engines.first(where: { isRecipeEngine($0.command) }) {
            return .adopt(pid: ours.pid)
        }
        return .foreign
    }

    /// `pgrep -lf <pattern>` 的输出 → 进程表（每行 `<pid> <command…>`；坏行跳过）。
    static func parseListing(_ output: String) -> [EngineProcess] {
        output.split(separator: "\n").compactMap { raw in
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard let space = line.firstIndex(of: " "),
                  let pid = Int32(line[..<space]) else { return nil }
            let command = line[line.index(after: space)...].trimmingCharacters(in: .whitespaces)
            return command.isEmpty ? nil : EngineProcess(pid: pid, command: command)
        }
    }

    /// 退出判决：只有**我们 spawn 的那台此刻还活着**且**没有升级握手**才在退出路径上停它。
    static func stopAtExit(spawnedAlive: Bool, handoffPending: Bool) -> Bool {
        spawnedAlive && !handoffPending
    }

    /// pidfile 的内容（nil = 删文件）：我们 spawn 的、活着的那台优先；否则 adopt 的那台（引擎还活着时）。
    static func pidfileBody(spawnedPid: Int32?, adoptedPid: Int32?, engineAlive: Bool) -> String? {
        if let pid = spawnedPid { return "\(pid) spawned\n" }
        if engineAlive, let pid = adoptedPid { return "\(pid) adopted\n" }
        return nil
    }

    // MARK: - 常量

    /// 升级握手的偏好键（UserDefaults，域 com.zelin.ai-board）。§79 的升级脚本在 relaunch 前
    /// `defaults write com.zelin.ai-board engineHandoff -string <ISO 时间>`；新壳启动判定完即删。
    static let handoffKey = "engineHandoff"
    /// pidfile 的位置（Application Support，与 home.txt 同一目录；repo 树之外）。
    static let pidfilePath: String =
        ("~/Library/Application Support/ZelinAIAssistant/engine.pid" as NSString).expandingTildeInPath
    /// 退出路径的预算：macOS 给 `applicationWillTerminate` 大约 5 s，本条最多花这么多。
    static let exitBudget: TimeInterval = 1.5
    /// 轮询粒度。
    static let pollStep: TimeInterval = 0.1

    // MARK: - 缝（默认直连系统；harness 注入，绝不真 pgrep / pkill）

    /// 进程表：`pgrep -lf <enginePattern>`，整条输出（Shell.run 只留 400 字的尾巴，argv 比它长）。
    nonisolated(unsafe) static var listEngines: () -> [EngineProcess] = {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/pgrep")
        p.arguments = ["-lf", RecordingController.enginePattern]
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = FileHandle.nullDevice
        do { try p.run() } catch { return [] }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return parseListing(String(data: data, encoding: .utf8) ?? "")
    }
    nonisolated(unsafe) static var engineRunning: () -> Bool = {
        RecordingController.isEngineRunning()
    }
    /// 「我们那台引擎此刻还活着吗」。**活性，不是历史**：`Recording.swift` 只在 spawn 时
    /// 赋一次 `engineProcess`、从不置回 nil，`!= nil` 因此是终身通行证（判例 [9](h) 直接
    /// 钉这个默认实现，所以它有名字——缝被注入过之后也够得着）。
    nonisolated(unsafe) static let defaultEngineSpawnedByUs: () -> Bool = {
        RecordingController.engineProcess?.isRunning == true
    }
    nonisolated(unsafe) static var engineSpawnedByUs: () -> Bool = EngineOwnership.defaultEngineSpawnedByUs
    /// 我们 spawn 的那台的 pid（活着才有）。
    nonisolated(unsafe) static var spawnedPid: () -> Int32? = {
        guard let p = RecordingController.engineProcess, p.isRunning else { return nil }
        return p.processIdentifier
    }
    nonisolated(unsafe) static var handoffPending: () -> Bool = {
        UserDefaults.standard.object(forKey: handoffKey) != nil
    }
    nonisolated(unsafe) static var clearHandoff: () -> Void = {
        UserDefaults.standard.removeObject(forKey: handoffKey)
    }
    /// `signal` ∈ `"-TERM"` | `"-KILL"`；模式逐字借录制引擎的 `enginePattern`
    /// （§15 契约停法 `pkill -f '<engine>'`——`[r]` 字符类让它不匹配自己的 argv）。
    nonisolated(unsafe) static var killEngine: (String) -> Void = { signal in
        _ = Shell.run("/usr/bin/pkill", [signal, "-f", RecordingController.enginePattern])
    }
    /// nil = 删 pidfile；否则整份覆写（先建 Application Support 目录）。
    nonisolated(unsafe) static var writePidfile: (String?) -> Void = { body in
        let fm = FileManager.default
        guard let body else {
            try? fm.removeItem(atPath: pidfilePath)
            return
        }
        let dir = (pidfilePath as NSString).deletingLastPathComponent
        try? fm.createDirectory(atPath: dir, withIntermediateDirectories: true)
        try? body.write(toFile: pidfilePath, atomically: true, encoding: .utf8)
    }
    /// 面包屑落 `~/.screenpipe/engine.log`，与 autostart / 日程同一条格式；`[app` 前缀让
    /// `engineLogTail` 把它滤掉（诊断只读真引擎的话）。
    nonisolated(unsafe) static var logLine: (String) -> Void = { line in
        _ = Shell.ok("echo \"[app $(date '+%F %T')] \(line)\" >> \"$HOME/.screenpipe/engine.log\"")
    }
    nonisolated(unsafe) static var pause: (TimeInterval) -> Void = { Thread.sleep(forTimeInterval: $0) }

    // MARK: - 状态

    /// 本壳 adopt 的那台（启动时判定；引擎没了在 syncPidfile 里清掉）。
    nonisolated(unsafe) static var adoptedPid: Int32?
    /// 上一次写进 pidfile 的内容（避免 5 s tick 每拍都写盘）；"" = 还没写过。
    nonisolated(unsafe) private static var lastPidfileBody: String? = ""

    // MARK: - 效果（阻塞——只在后台队列 / willTerminate 里跑）

    /// 启动判定。返回实际执行的判决（main.swift 只在它完成之后才放行日程执法、autostart
    /// 与 5 s tick）。三条路**都不 kill**；握手键在场时判定完即清（它只管一次 relaunch）。
    @discardableResult
    static func adoptAtLaunch() -> LaunchVerdict {
        let verdict = decideAtLaunch(engines: listEngines())
        let handoff = handoffPending()
        switch verdict {
        case .idle:
            if handoff { logLine("engine handoff: no engine running to adopt") }
        case .adopt(let pid):
            adoptedPid = pid
            logLine("adopt engine pid=\(pid): recipe markers present — this shell never stops it on quit")
            recordPidfile(pidfileBody(spawnedPid: nil, adoptedPid: pid, engineAlive: true))
        case .foreign:
            logLine("engine running without the recipe markers — left alone (not adopted, not stopped)")
        }
        if handoff { clearHandoff() }
        return verdict
    }

    /// 5 s tick 的串行后台队列：pgrep 阻塞，不占主线程；串行保证两拍不交叠写 pidfile。
    static let tickQueue = DispatchQueue(label: "com.zelin.ai-board.engine-ownership", qos: .utility)

    /// 5 s tick 的入口（在 `tickQueue` 上跑）：**现读**活性再 sync。不吃 RecordingController 缓存的
    /// `engineRunning`——窗外启动时 autostart 被跳过，第一拍之前没人刷新过它，读到的是初值 false，
    /// 会把刚 adopt 的那台的 pidfile 删掉、再也写不回来（判例 [9](g4)）。
    static func tickPidfile() {
        syncPidfile(engineAlive: engineRunning())
    }

    /// 按给定活性重算 pidfile（spawn 的优先），内容变了才写。
    static func syncPidfile(engineAlive: Bool) {
        if !engineAlive { adoptedPid = nil }
        recordPidfile(pidfileBody(spawnedPid: spawnedPid(), adoptedPid: adoptedPid, engineAlive: engineAlive))
    }

    /// 退出路径：`pkill -TERM` → 等 ≤ `exitBudget` → 还在就 `pkill -KILL`。同步、有界。
    static func stopAtExit() {
        tickQueue.sync {}   // 等在途的那一拍写完，免得它在下面删掉 pidfile 之后再写回去
        guard engineSpawnedByUs() else { return }
        let handoff = handoffPending()
        guard stopAtExit(spawnedAlive: true, handoffPending: handoff) else {
            logLine("stop engine on quit skipped: \(handoffKey) is set — the next shell adopts it")
            return
        }
        logLine("stop engine on quit: this shell spawned it and it is still alive")
        killEngine("-TERM")
        if !waitUntilGone(exitBudget) { killEngine("-KILL") }
        recordPidfile(nil)
    }

    private static func recordPidfile(_ body: String?) {
        guard body != lastPidfileBody else { return }
        lastPidfileBody = body
        writePidfile(body)
    }

    /// 测试用：忘掉上一次写过的 pidfile 内容与 adopt 的 pid（harness 每个场景前调）。
    static func resetState() {
        adoptedPid = nil
        lastPidfileBody = ""
    }

    /// 轮询到引擎消失或预算耗尽；返回「真的没了」。步数按整数算（浮点累加会多转一圈）。
    private static func waitUntilGone(_ budget: TimeInterval) -> Bool {
        let steps = max(1, Int((budget / pollStep).rounded()))
        for _ in 0..<steps {
            if !engineRunning() { return true }
            pause(pollStep)
        }
        return !engineRunning()
    }
}

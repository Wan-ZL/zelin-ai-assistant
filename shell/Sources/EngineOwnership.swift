// EngineOwnership.swift — 引擎归属与孤儿回收（CONTRACT §61.8；§15 录制三态 2026-09-14 追记；
// §56.5 relaunch 规则的壳半边）。issue #318：install.sh 换壳 relaunch 之后，旧壳拉起的
// `screenpipe record` 既不随旧壳退出、也不被新壳接管——它 ppid=1 继续录屏，TCC 的责任方
// 仍是那个已经不存在的旧壳身份；新壳的 5 s tick 只看 pgrep，就把这台外人的引擎当成「自己
// 的」静默认领（`autostartIfNeeded` 见 running=true 直接跳过 spawn）。
//
// 为什么长在这里而不是 Recording.swift：那份文件是 mac/ 冻结规范的逐字节副本（§61.3，
// tests/test_shell_engine_mirror.py 执法）。本模块照 §61.7 `RecordingSchedule.swift` 的先例
// 站在它外面，只借它三样公开的东西——`enginePattern`（§15 契约停法的那个模式，逐字复用、
// 绝不另写一个）、`isEngineRunning()`（pgrep）、`engineProcess`（我们自己 spawn 的凭据）——
// 全部经缝注入，harness 绝不真 pgrep / pkill。
//
// 两个判决（纯函数，判例 shell/tests/LaunchHarness.swift [8]/[9]）：
//   启动：新壳进程的 `engineProcess` 必然是 nil ⇒ 此刻还活着的引擎**按定义不是我们的**
//         → 先回收再让 autostart 看世界。唯一例外 = §54 那个冻结的原生 app 在班（它自己
//         拉自己的引擎，一根手指不碰）。
//   退出：只停我们自己 spawn 的那一台（main.swift 的「生命周期诚实原则」同款）。
//
// 诚实边界（issue #318 Expected 第 1 条只兑现两条退出路径）：**崩溃**（SIGKILL / 闪退）
// 时 `applicationWillTerminate` 根本不跑，引擎会一直录到下一次壳启动的回收那一刻——
// 没有 pid 文件、没有进程组托管，本条不假装覆盖它。install.sh 的 `ui` 步另有一次扫除
// （壳没在跑却有引擎 = 孤儿），把这个窗口从「到下次开 app 为止」缩到「到下次部署为止」。

import Foundation

enum EngineOwnership {

    // MARK: - 纯判决（无 I/O，harness 直接钉）

    /// 启动那一刻对「现在跑着的引擎归谁」的判决。
    enum LaunchVerdict: Equatable {
        /// 没有引擎在跑——autostart 照常。
        case idle
        /// §54 冻结的原生 app 在班：那是它的引擎，不碰（它自己的退出路径管它）。
        case legacyOwns
        /// 有引擎、没人认领 = 孤儿：先杀，再由 autostart 按当前设置重新拉起。
        case reclaim
    }

    /// 启动判决。`engineAlive` = pgrep 有结果；`legacyAppRunning` = `pgrep -x ZelinAIEngineer`。
    /// 本进程刚出生，所以**不需要**第三个输入：`engineProcess` 恒为 nil。
    static func decideAtLaunch(engineAlive: Bool, legacyAppRunning: Bool) -> LaunchVerdict {
        guard engineAlive else { return .idle }
        return legacyAppRunning ? .legacyOwns : .reclaim
    }

    /// 退出判决：只有我们自己 spawn 过引擎（`engineProcess != nil`）才在退出路径上停它。
    /// 对一台我们仅仅「看见」的引擎绝不动手——那可能是原生 app 的、或 owner 手工起的。
    static func stopAtExit(spawned: Bool) -> Bool { spawned }

    // MARK: - 常量

    /// §54 冻结原生 app 的可执行名（bundle id `com.zelin.ai-engineer`，`pgrep -x` 的键）。
    static let legacyExecName = "ZelinAIEngineer"
    /// 回收后等引擎真的消失的上限；超时只记一行诚实的日志，不升级信号。
    static let reclaimBudget: TimeInterval = 2.0
    /// 退出路径的预算：macOS 给 `applicationWillTerminate` 大约 5 s，本条最多花这么多。
    static let exitBudget: TimeInterval = 1.5
    /// 轮询粒度。
    static let pollStep: TimeInterval = 0.1

    // MARK: - 缝（默认直连系统；harness 注入，绝不真 pgrep / pkill）

    nonisolated(unsafe) static var engineRunning: () -> Bool = {
        RecordingController.isEngineRunning()
    }
    nonisolated(unsafe) static var legacyAppRunning: () -> Bool = {
        Shell.run("/usr/bin/pgrep", ["-x", legacyExecName]).0 == 0
    }
    nonisolated(unsafe) static var engineSpawnedByUs: () -> Bool = {
        RecordingController.engineProcess != nil
    }
    /// `signal` ∈ `"-TERM"` | `"-KILL"`；模式逐字借冻结引擎的 `enginePattern`
    /// （§15 契约停法 `pkill -f '<engine>'`——`[r]` 字符类让它不匹配自己的 argv）。
    nonisolated(unsafe) static var killEngine: (String) -> Void = { signal in
        _ = Shell.run("/usr/bin/pkill", [signal, "-f", RecordingController.enginePattern])
    }
    /// 面包屑落 `~/.screenpipe/engine.log`，与 autostart / 日程同一条格式；`[app` 前缀让
    /// `engineLogTail` 把它滤掉（诊断只读真引擎的话）。
    nonisolated(unsafe) static var logLine: (String) -> Void = { line in
        _ = Shell.ok("echo \"[app $(date '+%F %T')] \(line)\" >> \"$HOME/.screenpipe/engine.log\"")
    }
    nonisolated(unsafe) static var pause: (TimeInterval) -> Void = { Thread.sleep(forTimeInterval: $0) }

    // MARK: - 效果（阻塞——只在后台队列 / willTerminate 里跑）

    /// 启动回收。返回实际执行的判决（main.swift 只在它完成之后才放行日程执法、autostart
    /// 与 5 s tick——autostart 的 pgrep 若先看见孤儿就会认领它，而 tick 若先装上，回收的
    /// 那一下 pkill 会掉进 §61.7 的反抖窗、被读成「新引擎没起来」→ 假回滚 + 系统通知）。
    @discardableResult
    static func reclaimAtLaunch() -> LaunchVerdict {
        // 没有引擎在跑就不必再起第二个 pgrep 子进程问原生 app（启动路径上最常见的一格）。
        let alive = engineRunning()
        let verdict = decideAtLaunch(engineAlive: alive,
                                     legacyAppRunning: alive ? legacyAppRunning() : false)
        switch verdict {
        case .idle:
            break
        case .legacyOwns:
            logLine("reclaim skipped: \(legacyExecName) is running and owns its own engine")
        case .reclaim:
            logLine("reclaim orphan engine: this shell spawned nothing yet")
            killEngine("-TERM")
            if waitUntilGone(reclaimBudget) {
                logLine("reclaim orphan engine: done")
            } else {
                logLine("reclaim orphan engine: still alive after \(Int(reclaimBudget))s")
            }
        }
        return verdict
    }

    /// 退出回收：`pkill -TERM` → 等 ≤ `exitBudget` → 还在就 `pkill -KILL`。同步、有界。
    static func stopAtExit() {
        guard stopAtExit(spawned: engineSpawnedByUs()) else { return }
        logLine("stop engine on quit: this shell spawned it")
        killEngine("-TERM")
        if !waitUntilGone(exitBudget) { killEngine("-KILL") }
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

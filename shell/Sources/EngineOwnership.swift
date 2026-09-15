// EngineOwnership.swift — 引擎归属与孤儿回收（CONTRACT §61.8；§15 录制三态 2026-09-14 追记；
// §56.5 relaunch 规则的壳半边）。issue #318：install.sh 换壳 relaunch 之后，旧壳拉起的
// `screenpipe record` 既不随旧壳退出、也不被新壳接管——它 ppid=1 继续录屏，TCC 的责任方
// 仍是那个已经不存在的旧壳身份；新壳的 5 s tick 只看 pgrep，就把这台外人的引擎当成「自己
// 的」静默认领（`autostartIfNeeded` 见 running=true 直接跳过 spawn）。
//
// 为什么长在这里而不是 Recording.swift：那份文件是 mac/ 冻结规范的逐字节副本（§61.3，
// tests/test_shell_engine_mirror.py 执法）。本模块照 §61.7 `RecordingSchedule.swift` 的先例
// 站在它外面，只借它三样公开的东西——`enginePattern`（§15 契约停法的那个模式，逐字复用、
// 绝不另写一个）、`isEngineRunning()`（pgrep）、`engineProcess`（我们 spawn 的那台的句柄）——
// 全部经缝注入，harness 绝不真 pgrep / pkill。
//
// 两个判决（纯函数，判例 shell/tests/LaunchHarness.swift [8]/[9]）：
//   启动：新壳进程的 `engineProcess` 必然是 nil ⇒ 此刻还活着的引擎**按定义不是我们的**
//         → 先回收再让 autostart 看世界。唯一例外 = §54 那个冻结的原生 app 在班（它自己
//         拉自己的引擎，一根手指不碰）。
//   退出：只停我们自己 spawn 的、**并且此刻还活着的**那一台（main.swift 的「生命周期诚实
//         原则」同款）。凭据是活性不是历史：`engineProcess?.isRunning == true`。
//         `Recording.swift` 从不把 `engineProcess` 置回 nil（只在 spawn 时赋一次值），
//         所以 `!= nil` 会变成「这个壳曾经起过引擎」的终身通行证——§61.7 日程停过 /
//         owner 切 off / 引擎自己崩过之后，退出路径会拿着这张过期凭据去 pkill 一台
//         外人的引擎。recipe 结尾是 `exec npx screenpipe record …`，所以我们握着的
//         那个 `Process` **就是**引擎本身，`isRunning` 是精确的。
//         第二道保险与启动路径同款：§54 冻结原生 app 在班时退出路径也不开火——
//         `pkill -f <pattern>` 分不清是谁的引擎，会连它那台一起带走。
//
// 诚实边界（issue #318 Expected 第 1 条只兑现两条退出路径）：**崩溃**（SIGKILL / 闪退）
// 时 `applicationWillTerminate` 根本不跑，引擎会一直录到下一次壳启动的回收那一刻——
// 没有 pid 文件、没有进程组托管，本条不假装覆盖它。install.sh 的 `ui` 步另有一次扫除
// （壳没在跑却有引擎 = 孤儿），把这个窗口从「到下次开 app 为止」缩到「到下次部署为止」。
// 第二个缺口：§54 冻结原生 app 在班时，启动与退出两条路都让手——我们自己的引擎会漏停，
// 等下一次没有它在班的启动才被回收。§54 的不碰它是硬规矩，漏停只是慢一点。

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

    /// 退出判决：只有**我们 spawn 的那台此刻还活着**（`engineProcess?.isRunning == true`）
    /// 才在退出路径上停它。对一台我们仅仅「看见」的引擎绝不动手——那可能是原生 app 的、
    /// 或 owner 手工起的、或我们那台死掉之后别人补上的。
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
    /// 「我们那台引擎此刻还活着吗」。**活性，不是历史**：`Recording.swift` 只在 spawn 时
    /// 赋一次 `engineProcess`、从不置回 nil，`!= nil` 因此是终身通行证（判例 [9](i) 直接
    /// 钉这个默认实现，所以它有名字——缝被注入过之后也够得着）。
    nonisolated(unsafe) static let defaultEngineSpawnedByUs: () -> Bool = {
        RecordingController.engineProcess?.isRunning == true
    }
    nonisolated(unsafe) static var engineSpawnedByUs: () -> Bool = EngineOwnership.defaultEngineSpawnedByUs
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
        // 与启动路径同一道守卫（§54）：`pkill -f <pattern>` 分不清引擎的归属，冻结原生 app
        // 在班时开火会连它那台一起带走。宁可漏停我们自己的（下次启动的回收 / `ui` 步的
        // 扫除兜底），也绝不碰它的——代价如实写在 §61.8 的诚实边界里。
        if legacyAppRunning() {
            logLine("stop engine on quit skipped: \(legacyExecName) is running — "
                    + "pkill -f would take its engine down too")
            return
        }
        logLine("stop engine on quit: this shell spawned it and it is still alive")
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

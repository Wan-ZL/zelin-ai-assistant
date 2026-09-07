// RecordingSchedule.swift — 录制日程（issue #27；CONTRACT §61.7）：只在设定的时间窗内让
// screenpipe 引擎跑。默认关（always-on，与今天行为一字不差）；opt-in 后，窗外把引擎停掉、
// 窗内把引擎按 owner 选的模式拉起来——`recordingMode` 本身不动（「按日程暂停」是派生状态，
// 不是第四种模式），所以 header 的三态、consent 语义、LegacyPrefs 迁移全部照旧。
//
// 为什么长在这里而不是 Recording.swift：那份文件是 mac/ 冻结规范的逐字节副本（§61.3，
// tests/test_shell_engine_mirror.py 执法）。日程门只借它三样公开的东西——`mode`、
// `applyMode()`（stop → start）、`isEngineRunning` / `stopEngineBlocking`（pgrep / pkill）——
// 全部经缝注入，harness 绝不真 pkill。
//
// 驱动：main.swift 的 5 s tick（`enforce(reason: "tick")`）+ `NSWorkspace.didWakeNotification`
// （合盖睡过边界醒来立刻判）+ 偏好改动（桥 `setRecordingSchedule`）+ 启动（窗外则跳过 autostart）。
// 边界语义见 CONTRACT §61.7；本文件只做 算窗口 → 比上一拍 → 停 / 起。

import AppKit
import Combine
import Foundation

// MARK: - 纯值：日程 + 窗口数学（无 I/O，harness 直接钉）

struct RecordingScheduleSpec: Equatable {
    var enabled: Bool
    /// "HH:MM"（24 h，本地时间）。start == end 是坏值（桥拒绝）；start > end = 跨午夜窗口。
    var start: String
    var end: String
    /// Calendar weekday：1 = 周日 … 7 = 周六；已排序去重、非空。
    var days: [Int]

    /// issue #27 的例子：工作日 09:00–19:00；`enabled: false` = 现状 always-on。
    static let defaults = RecordingScheduleSpec(enabled: false, start: "09:00", end: "19:00", days: [2, 3, 4, 5, 6])
    static let weekdayRange = 1...7

    /// 严格 "HH:MM" → 当天分钟数；任何别的形状（"9:00"、"24:00"、"09:60"、带空格）→ nil。
    static func minutes(_ hhmm: String) -> Int? {
        let parts = hhmm.split(separator: ":", omittingEmptySubsequences: false)
        guard parts.count == 2, parts[0].count == 2, parts[1].count == 2,
              let h = Int(parts[0]), let m = Int(parts[1]),
              (0...23).contains(h), (0...59).contains(m) else { return nil }
        return h * 60 + m
    }

    static func normalizedDays(_ raw: [Int]) -> [Int]? {
        let clean = Array(Set(raw)).sorted()
        guard !clean.isEmpty, clean.allSatisfy({ weekdayRange.contains($0) }) else { return nil }
        return clean
    }

    /// 这份日程本身合法？（桥在写入前校验；坏值永远进不了 UserDefaults）
    var isValid: Bool {
        guard let s = Self.minutes(start), let e = Self.minutes(end), s != e else { return false }
        return Self.normalizedDays(days) == days
    }

    /// `date` 落在捕获窗口里？同日窗口（start < end）= [start, end) 且当天在 days 里；
    /// 跨午夜窗口（start > end）归 **开始那天**：22:00–02:00 勾周五 = 周五 22:00 → 周六 02:00。
    /// 日程不合法时恒 true（宁可多录，绝不因为坏值把录制停死）。
    func contains(_ date: Date, calendar: Calendar = .current) -> Bool {
        guard let s = Self.minutes(start), let e = Self.minutes(end), s != e else { return true }
        let comps = calendar.dateComponents([.weekday, .hour, .minute], from: date)
        guard let weekday = comps.weekday, let hour = comps.hour, let minute = comps.minute else { return true }
        let now = hour * 60 + minute
        if s < e { return days.contains(weekday) && now >= s && now < e }
        if now >= s { return days.contains(weekday) }
        if now < e {
            let previous = weekday == 1 ? 7 : weekday - 1
            return days.contains(previous)
        }
        return false
    }

    /// 快照用（§61.1 wire；键名 snake_case、add-only）。`paused` 由控制器另加。
    var wireValue: [String: Any] {
        ["enabled": enabled, "start": start, "end": end, "days": days]
    }
}

// MARK: - 控制器：UserDefaults 持久化 + 边界执法

@MainActor
final class RecordingSchedule: ObservableObject {
    /// 进程内唯一实例；harness 换成自己的（注入 UserDefaults suite，绝不写 .standard）。
    static var active = RecordingSchedule(defaults: .standard)

    static let enabledKey = "recordingScheduleEnabled"
    static let startKey = "recordingScheduleStart"
    static let endKey = "recordingScheduleEnd"
    static let daysKey = "recordingScheduleDays"

    /// 窗外把一台**正在跑**的引擎停掉前要连续观察到它跑的 tick 数（≈ 10–15 s）。不是 1 的原因：
    /// 冻结引擎的 applyMode 在 spawn 后 0.5 s + 7.5 s 里还在「慢死亡观察」，这时 pkill 会被它当成
    /// 「新模式没起来」而回滚到上一个模式并弹「已退回…」——那是日程造成的假回滚。边界跨越
    /// （active → paused）与醒来的那一拍不受此限，立刻停。
    static let killAfterTicks = 3

    // 缝（默认直连冻结引擎 / 本机时钟与日历；harness 注入）
    static var now: () -> Date = { Date() }
    static var calendar: () -> Calendar = { Calendar.current }
    static var currentMode: () -> String = { RecordingController.shared.mode }
    static var engineRunning: () -> Bool = { RecordingController.shared.engineRunning }
    /// 窗外停引擎：pgrep 有才 pkill（后台队列；pkill 本身是 §15 契约停法）。
    static var stopEngine: (String) -> Void = { reason in
        DispatchQueue.global(qos: .userInitiated).async {
            guard RecordingController.isEngineRunning() else { return }
            _ = Shell.ok("echo \"[app $(date '+%F %T')] schedule pause reason=\(reason)\" >> \"$HOME/.screenpipe/engine.log\"")
            RecordingController.stopEngineBlocking()
        }
    }
    /// 窗内拉起：冻结引擎自己的 stop → start（TCC / ffmpeg / 诊断都在那边，零复制）。
    static var startEngine: (String) -> Void = { reason in
        _ = Shell.ok("echo \"[app $(date '+%F %T')] schedule resume reason=\(reason)\" >> \"$HOME/.screenpipe/engine.log\"")
        RecordingController.shared.applyMode()
    }

    @Published private(set) var spec: RecordingScheduleSpec
    /// 派生：日程开 ∧ mode != off ∧ 现在不在窗内。快照 `recording.schedule.paused`。
    @Published private(set) var paused = false

    private let defaults: UserDefaults
    private var lastPaused: Bool?
    private var runningWhilePausedTicks = 0
    private var wakeObserver: NSObjectProtocol?

    init(defaults: UserDefaults) {
        self.defaults = defaults
        spec = Self.load(from: defaults)
    }

    deinit {
        if let wakeObserver { NSWorkspace.shared.notificationCenter.removeObserver(wakeObserver) }
    }

    /// 读偏好；缺键 / 坏值逐键回落默认（一把坏键不拖垮整份日程）。
    static func load(from defaults: UserDefaults) -> RecordingScheduleSpec {
        var spec = RecordingScheduleSpec.defaults
        if defaults.object(forKey: enabledKey) != nil { spec.enabled = defaults.bool(forKey: enabledKey) }
        if let s = defaults.string(forKey: startKey), RecordingScheduleSpec.minutes(s) != nil { spec.start = s }
        if let e = defaults.string(forKey: endKey), RecordingScheduleSpec.minutes(e) != nil { spec.end = e }
        if let raw = defaults.array(forKey: daysKey) as? [Int],
           let days = RecordingScheduleSpec.normalizedDays(raw) {
            spec.days = days
        }
        if RecordingScheduleSpec.minutes(spec.start) == RecordingScheduleSpec.minutes(spec.end) {
            // start == end 只可能来自手改 plist；回落默认窗口而不是停死录制
            spec.start = RecordingScheduleSpec.defaults.start
            spec.end = RecordingScheduleSpec.defaults.end
        }
        return spec
    }

    /// 桥 `setRecordingSchedule` 的写侧：整份已校验的日程落盘 + 立刻执法（开日程时窗外 = 马上停；
    /// 关日程时正暂停 = 马上起）。
    func apply(_ newSpec: RecordingScheduleSpec) {
        precondition(newSpec.isValid, "RecordingSchedule.apply needs a validated spec")
        let changed = newSpec != spec
        defaults.set(newSpec.enabled, forKey: Self.enabledKey)
        defaults.set(newSpec.start, forKey: Self.startKey)
        defaults.set(newSpec.end, forKey: Self.endKey)
        defaults.set(newSpec.days, forKey: Self.daysKey)
        spec = newSpec
        if changed {
            Analytics.log("recording_schedule_set", fields: [
                "enabled": newSpec.enabled, "start": newSpec.start, "end": newSpec.end,
                "days": newSpec.days.count,
            ])
        }
        enforce(reason: "prefs")
    }

    /// 现在应该录吗？（启动序列用：窗外跳过 autostartIfNeeded，引擎根本不起）
    func allowsCaptureNow() -> Bool {
        !spec.enabled || spec.contains(Self.now(), calendar: Self.calendar())
    }

    /// 每拍一次：算 应暂停 → 与上一拍比 → 停 / 起。幂等；reason 只进日志 / analytics。
    func enforce(reason: String) {
        let mode = Self.currentMode()
        let shouldPause = spec.enabled && mode != "off" && !spec.contains(Self.now(), calendar: Self.calendar())
        if paused != shouldPause { paused = shouldPause }
        let edge = lastPaused != nil && lastPaused != shouldPause
        lastPaused = shouldPause

        if shouldPause {
            // 边界跨越 / 刚开日程：立刻停。醒来：本来就暂停着且引擎没在跑 = 无事（睡前那一拍已经停过，
            // 再记一次 pause 事件就是虚报）；引擎在跑才不等宽限立刻停。其余拍：引擎又被拉起来了
            // （autostart、TCC 自愈、owner 在窗外点了模式或「重启」）→ 连续 killAfterTicks 拍看见它在跑才停
            // （见常量注释）。
            if edge || (reason == "wake" && Self.engineRunning()) {
                runningWhilePausedTicks = 0
                Analytics.log("recording_schedule_pause", fields: ["reason": reason, "mode": mode])
                Self.stopEngine(reason)
                return
            }
            if Self.engineRunning() {
                runningWhilePausedTicks += 1
                if runningWhilePausedTicks >= Self.killAfterTicks {
                    runningWhilePausedTicks = 0
                    Analytics.log("recording_schedule_pause", fields: ["reason": "\(reason)_late", "mode": mode])
                    Self.stopEngine(reason)
                }
            } else {
                runningWhilePausedTicks = 0
            }
            return
        }

        runningWhilePausedTicks = 0
        // paused → active 的那一拍才拉引擎（每拍都拉会把 ffmpeg 缺失等真死因变成 5 s 一次的重启风暴）
        if edge && mode != "off" {
            Analytics.log("recording_schedule_resume", fields: ["reason": reason, "mode": mode])
            Self.startEngine(reason)
        }
    }

    /// 合盖睡过边界：醒来那一拍不等 5 s timer（timer 在睡眠中不走，醒后也会补一拍，这里只是提前）。
    func startObservingWake() {
        guard wakeObserver == nil else { return }
        wakeObserver = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didWakeNotification, object: nil, queue: .main) { _ in
            DispatchQueue.main.async {
                MainActor.assumeIsolated { RecordingSchedule.active.enforce(reason: "wake") }
            }
        }
    }

    /// 快照块（§61.1 add-only）：日程四键 + 派生 `paused`。
    func wireValue() -> [String: Any] {
        var value = spec.wireValue
        value["paused"] = paused
        return value
    }
}

// DiagnosticsRules.swift — §48 诊断卡资格的纯逻辑（Foundation-only）。
//
// Diagnostics.swift 的 rebuild() 只做 IO（读投影/凭证/plist 存在性），
// 「该不该出卡」的判断全部收在这里，经 mac/LogicTests 符号链接被判例钉住
// （被测代码是真文件，不是拷贝——Package.swift 顶部的约定）。
// 消费的是 shared/Sources/Contract.swift 的 RadarSourceHealth（§48 投影的
// Swift 侧解码类型）——不再有第二条裸 JSONSerialization 读法。

import Foundation

enum DiagnosticsRules {

    /// setup 类 skip_reason：源开着但还没配好。这类卡需要**真实意愿信号**，
    /// 否则「enabled 默认 true」会让全新安装、从没碰过 Gmail 的用户永久吃
    /// 一张「开着但没配好」的常驻卡（anti-nag 反例）。
    static let gmailSetupReasons: Set<String> = ["no_credentials", "no_address"]

    /// Gmail 卡的文案分组：failure 长什么样决定引导用户去修什么——
    /// command 类（§14bis 自定义抓取命令没跑成/输出坏）跟应用密码毫无关系，
    /// 统一说成「密码过期」是误导。
    enum GmailCardKind {
        case setup        // 还没配好 → 引导补凭证/地址
        case command      // gmail_fetch_command 在报错 → 引导检查命令
        case connection   // IMAP 登录/网络失败 → 引导查密码/地址
    }

    static func gmailCardKind(reason: String) -> GmailCardKind {
        if gmailSetupReasons.contains(reason) { return .setup }
        if reason == "command_failed" || reason == "command_bad_output" {
            return .command
        }
        return .connection
    }

    /// Gmail 诊断卡资格（§48.4）：
    /// - 开关判据 = 投影 `enabled`（缺投影的旧 payload 回退老判据：凭证非空）；
    /// - setup 类 reason（no_credentials/no_address）额外要求意愿信号 ——
    ///   用户碰过开关（settings_overrides 存在 gmail_enabled 键，开或关都算
    ///   碰过）**或**凭证文件已存在（配到一半）；
    /// - 连接类 reason（auth_failed 等）维持投影判据即可（有凭证在报错 =
    ///   用户显然配过）；
    /// - `disabled` 是退役码（仅升级瞬间的残留记录），永不出卡。
    static func gmailCardEligible(reason: String?,
                                  projected: RadarSourceHealth?,
                                  legacyCredentialNonEmpty: Bool,
                                  switchTouched: Bool,
                                  credentialFileExists: Bool) -> Bool {
        guard let reason, !reason.isEmpty, reason != "disabled" else { return false }
        let enabled = projected?.enabled ?? legacyCredentialNonEmpty
        guard enabled else { return false }
        if gmailSetupReasons.contains(reason) {
            return switchTouched || credentialFileExists
        }
        return true
    }

    /// 「雷达调度未安装」修复卡资格（§48.6）：源按真源投影开着、launchd
    /// plist 却不在（典型路径：关着时 .pkg 升级把 plist 退役，之后用户在
    /// 功能开关里重新打开——配置 on 但没人再装 plist，雷达永久静默）。
    /// 只认真实投影（旧 payload 缺投影 → 不出卡，保守），修复动作 =
    /// LaunchAgents.install（与设置面板的「重新安装」同一条路）。
    /// `repairFailed`：最近一次卡上重装**失败**的回执——plist 可能已写成但
    /// launchctl load 没成（雷达照样死），仅凭「plist 存在」撤卡会把失败
    /// 吞成成功；有失败回执时卡必须留着（文案换成失败详情）。
    static func schedulerMissing(projected: RadarSourceHealth?,
                                 plistExists: Bool,
                                 repairFailed: Bool = false) -> Bool {
        guard let projected, projected.enabled else { return false }
        return !plistExists || repairFailed
    }
}

// DiagnosticsRulesTests.swift — §46.4/§46.6 诊断卡资格纯逻辑的判例。
// DiagnosticsRules.swift 经符号链接进入 MacLogic（被测代码是真文件），
// 消费的投影类型 = Contract.swift 的 RadarSourceHealth（真实读路径同款）。

import Foundation
import Testing
@testable import MacLogic

struct DiagnosticsRulesTests {

    private func projected(_ enabled: Bool) -> RadarSourceHealth {
        RadarSourceHealth(enabled: enabled)
    }

    // MARK: - §46.4 gmail setup 卡需要真实意愿信号

    @Test func freshInstallNeverGetsSetupCard() {
        // 全新安装：enabled 默认 true、没碰过开关、没有凭证文件 —— 不出卡
        // （「默认 true」不是 intent；这曾是常驻假卡的来源）。
        #expect(!DiagnosticsRules.gmailCardEligible(
            reason: "no_credentials", projected: projected(true),
            legacyCredentialNonEmpty: false,
            switchTouched: false, credentialFileExists: false))
        #expect(!DiagnosticsRules.gmailCardEligible(
            reason: "no_address", projected: projected(true),
            legacyCredentialNonEmpty: false,
            switchTouched: false, credentialFileExists: false))
    }

    @Test func touchedSwitchUnlocksSetupCard() {
        // 用户碰过开关（override 键存在）= 真实意愿 → setup 卡照出
        #expect(DiagnosticsRules.gmailCardEligible(
            reason: "no_credentials", projected: projected(true),
            legacyCredentialNonEmpty: false,
            switchTouched: true, credentialFileExists: false))
    }

    @Test func credentialFileUnlocksSetupCard() {
        // 配到一半（凭证文件存在，哪怕空）也是意愿信号
        #expect(DiagnosticsRules.gmailCardEligible(
            reason: "no_address", projected: projected(true),
            legacyCredentialNonEmpty: false,
            switchTouched: false, credentialFileExists: true))
    }

    @Test func connectionReasonNeedsOnlyProjection() {
        // 连接类 reason（auth_failed）维持投影判据：开着就报，不再要求
        // 额外意愿信号（有凭证在报错 = 显然配过）。
        #expect(DiagnosticsRules.gmailCardEligible(
            reason: "auth_failed", projected: projected(true),
            legacyCredentialNonEmpty: false,
            switchTouched: false, credentialFileExists: false))
    }

    @Test func disabledSourceNeverCards() {
        // 投影 enabled=false（用户关了源）→ 任何 reason 都不出卡
        #expect(!DiagnosticsRules.gmailCardEligible(
            reason: "auth_failed", projected: projected(false),
            legacyCredentialNonEmpty: true,
            switchTouched: true, credentialFileExists: true))
    }

    @Test func retiredDisabledReasonNeverCards() {
        // 退役码 `disabled`（升级瞬间的残留记录）永不出卡
        #expect(!DiagnosticsRules.gmailCardEligible(
            reason: "disabled", projected: projected(true),
            legacyCredentialNonEmpty: true,
            switchTouched: true, credentialFileExists: true))
    }

    @Test func missingProjectionFallsBackToLegacy() {
        // 旧 actd payload（无投影）回退老判据：凭证非空才算开
        #expect(DiagnosticsRules.gmailCardEligible(
            reason: "auth_failed", projected: nil,
            legacyCredentialNonEmpty: true,
            switchTouched: false, credentialFileExists: true))
        #expect(!DiagnosticsRules.gmailCardEligible(
            reason: "auth_failed", projected: nil,
            legacyCredentialNonEmpty: false,
            switchTouched: false, credentialFileExists: false))
    }

    @Test func emptyReasonNeverCards() {
        #expect(!DiagnosticsRules.gmailCardEligible(
            reason: nil, projected: projected(true),
            legacyCredentialNonEmpty: true,
            switchTouched: true, credentialFileExists: true))
        #expect(!DiagnosticsRules.gmailCardEligible(
            reason: "", projected: projected(true),
            legacyCredentialNonEmpty: true,
            switchTouched: true, credentialFileExists: true))
    }

    // MARK: - §46.6 「调度未安装」修复卡

    @Test func onButPlistMissingAlarms() {
        // 关着时升级退役了 plist → 用户重新打开 → 配置 on 但 plist 缺失
        #expect(DiagnosticsRules.schedulerMissing(
            projected: projected(true), plistExists: false))
    }

    @Test func onWithPlistIsQuiet() {
        #expect(!DiagnosticsRules.schedulerMissing(
            projected: projected(true), plistExists: true))
    }

    @Test func offNeverAlarms() {
        // 关着的源没 plist 是正常形态（§46.5 防复活闸门的产物）
        #expect(!DiagnosticsRules.schedulerMissing(
            projected: projected(false), plistExists: false))
    }

    @Test func missingProjectionStaysConservative() {
        // 旧 payload（无投影）不出修复卡 —— 宁可漏，不误报
        #expect(!DiagnosticsRules.schedulerMissing(
            projected: nil, plistExists: false))
    }
}

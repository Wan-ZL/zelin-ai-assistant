// AnalyticsGateTests.swift — Swift 写者的 features.analytics gate 判例
// （CONTRACT §16 追记）。Analytics.log / firstReach 与 Python 写者共用同一份
// state/analytics/events.jsonl，所以 Swift 侧必须有对称的 gate：
// 优先级 overrides（嵌套 → 平铺）→ config.yaml features: 块 → 默认 on；
// 隐私 fail-closed 特例：overrides 存在但解析不了 = 按关处理。
//
// AppPaths.stateRoot 每进程只解析一次（static let），所以整个 suite 共用
// 一个临时 HOME，用例之间靠改写/删除该目录下的文件切换场景——必须串行。

import Testing
import Foundation
@testable import MacLogic

@Suite(.serialized)
struct AnalyticsGateTests {

    /// 进程内唯一的临时 HOME：首次触碰时 setenv，先于任何 AppPaths 解析。
    private static let home: String = {
        let dir = NSTemporaryDirectory() + "analytics-gate-" + UUID().uuidString
        try? FileManager.default.createDirectory(
            atPath: dir + "/state", withIntermediateDirectories: true)
        setenv("AIASSISTANT_HOME", dir, 1)
        return dir
    }()

    private var overridesPath: String { Self.home + "/state/settings_overrides.json" }
    private var configPath: String { Self.home + "/config.yaml" }

    /// 每个用例开头清场（Swift Testing 无 setUp；串行 suite 里够用）。
    private func reset() {
        try? FileManager.default.removeItem(atPath: overridesPath)
        try? FileManager.default.removeItem(atPath: configPath)
    }

    private func writeOverrides(_ json: String) {
        try? json.write(toFile: overridesPath, atomically: true, encoding: .utf8)
    }

    @Test func defaultIsOnWithNoConfigFiles() {
        reset()
        #expect(Analytics.featureEnabled())
    }

    @Test func nestedOverridesFlagOffDisables() {
        reset()
        writeOverrides(#"{"features": {"analytics": false}}"#)
        #expect(!Analytics.featureEnabled())
    }

    @Test func flatOverridesFlagOffDisables() {
        reset()
        writeOverrides(#"{"features.analytics": false}"#)
        #expect(!Analytics.featureEnabled())
    }

    @Test func configYamlFeaturesBlockOffDisables() {
        reset()
        try? "features:\n  analytics: false\n"
            .write(toFile: configPath, atomically: true, encoding: .utf8)
        #expect(!Analytics.featureEnabled())
    }

    @Test func overridesWinOverConfigYaml() {
        // §15 优先级：overrides 是 UI 写的最后一层，压过 config.yaml
        reset()
        try? "features:\n  analytics: false\n"
            .write(toFile: configPath, atomically: true, encoding: .utf8)
        writeOverrides(#"{"features": {"analytics": true}}"#)
        #expect(Analytics.featureEnabled())
    }

    @Test func unparseableOverridesFailsClosed() {
        // 隐私特例：显式退出可能正躺在这份读不懂的文件里——按关处理
        reset()
        writeOverrides("{broken json")
        #expect(!Analytics.featureEnabled())
    }
}

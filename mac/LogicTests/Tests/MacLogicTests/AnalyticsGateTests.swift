// AnalyticsGateTests.swift — Swift 写者的 features.analytics gate 判例
// （CONTRACT §16 追记）。Analytics.log / firstReach 与 Python 写者共用同一份
// state/analytics/events.jsonl，所以 Swift 侧必须有对称的 gate：
// 优先级 overrides（嵌套 → 平铺）→ config.yaml features: 块 → 默认 on；
// 隐私 fail-closed 特例：overrides 存在但解析不了、真 config.yaml 存在但
// 读不出/行扫描认不动（非 UTF-8、跨行 flow mapping、空值）= 按关处理。
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

    @Test func pyyamlBoolSpellingsAllDisable() {
        // 布尔拼写集对齐 PyYAML（act/lib/config._coerce_bool）：Python 认
        // no/off/0 为关，Swift 侧不能只认 "false"
        for spelling in ["false", "no", "off", "0", "False", "NO", "Off"] {
            reset()
            try? "features:\n  analytics: \(spelling)\n"
                .write(toFile: configPath, atomically: true, encoding: .utf8)
            #expect(!Analytics.featureEnabled(), "spelling: \(spelling)")
        }
    }

    @Test func inlineFlowMappingFormIsParsed() {
        // 单行内联花括号形——Python 侧 yaml 认，Swift 行扫描也必须认
        reset()
        try? "features: {slack_radar: true, analytics: false}\n"
            .write(toFile: configPath, atomically: true, encoding: .utf8)
        #expect(!Analytics.featureEnabled())
        // 同形但值为 on：不许把「出现在内联块里」本身当成 off
        try? "features: {analytics: yes}\n"
            .write(toFile: configPath, atomically: true, encoding: .utf8)
        #expect(Analytics.featureEnabled())
    }

    @Test func spaceBeforeColonIsValidYaml() {
        // `analytics : false` 是合法 YAML（PyYAML 解析成 analytics 键）——
        // Swift 行扫描不认它就与 Python gate 分叉：Python 停记、App 继续记
        reset()
        try? "features:\n  analytics : false\n"
            .write(toFile: configPath, atomically: true, encoding: .utf8)
        #expect(!Analytics.featureEnabled())
        // 顶层键同款：`features :` 块形 + 内联花括号形（键旁空白）
        try? "features :\n  analytics: false\n"
            .write(toFile: configPath, atomically: true, encoding: .utf8)
        #expect(!Analytics.featureEnabled())
        try? "features : {analytics : off}\n"
            .write(toFile: configPath, atomically: true, encoding: .utf8)
        #expect(!Analytics.featureEnabled())
    }

    @Test func nestedOverridesBeatStaleFlatKey() {
        // 嵌套形 vs 平铺形同文件冲突：嵌套形优先（Python
        // _apply_settings_overrides 同序、与键序无关）——两侧对同一份
        // overrides 必须给出同一个 gate 答案
        reset()
        writeOverrides(#"{"features.analytics": true, "features": {"analytics": false}}"#)
        #expect(!Analytics.featureEnabled())
    }

    @Test func unparseableFlagValueFailsClosed() {
        // 值写了但判不动布尔 ⇒ 按损坏 off（Python _config_sources_intact
        // 同一保守探测）；键不存在才落默认 on
        reset()
        try? "features:\n  analytics: banana\n"
            .write(toFile: configPath, atomically: true, encoding: .utf8)
        #expect(!Analytics.featureEnabled())
    }

    @Test func unreadableConfigYamlFailsClosed() {
        // 真 config.yaml 存在但读不出（非 UTF-8）⇒ off，绝不回退默认 on
        // ——原配置可能正是 analytics: false，损坏 ≠ 未配置（§16 隐私特例，
        // 镜像 Python _config_sources_intact 对损坏 config.yaml 的探测）
        reset()
        FileManager.default.createFile(
            atPath: configPath, contents: Data([0xC3, 0x28, 0xA0, 0xA1]))
        #expect(!Analytics.featureEnabled())
    }

    @Test func multilineFlowMappingFailsClosed() {
        // `features: {` 换行接键值是合法 YAML（PyYAML 照样读出 false），
        // 单行扫描认不动 ⇒ fail-closed，两个读者对同一份文件同答案
        reset()
        try? "features: {\n  analytics: false}\n"
            .write(toFile: configPath, atomically: true, encoding: .utf8)
        #expect(!Analytics.featureEnabled())
    }

    @Test func singleQuotedBoolSpellingsMatchPyyaml() {
        // PyYAML 把 'false' 解析成字符串 "false"，_coerce_bool 照判——
        // Swift 只剥双引号就分叉：'false' 判 nil → off 碰巧对，'true' 判
        // nil → off 则是开关静默失效（fail-closed 方向，但仍是分叉）
        reset()
        try? "features:\n  analytics: 'false'\n"
            .write(toFile: configPath, atomically: true, encoding: .utf8)
        #expect(!Analytics.featureEnabled())
        try? "features:\n  analytics: 'true'\n"
            .write(toFile: configPath, atomically: true, encoding: .utf8)
        #expect(Analytics.featureEnabled())
    }

    @Test func crlfLineEndingsMatchPyyaml() {
        // CRLF 行尾的 \r 不算布尔拼写的一部分（PyYAML 照样解析）：
        // "false\r" 必须判关，"true\r" 必须判开（不许静默 off）
        reset()
        try? "features:\r\n  analytics: false\r\n"
            .write(toFile: configPath, atomically: true, encoding: .utf8)
        #expect(!Analytics.featureEnabled())
        try? "features:\r\n  analytics: true\r\n"
            .write(toFile: configPath, atomically: true, encoding: .utf8)
        #expect(Analytics.featureEnabled())
    }

    @Test func bareAnalyticsKeyFailsClosed() {
        // `analytics:` 空值：PyYAML 解析成 None，Python 判不动 → off；
        // Swift 不能把空值当「键不存在」落回默认 on
        reset()
        try? "features:\n  analytics:\n"
            .write(toFile: configPath, atomically: true, encoding: .utf8)
        #expect(!Analytics.featureEnabled())
    }

    @Test func firstReachMarkerOnlyAfterSuccessfulWrite() {
        // P2 时序：flag off 期间 firstReach 连 UserDefaults marker 也不落
        // （gate/查重/写入/marker 整链在 serial queue 内，写入成功才 mark）；
        // 重开后同一里程碑恰好发一次
        reset()
        let feature = "gate-test-" + UUID().uuidString
        let key = "analytics.firstReach." + feature
        defer { UserDefaults.standard.removeObject(forKey: key) }
        let events = Self.home + "/state/analytics/events.jsonl"
        try? FileManager.default.removeItem(atPath: events)

        writeOverrides(#"{"features": {"analytics": false}}"#)
        Analytics.firstReach(feature)
        Analytics.flush()
        #expect(!UserDefaults.standard.bool(forKey: key))
        #expect(!FileManager.default.fileExists(atPath: events))

        reset()  // flag 回到默认 on
        Analytics.firstReach(feature)
        Analytics.firstReach(feature)  // 第二次被 marker 挡住
        Analytics.flush()
        #expect(UserDefaults.standard.bool(forKey: key))
        let text = (try? String(contentsOfFile: events, encoding: .utf8)) ?? ""
        let hits = text.components(separatedBy: "\n")
            .filter { $0.contains(feature) }
        #expect(hits.count == 1)
    }
}

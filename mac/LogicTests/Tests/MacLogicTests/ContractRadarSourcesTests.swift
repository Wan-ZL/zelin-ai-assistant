// ContractRadarSourcesTests.swift — CONTRACT §46 `radar_sources` 投影的
// Swift 侧解码判例：add-only 字段（decodeIfPresent），旧 payload 缺字段时
// 解码为空 map、绝不 fail；坏行降级不炸整个 dashboard。
// Contract.swift 经符号链接进入 MacLogic（Package.swift 顶部的约定——被测
// 代码永远是真文件，不是拷贝）。

import Foundation
import Testing
@testable import MacLogic

struct ContractRadarSourcesTests {

    private func decode(_ json: String) throws -> Dashboard {
        try JSONDecoder().decode(Dashboard.self, from: Data(json.utf8))
    }

    @Test func decodesFullShape() throws {
        let dash = try decode("""
        {"generated_at": "2026-08-07T00:00:00Z", "counts": {},
         "radar_sources": {
           "gmail": {"enabled": true, "last_ok": "2026-08-01T00:00:00Z",
                     "skip_reason": "auth_failed", "stale": true},
           "slack": {"enabled": false, "last_ok": null,
                     "skip_reason": null, "stale": false}}}
        """)
        let gm = try #require(dash.radar_sources["gmail"])
        #expect(gm.enabled)
        #expect(gm.stale)
        #expect(gm.last_ok == "2026-08-01T00:00:00Z")
        #expect(gm.skip_reason == "auth_failed")
        let sl = try #require(dash.radar_sources["slack"])
        #expect(!sl.enabled)
        #expect(!sl.stale)
        #expect(sl.last_ok == nil)
        #expect(sl.skip_reason == nil)
    }

    @Test func missingMapDecodesEmpty() throws {
        // 旧 actd payload（§46 之前）没有 radar_sources —— 空 map，不 fail
        let dash = try decode(#"{"generated_at": null, "counts": {}}"#)
        #expect(dash.radar_sources.isEmpty)
    }

    @Test func partialEntryGetsDefaults() throws {
        // add-only 契约：未知/缺失字段按默认值降级（enabled/stale = false）
        let dash = try decode(#"{"counts": {}, "radar_sources": {"gmail": {}}}"#)
        let gm = try #require(dash.radar_sources["gmail"])
        #expect(!gm.enabled)
        #expect(!gm.stale)
        #expect(gm.last_ok == nil)
    }

    @Test func malformedMapDegradesToEmpty() throws {
        // radar_sources 是坏类型（字符串）时整体降级为空 map，不炸 dashboard
        let dash = try decode(#"{"counts": {}, "radar_sources": "broken"}"#)
        #expect(dash.radar_sources.isEmpty)
    }
}

// LoopHealthTests — §47.3 判例：连续 3 pass FAILED → failing 亮红；恢复
// （清零回执）→ 消。与 tests/test_actd_loop_health.py（Python 写侧）合起来
// 钉住整条投影链：actd 写 loop_health.json → App 读 → PipelineHealth.failing。
// Swift Testing（不是 XCTest）——理由见 test.sh 顶部（CLT 无 XCTest.framework）。

import Testing
import Foundation
@testable import MacLogic

struct LoopHealthTests {
    private func data(_ s: String) -> Data { Data(s.utf8) }

    @Test func threeConsecutiveFailuresIsFailing() {
        let lh = LoopHealth.parse(data(
            #"{"consecutive_failures": 3, "last_error": "NameError: x", "updated_at": "2026-08-07T00:00:00Z"}"#))
        #expect(lh?.consecutiveFailures == 3)
        #expect(lh?.lastError == "NameError: x")
        #expect(lh?.failing == true)   // 判例 ④ 前半：红点信号亮起
    }

    @Test func belowThresholdIsNotFailing() {
        // 单次/两次失败可能是瞬时抖动——阈值以下绝不报警
        for n in 0...2 {
            let lh = LoopHealth.parse(data(
                "{\"consecutive_failures\": \(n), \"last_error\": null}"))
            #expect(lh?.failing == false, "n=\(n) must not alarm")
        }
    }

    @Test func recoveryReceiptClearsAlarm() {
        // 判例 ④ 后半：恢复回执（清零 + last_error null）→ 红点消
        let lh = LoopHealth.parse(data(
            #"{"consecutive_failures": 0, "last_error": null, "updated_at": "2026-08-07T00:00:01Z"}"#))
        #expect(lh?.failing == false)
        #expect(lh?.lastError == nil)
    }

    @Test func corruptOrWrongShapeNeverAlarms() {
        // 诊断文件绝不许自己成为报警源：坏 JSON / 缺字段 / 负数 → nil
        #expect(LoopHealth.parse(data("not json")) == nil)
        #expect(LoopHealth.parse(data("{}")) == nil)
        #expect(LoopHealth.parse(data(#"{"consecutive_failures": "many"}"#)) == nil)
        #expect(LoopHealth.parse(data(#"{"consecutive_failures": -1}"#)) == nil)
        #expect(LoopHealth.load(path: "/nonexistent/loop_health.json") == nil)
    }
}

// PastedImagesTests.swift — ⌘V 认领矩阵（PastedImages.isImagePaste / readImages）
// 的判例测试。矩阵来自 PastedImages.swift 顶部的交互契约注释；每个用例钉一格。
// Swift Testing（不是 XCTest）：CLT-only 机器不带 XCTest.framework，而
// Testing.framework 随 Swift 6 工具链发布——见 test.sh 顶部的说明。
//
// Pasteboard hygiene: every test builds its own UNIQUELY NAMED NSPasteboard
// and releases it before returning — .general is never touched, so running
// the suite can't destroy the user's clipboard.

import Testing
import AppKit
@testable import MacLogic

@MainActor
struct PastedImagesClaimMatrixTests {

    // MARK: - fixtures

    /// Fresh private pasteboard. Callers MUST `defer { pb.releaseGlobally() }`
    /// — named pasteboards otherwise persist in the pboard server across runs.
    private func freshPasteboard() -> NSPasteboard {
        let pb = NSPasteboard(name: NSPasteboard.Name("logic-tests-" + UUID().uuidString))
        pb.clearContents()
        return pb
    }

    /// 4×4 opaque bitmap — the in-memory "截图" flavor (writes TIFF).
    private func tinyBitmapRep() -> NSBitmapImageRep {
        let rep = NSBitmapImageRep(
            bitmapDataPlanes: nil, pixelsWide: 4, pixelsHigh: 4,
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
            isPlanar: false, colorSpaceName: .deviceRGB,
            bytesPerRow: 0, bitsPerPixel: 0)!
        for x in 0..<4 {
            for y in 0..<4 { rep.setColor(.red, atX: x, y: y) }
        }
        return rep
    }

    /// One pasteboard item carrying a bitmap plus an optional text flavor —
    /// the Excel/Numbers "cell copy" shape (image + string on the SAME item).
    private func writeBitmap(_ pb: NSPasteboard, withText text: String?) {
        let rep = tinyBitmapRep()
        let img = NSImage(size: NSSize(width: 4, height: 4))
        img.addRepresentation(rep)
        let item = NSPasteboardItem()
        item.setData(img.tiffRepresentation!, forType: .tiff)
        if let text { item.setString(text, forType: .string) }
        #expect(pb.writeObjects([item]))
    }

    /// Real PNG on disk — the Finder-copy flavor. Caller deletes it.
    private func tempPNGFile() throws -> URL {
        let png = tinyBitmapRep().representation(using: .png, properties: [:])!
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("logic-tests-\(UUID().uuidString).png")
        try png.write(to: url)
        return url
    }

    // MARK: - 认领矩阵

    /// 纯位图（截图）= 认领，且 readImages 能解出这张图。
    @Test func pureBitmapIsClaimed() {
        let pb = freshPasteboard()
        defer { pb.releaseGlobally() }
        writeBitmap(pb, withText: nil)
        #expect(PastedImages.isImagePaste(pb))
        #expect(PastedImages.readImages(from: pb).count == 1)
    }

    /// 图片文件 URL（Finder copy，旁带文件名字符串 flavor）= 无条件认领——
    /// 文本 flavor 是文件名，不算数；readImages 从文件解码出图。
    @Test func imageFileURLWithFilenameTextIsClaimed() throws {
        let pb = freshPasteboard()
        defer { pb.releaseGlobally() }
        let url = try tempPNGFile()
        defer { try? FileManager.default.removeItem(at: url) }
        let item = NSPasteboardItem()
        item.setString(url.absoluteString, forType: .fileURL)
        item.setString(url.lastPathComponent, forType: .string)
        #expect(pb.writeObjects([item]))
        #expect(PastedImages.isImagePaste(pb))
        #expect(PastedImages.readImages(from: pb).count == 1)
    }

    /// 位图 + 多行文本双 flavor（Excel/Numbers 复制单元格）= 不认领——吞掉
    /// ⌘V 会让这类文本彻底无法粘贴，文本粘贴优先。
    @Test func bitmapWithMultilineTextIsNotClaimed() {
        let pb = freshPasteboard()
        defer { pb.releaseGlobally() }
        writeBitmap(pb, withText: "a\tb\nc\td")
        #expect(!PastedImages.isImagePaste(pb))
    }

    /// 位图 + 单行 URL 文本 = 当前实现不认领（非空文本一律让给文本粘贴）。
    /// 注意：这条钉的是「当前行为」——另一分支正在改这条规则；规则演进时
    /// 允许（且应该）更新本判例，其余矩阵格不受影响。
    @Test func bitmapWithSingleLineURLTextPinnedToCurrentBehavior() {
        let pb = freshPasteboard()
        defer { pb.releaseGlobally() }
        writeBitmap(pb, withText: "https://example.com/cat.png")
        #expect(!PastedImages.isImagePaste(pb))
    }

    /// 空剪贴板 = 不认领，readImages 返回空。
    @Test func emptyPasteboardIsNotClaimed() {
        let pb = freshPasteboard()
        defer { pb.releaseGlobally() }
        #expect(!PastedImages.isImagePaste(pb))
        #expect(PastedImages.readImages(from: pb).isEmpty)
    }
}

// PastedImages.swift — 输入框贴图（用户建议 #4/#5）：提建议弹窗、快速捕获/
// 直接开跑输入框、回答弹窗三处共用的粘贴收集器 + 缩略图行。
//
// 交互契约：⌘V 认领规则（isImagePaste）——图片文件 URL = 明确的贴图意图，
// 无条件认领（旁带文件名字符串也照收）；纯位图（截图）认领；**位图 + 非空
// 文本双 flavor**（Excel/Numbers 复制单元格）不认领，文本粘贴优先——否则这
// 类文本在输入框里彻底无法粘贴。一旦认领，事件必被吞掉——满员/解码失败也只
// beep 提示，绝不落回文本粘贴（图片文件的文本形态是本机绝对路径，插进会上传
// 的提建议正文就是路径泄漏）。缩略图行每张可 ✕ 移除；上限 4 张。
// 发送时由调用方把图片落成 PNG（savePNGs → <uuid>-<n>.png，落盘前统一降采样
// 到最长边 2560px）并把绝对路径写进 inbox action —— 图片本身永不上传，路径只
// 在本机管线里流转（feedback 记录 / 卡片 execution.attachments / answer 附图
// 行）。inbox 写失败时调用方删除本批 PNG（deleteFiles / answerAttachmentPaths
// 反解），actd 的日频附件 GC 只是兜底。

import AppKit
import SwiftUI
import Foundation

/// Collected pasted images for ONE input surface. Each of the three inputs
/// owns its own instance (a draft's images must never leak into another box).
@MainActor
final class PastedImagesModel: ObservableObject {
    /// 三处输入统一的上限。
    static let maxCount = 4

    struct Item: Identifiable {
        let id = UUID()
        let image: NSImage
    }

    @Published private(set) var items: [Item] = []

    /// The window hosting the input this model serves (HostingWindowReader
    /// keeps it current). The ⌘V key monitor only claims events belonging to
    /// this window WHILE IT IS KEY — @FocusState never flips false when a
    /// window merely loses key (AppKit keeps a non-key window's first
    /// responder), so without this a stale monitor in a background board
    /// window would steal the popover composer's paste (local monitors fire
    /// in install order and a nil return swallows the event).
    weak var hostWindow: NSWindow?

    var isEmpty: Bool { items.isEmpty }
    var images: [NSImage] { items.map { $0.image } }

    /// ⌘V handler. Returns true when the pasteboard is an image paste
    /// (isImagePaste's 认领规则) — the caller must then swallow the event,
    /// ALWAYS: at capacity (or on a decode failure) nothing is added and beep
    /// says "not taken", but the event never falls back to text paste — a
    /// Finder image file's text fallback would insert its local ABSOLUTE PATH
    /// into the draft (feedback text uploads → path leak). false = not an
    /// image paste, text paste proceeds untouched.
    @discardableResult
    func takeFromPasteboard(_ pb: NSPasteboard = .general) -> Bool {
        guard PastedImages.isImagePaste(pb) else { return false }
        let room = Self.maxCount - items.count
        let read = room > 0 ? PastedImages.readImages(from: pb) : []
        if read.isEmpty || read.count > room {
            NSSound.beep()   // full / decode failed / overflow truncated
        }
        for img in read.prefix(max(0, room)) {
            items.append(Item(image: img))
        }
        return true
    }

    func remove(_ id: UUID) { items.removeAll { $0.id == id } }
    func clear() { items.removeAll() }
}

@MainActor
enum PastedImages {
    /// Downsample bound: longest edge in pixels before an image is stored or
    /// encoded. tiffRepresentation materializes the UNCOMPRESSED bitmap (an
    /// 8000×6000 screenshot ≈ 190MB), so both the resident composer model and
    /// the send-time PNG encode must only ever see capped bitmaps — this
    /// keeps the synchronous main-thread encode of 4 images bounded.
    static let maxPixelDimension = 2560

    private static let urlReadingOptions: [NSPasteboard.ReadingOptionKey: Any] = [
        .urlReadingFileURLsOnly: true,
        .urlReadingContentsConformToTypes: ["public.image"],
    ]

    /// Cheap "is this an image paste?" probe — no decode, safe to call when
    /// the model is already full. 认领规则：
    /// - image FILE URL（Finder copy，content-checked against public.image）
    ///   = 明确的贴图意图，无条件认领（旁边的文件名字符串 flavor 不算数）；
    /// - 纯位图（截图等）认领；
    /// - 位图 + 非空文本双 flavor（Excel/Numbers 复制单元格连图带字）**不
    ///   认领**——吞掉 ⌘V 会让这类文本彻底无法粘贴，文本粘贴优先。
    static func isImagePaste(_ pb: NSPasteboard) -> Bool {
        if pb.canReadObject(forClasses: [NSURL.self], options: urlReadingOptions) {
            return true
        }
        guard pb.canReadObject(forClasses: [NSImage.self], options: [:]) else {
            return false
        }
        let text = pb.string(forType: .string) ?? ""
        return text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// Pasteboard → images (downsampled on the way in, so the resident
    /// composer never keeps a full-size original in memory). Callers gate on
    /// isImagePaste first — this only decodes the two claimed flavors: image
    /// FILE URLs (Finder copy) and in-memory bitmaps (截图). Everything else
    /// returns [].
    static func readImages(from pb: NSPasteboard) -> [NSImage] {
        if let urls = pb.readObjects(forClasses: [NSURL.self],
                                     options: urlReadingOptions) as? [URL],
           !urls.isEmpty {
            return urls.compactMap { NSImage(contentsOf: $0) }.map(downsampled)
        }
        if let imgs = pb.readObjects(forClasses: [NSImage.self],
                                     options: [:]) as? [NSImage] {
            return imgs.map(downsampled)
        }
        return []
    }

    /// Scale so the longest pixel edge fits maxPixelDimension; within-bound
    /// images pass through untouched. Falls back to the original on any
    /// rendering failure (an unbounded image beats a lost one).
    static func downsampled(_ image: NSImage) -> NSImage {
        var rect = NSRect(origin: .zero, size: image.size)
        guard let cg = image.cgImage(forProposedRect: &rect, context: nil,
                                     hints: nil) else { return image }
        let w = CGFloat(cg.width), h = CGFloat(cg.height)
        let longest = max(w, h)
        guard longest > CGFloat(maxPixelDimension) else { return image }
        let scale = CGFloat(maxPixelDimension) / longest
        let tw = max(1, Int(w * scale)), th = max(1, Int(h * scale))
        guard let rep = NSBitmapImageRep(
            bitmapDataPlanes: nil, pixelsWide: tw, pixelsHigh: th,
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
            isPlanar: false, colorSpaceName: .deviceRGB,
            bytesPerRow: 0, bitsPerPixel: 0)
        else { return image }
        rep.size = NSSize(width: tw, height: th)
        guard let ctx = NSGraphicsContext(bitmapImageRep: rep) else { return image }
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = ctx
        ctx.imageInterpolation = .high
        ctx.cgContext.draw(cg, in: CGRect(x: 0, y: 0,
                                          width: CGFloat(tw), height: CGFloat(th)))
        NSGraphicsContext.restoreGraphicsState()
        let out = NSImage(size: rep.size)
        out.addRepresentation(rep)
        return out
    }

    /// Flatten to PNG under `dir` as <uuid>-<n>.png (one uuid per send batch,
    /// n = 1-based position); returns the absolute paths actually written.
    /// Downsampled first (maxPixelDimension) so the encode cost is bounded.
    /// Best-effort per image — a broken bitmap is skipped, never fails the
    /// whole send (the text must still go out).
    static func savePNGs(_ images: [NSImage], toDir dir: String) -> [String] {
        guard !images.isEmpty else { return [] }
        try? FileManager.default.createDirectory(atPath: dir,
                                                 withIntermediateDirectories: true)
        let batch = UUID().uuidString
        var paths: [String] = []
        for (i, image) in images.enumerated() {
            let bounded = downsampled(image)
            guard let tiff = bounded.tiffRepresentation,
                  let rep = NSBitmapImageRep(data: tiff),
                  let png = rep.representation(using: .png, properties: [:])
            else { continue }
            let path = dir + "/\(batch)-\(i + 1).png"
            do {
                try png.write(to: URL(fileURLWithPath: path), options: .atomic)
                paths.append(path)
            } catch {
                NSLog("attachment write failed: \(error.localizedDescription)")
            }
        }
        return paths
    }

    /// Best-effort cleanup of just-saved PNGs when the inbox write failed —
    /// a retry saves a fresh batch, so these would be permanent orphans
    /// (actd's daily attachments GC is only the backstop).
    static func deleteFiles(_ paths: [String]) {
        for p in paths {
            try? FileManager.default.removeItem(atPath: p)
        }
    }

    /// §39 attachment-line prefix. The python side defines the SAME literal
    /// (act/actd.py, ANSWER_ATTACHMENT_PREFIX) to scrub these machine-
    /// generated lines — they carry local absolute paths — out of the
    /// capture_input-gated analytics text; keep the two in sync.
    static let answerLinePrefix = "[附图，用 Read 工具查看] "

    /// §39 answer channel stays plain text — attachments ride as trailing
    /// lines the agent can Read. One line per saved PNG; the format is agent
    /// payload (like the rework standing order), deliberately not L()-ized.
    static func answerLines(_ paths: [String]) -> String {
        paths.map { answerLinePrefix + $0 }.joined(separator: "\n")
    }

    /// Recover the PNG paths from an answer's 附图 lines — the failed-submit
    /// cleanup only has the final text in hand (promptAnswer already returned).
    static func answerAttachmentPaths(in text: String) -> [String] {
        text.components(separatedBy: "\n").compactMap { line in
            line.hasPrefix(answerLinePrefix)
                ? String(line.dropFirst(answerLinePrefix.count)) : nil
        }
    }
}

/// Thumbnail strip shared by the three inputs. Renders nothing when empty
/// unless `showsHintWhenEmpty`: the NSAlert accessories reserve a fixed-height
/// slot (NSAlert never re-lays-out mid-modal, so the row must not change the
/// panel's height when the first image lands) and show the ⌘V hint in it.
struct PastedImagesRow: View {
    @ObservedObject var model: PastedImagesModel
    var showsHintWhenEmpty = false

    var body: some View {
        if model.isEmpty {
            if showsHintWhenEmpty {
                Text(L("可 ⌘V 粘贴图片（最多 \(PastedImagesModel.maxCount) 张，仅保存在本机）",
                       "⌘V pastes images (up to \(PastedImagesModel.maxCount); kept on this Mac)"))
                    .font(.system(size: 10))
                    .foregroundColor(.secondary.opacity(0.7))
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        } else {
            HStack(spacing: 8) {
                ForEach(model.items) { item in
                    ZStack(alignment: .topTrailing) {
                        Image(nsImage: item.image)
                            .resizable()
                            .aspectRatio(contentMode: .fill)
                            .frame(width: 40, height: 40)
                            .clipShape(RoundedRectangle(cornerRadius: 6))
                            .overlay(RoundedRectangle(cornerRadius: 6)
                                .stroke(Color.primary.opacity(0.15)))
                        Button {
                            model.remove(item.id)
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .font(.system(size: 12))
                                .foregroundColor(.secondary)
                                .background(Circle().fill(Color(nsColor: .windowBackgroundColor)))
                        }
                        .buttonStyle(.plain)
                        .help(L("移除这张图", "Remove this image"))
                        .offset(x: 5, y: -5)
                    }
                    // room so the offset ✕ stays inside the row's hit bounds
                    .padding(.top, 5)
                    .padding(.trailing, 5)
                }
                Spacer(minLength: 0)
            }
        }
    }
}

/// NSTextView that diverts ⌘V to the images model when the pasteboard holds
/// image content; every other paste runs the inherited path. Used by the
/// NSAlert editors (提建议 / 回答) — the SwiftUI composer can't subclass its
/// shared field editor, so it uses PasteImageKeyMonitor instead.
final class ImagePasteTextView: NSTextView {
    var imagesModel: PastedImagesModel?

    override func paste(_ sender: Any?) {
        if let model = imagesModel, model.takeFromPasteboard() { return }
        super.paste(sender)
    }
}

/// Reports the hosting NSWindow into the model (PastedImagesModel.hostWindow)
/// so the ⌘V monitor can verify an event belongs to ITS window. Setting a
/// plain (non-@Published) reference during attach is deliberate — no SwiftUI
/// state mutation mid-layout, and the monitor reads it live at event time.
struct HostingWindowReader: NSViewRepresentable {
    let model: PastedImagesModel

    func makeNSView(context: Context) -> WindowProbeView {
        let v = WindowProbeView()
        v.model = model
        return v
    }
    func updateNSView(_ nsView: WindowProbeView, context: Context) {}

    final class WindowProbeView: NSView {
        weak var model: PastedImagesModel?
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            model?.hostWindow = window
        }
    }
}

/// ⌘V hook for SwiftUI TextFields (the composer): a focus-scoped local key
/// monitor — installed while the field owns the caret, removed on defocus /
/// disappear. Local monitors fire BEFORE the Edit menu's ⌘V key equivalent,
/// so an image paste never reaches the field editor; anything else passes
/// through untouched (shiftReturnMonitor 同款红线).
enum PasteImageKeyMonitor {
    static func install(for model: PastedImagesModel) -> Any? {
        NSEvent.addLocalMonitorForEvents(matching: .keyDown) { event in
            let mods = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
            guard mods.contains(.command),
                  mods.isDisjoint(with: [.shift, .option, .control]),
                  event.charactersIgnoringModifiers?.lowercased() == "v"
            else { return event }
            var handled = false
            MainActor.assumeIsolated {
                // a modal alert owns its own paste path (ImagePasteTextView);
                // a monitor left alive by a stale focus state must never
                // steal the alert's ⌘V into the composer's model.
                guard NSApp.modalWindow == nil else { return }
                // only the KEY window's own composer may claim a paste — a
                // stale monitor (focused state alive in a non-key window)
                // must pass the event through untouched (hostWindow doc).
                guard let owner = model.hostWindow,
                      event.window === owner,
                      NSApp.keyWindow === owner else { return }
                handled = model.takeFromPasteboard()
            }
            return handled ? nil : event
        }
    }

    static func remove(_ monitor: Any?) {
        if let monitor { NSEvent.removeMonitor(monitor) }
    }
}

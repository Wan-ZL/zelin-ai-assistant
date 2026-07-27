// PastedImages.swift — 输入框贴图（用户建议 #4/#5）：提建议弹窗、快速捕获/
// 直接开跑输入框、回答弹窗三处共用的粘贴收集器 + 缩略图行。
//
// 交互契约：⌘V 认领规则（isImagePaste）——图片文件 URL = 明确的贴图意图，
// 无条件认领（旁带文件名字符串也照收）；纯位图（截图）认领；**位图 + 文本双
// flavor** 分两档：单行 URL/文件路径形态的文本（浏览器「拷贝图像」、微信等
// 聊天工具的截图——位图旁几乎总带图片地址/文件引用）= 图片的伴生元数据，
// 认领贴图；多行或实质性非 URL 文本（Excel/Numbers 复制单元格连图带字）不
// 认领，文本粘贴优先——否则这类文本在输入框里彻底无法粘贴。确定性旁路：
// **⌥⌘V** 与缩略图行常驻的 **📎 按钮** = 强制贴图（force，跳过文本让路判定，
// 剪贴板无图只 beep）。一旦认领，事件必被吞掉——满员/解码失败也只
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

    /// 瞬时提示（3 秒）：⌘V 走了文本优先、但剪贴板里其实还有位图——Row 借它
    /// 提示「点 📎 或 ⌥⌘V 贴图」。composer 的 monitor 路径专用（NSAlert
    /// 编辑器的 📎 按钮常驻可见，不额外提示）。
    @Published private(set) var clipboardImageHint = false
    private var hintDismissal: DispatchWorkItem?

    var isEmpty: Bool { items.isEmpty }
    var images: [NSImage] { items.map { $0.image } }

    /// ⌘V handler. Returns true when the pasteboard is an image paste
    /// (isImagePaste's 认领规则) — the caller must then swallow the event,
    /// ALWAYS: at capacity (or on a decode failure) nothing is added and beep
    /// says "not taken", but the event never falls back to text paste — a
    /// Finder image file's text fallback would insert its local ABSOLUTE PATH
    /// into the draft (feedback text uploads → path leak). false = not an
    /// image paste, text paste proceeds untouched.
    /// `force`（⌥⌘V / 📎 按钮）= 用户明说「我要贴图」：跳过 isImagePaste 的
    /// 文本让路判定，剪贴板里有可读图片 flavor 就收（上限/降采样照旧），
    /// 没有则 beep 返回 false——force 的 false 只表示「没图可贴」，调用方
    /// 不得回退文本粘贴。
    @discardableResult
    func takeFromPasteboard(_ pb: NSPasteboard = .general,
                            force: Bool = false) -> Bool {
        if force {
            guard PastedImages.hasReadableImage(pb) else {
                NSSound.beep()   // chord/按钮语义就是要图，无图 beep 作答
                return false
            }
        } else if !PastedImages.isImagePaste(pb) {
            return false
        }
        let room = Self.maxCount - items.count
        let read = room > 0 ? PastedImages.readImages(from: pb) : []
        if read.isEmpty || read.count > room {
            NSSound.beep()   // full / decode failed / overflow truncated
        }
        for img in read.prefix(max(0, room)) {
            items.append(Item(image: img))
        }
        if !read.isEmpty {
            hintDismissal?.cancel()
            clipboardImageHint = false   // 图已贴上，指路提示即刻退场
        }
        return true
    }

    /// 亮起 3 秒的「剪贴板里还有图片」提示；重复触发重置倒计时。
    func flashClipboardImageHint() {
        clipboardImageHint = true
        hintDismissal?.cancel()
        let work = DispatchWorkItem { [weak self] in
            self?.clipboardImageHint = false
        }
        hintDismissal = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 3, execute: work)
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
    /// - 位图 + 文本双 flavor 分两档（两个真实场景）：
    ///   1. 浏览器「拷贝图像」/微信等聊天工具的截图——位图旁几乎总带一段
    ///      单行 URL/文件路径（图片地址、file 引用）。那是图片的伴生元数据，
    ///      **认领**贴图（此前一刀切让路，最常见的真贴图被放走 = 生产事故）；
    ///   2. Excel/Numbers 复制单元格（连图带字）——多行或实质性非 URL 文本，
    ///      **不认领**，文本粘贴优先，否则这类文本彻底无法粘贴。
    static func isImagePaste(_ pb: NSPasteboard) -> Bool {
        if pb.canReadObject(forClasses: [NSURL.self], options: urlReadingOptions) {
            return true
        }
        guard pb.canReadObject(forClasses: [NSImage.self], options: [:]) else {
            return false
        }
        let text = (pb.string(forType: .string) ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return text.isEmpty || isCompanionURLText(text)
    }

    /// 位图旁带文本的判别：trim 后单行、URL/文件路径前缀（http(s):// 、
    /// file:// 或 /）且 ≤2048 字符 = 图片的伴生元数据；其余算实质性文本。
    private static func isCompanionURLText(_ trimmed: String) -> Bool {
        guard trimmed.count <= 2048,
              !trimmed.contains("\n"), !trimmed.contains("\r")
        else { return false }
        return trimmed.hasPrefix("http://") || trimmed.hasPrefix("https://")
            || trimmed.hasPrefix("file://") || trimmed.hasPrefix("/")
    }

    /// force 贴图（⌥⌘V / 📎 按钮）的探针：剪贴板里有没有可读的图片 flavor
    /// （图片文件 URL 或位图），完全不看文本 flavor。
    static func hasReadableImage(_ pb: NSPasteboard) -> Bool {
        pb.canReadObject(forClasses: [NSURL.self], options: urlReadingOptions)
            || pb.canReadObject(forClasses: [NSImage.self], options: [:])
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

/// Thumbnail strip shared by the three inputs. 常驻一个 📎 强制贴图按钮
/// （= takeFromPasteboard(force:)，剪贴板无图 beep），空态也在——它是文本
/// 让路判定误伤时的确定性入口。`showsHintWhenEmpty`（NSAlert accessories，
/// 固定高度槽位——NSAlert never re-lays-out mid-modal）空态附带 ⌘V/⌥⌘V
/// 提示文案；clipboardImageHint 亮时两种状态都插 3 秒指路提示。
struct PastedImagesRow: View {
    @ObservedObject var model: PastedImagesModel
    var showsHintWhenEmpty = false

    var body: some View {
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
            pasteButton
            if model.clipboardImageHint {
                hint(L("剪贴板里还有图片：点 📎 或 ⌥⌘V 贴图",
                       "The clipboard also holds an image: click 📎 or press ⌥⌘V"))
            } else if model.isEmpty && showsHintWhenEmpty {
                hint(L("可 ⌘V 粘贴图片（最多 \(PastedImagesModel.maxCount) 张，仅保存在本机），或按 ⌥⌘V 强制贴图",
                       "⌘V pastes images (up to \(PastedImagesModel.maxCount); kept on this Mac); ⌥⌘V force-pastes"))
            }
            Spacer(minLength: 0)
        }
    }

    /// 常驻的强制贴图入口——与 ⌥⌘V 同一条路：剪贴板有可读图就收，无图 beep。
    private var pasteButton: some View {
        Button {
            model.takeFromPasteboard(force: true)
        } label: {
            Image(systemName: "paperclip")
                .font(.system(size: 12))
                .foregroundColor(.secondary)
        }
        .buttonStyle(.plain)
        .help(L("把剪贴板中的图片贴进来（⌥⌘V）",
                "Paste the clipboard image (⌥⌘V)"))
    }

    private func hint(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 10))
            .foregroundColor(.secondary.opacity(0.7))
            .lineLimit(2)
    }
}

/// NSTextView that diverts ⌘V to the images model when the pasteboard holds
/// image content, and takes ⌥⌘V as the force-paste chord; every other paste
/// runs the inherited path. Used by the NSAlert editors (提建议 / 回答) — the
/// SwiftUI composer can't subclass its shared field editor, so it uses
/// PasteImageKeyMonitor instead.
final class ImagePasteTextView: NSTextView {
    var imagesModel: PastedImagesModel?

    override func paste(_ sender: Any?) {
        if let model = imagesModel {
            // 按住 ⌥ 触发的粘贴（菜单派发到 paste(_:) 时事件还在手上）
            // 一律走强制贴图——⌥⌘V 语义的兜底路径。
            let mods = NSApp.currentEvent?.modifierFlags
                .intersection(.deviceIndependentFlagsMask) ?? []
            if mods.contains(.option) {
                model.takeFromPasteboard(force: true)
                return
            }
            if model.takeFromPasteboard() { return }
        }
        super.paste(sender)
    }

    /// ⌥⌘V 强制贴图 chord。它不匹配 Edit 菜单 ⌘V 的 key equivalent，NSAlert
    /// 模态下没人接手，只能在这里截；无论收没收（无图 beep 作答）都返回
    /// true 吞掉——这个 chord 在输入框里没有别的合法含义。
    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        let mods = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        if let model = imagesModel,
           mods.contains(.command), mods.contains(.option),
           mods.isDisjoint(with: [.shift, .control]),
           event.charactersIgnoringModifiers?.lowercased() == "v" {
            model.takeFromPasteboard(force: true)
            return true
        }
        return super.performKeyEquivalent(with: event)
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

/// ⌘V / ⌥⌘V hook for SwiftUI TextFields (the composer): a focus-scoped local
/// key monitor — installed while the field owns the caret, removed on defocus /
/// disappear. Local monitors fire BEFORE the Edit menu's ⌘V key equivalent,
/// so an image paste never reaches the field editor; anything else passes
/// through untouched (shiftReturnMonitor 同款红线).
enum PasteImageKeyMonitor {
    static func install(for model: PastedImagesModel) -> Any? {
        NSEvent.addLocalMonitorForEvents(matching: .keyDown) { event in
            let mods = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
            guard mods.contains(.command),
                  mods.isDisjoint(with: [.shift, .control]),
                  event.charactersIgnoringModifiers?.lowercased() == "v"
            else { return event }
            let force = mods.contains(.option)   // ⌥⌘V = 强制贴图
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
                if force {
                    // 无论收没收（无图 beep 作答）都吞掉——⌥⌘V 是本 app 的
                    // 贴图 chord，落进输入框没有别的合法含义。
                    model.takeFromPasteboard(force: true)
                    handled = true
                } else {
                    handled = model.takeFromPasteboard()
                    // ⌘V 让路给文本、但剪贴板确有图：亮 3 秒提示指路
                    // 📎 / ⌥⌘V（NSAlert 路径按钮常驻可见，不需要）。
                    if !handled, PastedImages.hasReadableImage(.general) {
                        model.flashClipboardImageHint()
                    }
                }
            }
            return handled ? nil : event
        }
    }

    static func remove(_ monitor: Any?) {
        if let monitor { NSEvent.removeMonitor(monitor) }
    }
}

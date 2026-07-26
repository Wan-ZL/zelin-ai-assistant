// PastedImages.swift — 输入框贴图（用户建议 #4/#5）：提建议弹窗、快速捕获/
// 直接开跑输入框、回答弹窗三处共用的粘贴收集器 + 缩略图行。
//
// 交互契约：⌘V 时剪贴板里有位图或图片文件 URL 才收下（其余剪贴板内容一律走
// 原有文本粘贴路径，绝不拦截）；缩略图行每张可 ✕ 移除；上限 4 张。发送时由
// 调用方把图片落成 PNG（savePNGs → <uuid>-<n>.png）并把绝对路径写进 inbox
// action —— 图片本身永不上传，路径只在本机管线里流转（feedback 记录 / 卡片
// execution.attachments / answer 附图行）。

import AppKit
import SwiftUI
import Foundation

/// Collected pasted images for ONE input surface. Each of the three inputs
/// owns its own instance (a draft's images must never leak into another box).
@MainActor
final class PastedImagesModel: ObservableObject {
    /// 三处输入统一的上限；满员后的图片粘贴不再收（takeFromPasteboard 返回
    /// false，事件走回普通文本粘贴 —— 对位图这是无害的 no-op，不吞事件）。
    static let maxCount = 4

    struct Item: Identifiable {
        let id = UUID()
        let image: NSImage
    }

    @Published private(set) var items: [Item] = []

    var isEmpty: Bool { items.isEmpty }
    var images: [NSImage] { items.map { $0.image } }

    /// ⌘V hook: appends the pasteboard's bitmap / image-file content up to
    /// the cap. Returns true when at least one image was taken — the caller
    /// then swallows the paste; false = not an image paste (or already full),
    /// so the normal text paste must proceed untouched.
    @discardableResult
    func takeFromPasteboard(_ pb: NSPasteboard = .general) -> Bool {
        guard items.count < Self.maxCount else { return false }
        let read = PastedImages.readImages(from: pb)
        guard !read.isEmpty else { return false }
        for img in read.prefix(Self.maxCount - items.count) {
            items.append(Item(image: img))
        }
        return true
    }

    func remove(_ id: UUID) { items.removeAll { $0.id == id } }
    func clear() { items.removeAll() }
}

@MainActor
enum PastedImages {
    /// Pasteboard → images. Only two flavors count: image FILE URLs (Finder
    /// copy — content-checked against public.image, so a .txt path stays a
    /// text paste) and in-memory bitmaps (截图 / 浏览器图片复制，即使旁边还
    /// 带着 URL 文本也按图收 —— 聊天框的惯例). Everything else returns [].
    static func readImages(from pb: NSPasteboard) -> [NSImage] {
        let urlOpts: [NSPasteboard.ReadingOptionKey: Any] = [
            .urlReadingFileURLsOnly: true,
            .urlReadingContentsConformToTypes: ["public.image"],
        ]
        if let urls = pb.readObjects(forClasses: [NSURL.self],
                                     options: urlOpts) as? [URL],
           !urls.isEmpty {
            return urls.compactMap { NSImage(contentsOf: $0) }
        }
        if let imgs = pb.readObjects(forClasses: [NSImage.self],
                                     options: [:]) as? [NSImage] {
            return imgs
        }
        return []
    }

    /// Flatten to PNG under `dir` as <uuid>-<n>.png (one uuid per send batch,
    /// n = 1-based position); returns the absolute paths actually written.
    /// Best-effort per image — a broken bitmap is skipped, never fails the
    /// whole send (the text must still go out).
    static func savePNGs(_ images: [NSImage], toDir dir: String) -> [String] {
        guard !images.isEmpty else { return [] }
        try? FileManager.default.createDirectory(atPath: dir,
                                                 withIntermediateDirectories: true)
        let batch = UUID().uuidString
        var paths: [String] = []
        for (i, image) in images.enumerated() {
            guard let tiff = image.tiffRepresentation,
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

    /// §39 answer channel stays plain text — attachments ride as trailing
    /// lines the agent can Read. One line per saved PNG; the format is agent
    /// payload (like the rework standing order), deliberately not L()-ized.
    static func answerLines(_ paths: [String]) -> String {
        paths.map { "[附图，用 Read 工具查看] " + $0 }.joined(separator: "\n")
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
                handled = model.takeFromPasteboard()
            }
            return handled ? nil : event
        }
    }

    static func remove(_ monitor: Any?) {
        if let monitor { NSEvent.removeMonitor(monitor) }
    }
}

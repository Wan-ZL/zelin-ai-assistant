// FoldNote.swift — §38 fold-note line parsing (折叠进来的信息 / 拆成新卡).
// SHARED between the Mac app and the iOS app (Foundation-only by contract —
// mac/build.sh lint gate); pure value logic so the contract harness
// (ios/tests/contract) can pin it with plain swiftc.
//
// Python 侧 registry.append_fold_note 往 notes 写
// "[radar|quick] <text> [@<ts>]"（拆出后再追加 " [已拆出 R-yyy]"），dashboard
// 以 notes_text 投影（§38: 行对齐 TAIL 截断，本 parser 只会见到完整行）。
// 这里的解析必须与 act/lib/registry.py 的
// _FOLD_LINE_RE/_FOLD_TS_RE/_FOLD_SPLIT_RE 保持 lockstep——两边同时改。

import Foundation

struct FoldNote: Hashable {
    let kind: String        // "radar" | "quick"
    let text: String
    let ts: String?         // split handle；nil = §38 之前的旧行（不可拆）
    let splitInto: String?  // 已拆出 → 新卡 id

    static func parse(_ notes: String?) -> [FoldNote] {
        guard let notes, !notes.isEmpty else { return [] }
        var out: [FoldNote] = []
        for raw in notes.components(separatedBy: "\n") {
            var line = raw.trimmingCharacters(in: .whitespaces)
            var kind: String?
            for k in ["radar", "quick"] where line.hasPrefix("[\(k)] ") {
                kind = k
                line = String(line.dropFirst(k.count + 3))
                break
            }
            guard let kind else { continue }
            var splitInto: String?
            if line.hasSuffix("]"),
               let r = line.range(of: " [已拆出 ", options: .backwards),
               !line[r.upperBound...].dropLast().contains("]") {
                splitInto = String(line[r.upperBound...].dropLast())
                line = String(line[..<r.lowerBound])
            }
            var ts: String?
            if line.hasSuffix("]"),
               let r = line.range(of: " [@", options: .backwards) {
                let tag = line[r.upperBound...].dropLast()
                // the ts tag never contains spaces/brackets — a "]"-ending
                // note text must not be mistaken for one.
                if !tag.isEmpty, !tag.contains(" "), !tag.contains("]") {
                    ts = String(tag)
                    line = String(line[..<r.lowerBound])
                }
            }
            out.append(FoldNote(kind: kind,
                                text: line.trimmingCharacters(in: .whitespaces),
                                ts: ts, splitInto: splitInto))
        }
        return out
    }
}

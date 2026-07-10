// SettingsWeeklyDigest.swift — 设置 · 每周摘要 (weekly ingest digest, CONTRACT §24)
// Self-contained section: the toggle writes settings_overrides.json immediately
// (read-merge-write, no form Save button — P0-7 semantics), and "现在生成一份"
// drops a {"action":"weekly_digest_now"} inbox file that actd turns into a
// detached `python -m act.weekly_digest --now` run.

import AppKit
import SwiftUI

struct WeeklyDigestSettingsSection: View {
    @ObservedObject private var i18n = LanguageStore.shared
    @State private var enabled = true
    @State private var status = ""
    @State private var loaded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L("每周摘要", "Weekly digest"))
                .font(.system(size: 13, weight: .semibold))

            Toggle(L("每周自动生成「本周你都在忙什么」+ 自动化建议",
                     "Auto-generate a weekly \"what you were up to\" recap + automation ideas"),
                   isOn: Binding(
                    get: { enabled },
                    set: { v in
                        enabled = v
                        saveEnabled(v)
                    }))
                .toggleStyle(.switch)
                .font(.system(size: 12))

            HStack(spacing: 10) {
                Button(L("现在生成一份", "Generate now")) { generateNow() }
                    .controlSize(.small)
                if !status.isEmpty {
                    Text(status)
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                }
                Spacer()
            }

            Text(L("读取最近 7 天的 ingest 笔记：摘要卡片会出现在「待验收」，自动化建议进「待审批」。没有新数据时会自动跳过，不花钱。",
                   "Reads the last 7 days of ingest notes: the recap lands in the Review lane, automation ideas in Approvals. Skips (free) when there is no new data."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
            Text(L("本区改动即时生效，不用点下方的保存。",
                   "Changes in this section apply immediately — no Save needed."))
                .font(.system(size: 10))
                .foregroundColor(.secondary)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(Color(nsColor: .controlBackgroundColor)))
        .onAppear {
            if !loaded {
                load()
                loaded = true
            }
        }
    }

    private func load() {
        let ov = SettingsIO.readOverrides()
        enabled = (ov["weekly_digest_enabled"] as? Bool) ?? true
    }

    /// Immediate persist (read-merge-write): only this key is touched, so
    /// out-of-form overrides survive — same pattern as the iMessage section.
    private func saveEnabled(_ v: Bool) {
        var merged = SettingsIO.readOverrides()
        if v {
            // true == the product default -> drop the override key entirely
            merged.removeValue(forKey: "weekly_digest_enabled")
        } else {
            merged["weekly_digest_enabled"] = false
        }
        do {
            try SettingsIO.writeOverrides(merged)
            status = v ? L("已开启", "Enabled") : L("已关闭", "Disabled")
            Analytics.log("weekly_digest_toggle", fields: ["on": v])
        } catch {
            status = L("保存失败，请再试一次：", "Save failed — try again: ")
                + error.localizedDescription
        }
    }

    /// Writes state/inbox/<uuid>.json {"action":"weekly_digest_now"} — the
    /// same contract surface as card actions (app writes inbox, actd reads).
    private func generateNow() {
        let fm = FileManager.default
        do {
            try fm.createDirectory(atPath: AppPaths.inboxDir,
                                   withIntermediateDirectories: true)
            let dict: [String: Any] = [
                "action": "weekly_digest_now",
                "ts": ISO8601DateFormatter().string(from: Date()),
            ]
            let data = try JSONSerialization.data(withJSONObject: dict,
                                                  options: [.prettyPrinted, .sortedKeys])
            let path = AppPaths.inboxDir + "/" + UUID().uuidString + ".json"
            try data.write(to: URL(fileURLWithPath: path), options: .atomic)
            status = L("已请求生成——完成后会弹通知，摘要出现在「待验收」。",
                       "Requested — you'll get a notification; the recap appears in the Review lane.")
            Analytics.log("weekly_digest_generate_now")
        } catch {
            status = L("没能写入请求（磁盘问题），请再点一次：",
                       "Could not write the request (disk issue) — try again: ")
                + error.localizedDescription
        }
    }
}

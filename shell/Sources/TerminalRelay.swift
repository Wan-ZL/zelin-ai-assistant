// TerminalRelay.swift — §68.7 terminal-launch queue consumer + shell heartbeat
// (CONTRACT §68.7 / §68.13 2026-09-05 追记, issue #216).
//
// The server never opens a terminal itself any more (its `.command` + `open -a`
// channel drew an "Allow Ghostty to execute …?" prompt per launch on macOS 26).
// Instead `server/terminal_launch.py` writes ONE JSON file per launch request
// into state/terminal_queue/ (atomic .json.tmp + rename — the same shape as
// the §28 notify_queue) and this relay drains it on a 1 s tick: each fresh
// entry's `shell_line` goes to TerminalLauncher (Apple Events → Ghostty /
// iTerm2 / Terminal; Automation consent is remembered per (shell, terminal)
// pair), and the consumed file is deleted (consume-on-launch keeps the queue
// empty). Entries older than `staleAfter` are deleted UNLAUNCHED — a terminal
// popping up a minute after a double-click the user has forgotten about is
// worse than nothing; the server sweeps with the same threshold on its side.
//
// Heartbeat: the server has to know whether anyone is draining the queue
// (no consumer → 503 SHELL_UNAVAILABLE → the page degrades to "copy the
// command"). The shell touches state/shell.heartbeat on its 5 s engine tick
// and removes it on quit; the server treats mtime within 15 s as alive
// (server/terminal_launch.HEARTBEAT_FRESH_S).
//
// Trust boundary: the queue lives inside the app home (same trust level as
// dashboard.json / notify_queue); its writer is the server, its command text
// is server-derived from the card projection. Nothing here validates the
// shell line beyond shape — see TerminalLauncher's SECURITY note.

import Foundation

enum TerminalRelay {
    static var queueDir: String { AppPaths.stateRoot + "/state/terminal_queue" }

    /// Entries older than this are dropped unlaunched (server side sweeps the
    /// same age: server/terminal_launch.STALE_AFTER_S).
    static let staleAfter: TimeInterval = 60

    /// Queue tick — separate from the 5 s engine tick: a double-click should
    /// open the terminal within a second, and listing an (almost always
    /// empty) directory once a second is negligible.
    static let tickInterval: TimeInterval = 1.0

    struct Entry: Equatable {
        let path: String
        let id: String
        let kind: String
        let command: String
        let shellLine: String
        let createdAt: TimeInterval
    }

    /// One queue file → Entry; nil when the shape is wrong (malformed entries
    /// are deleted by drain so the tick does not re-log them forever).
    static func parse(path: String, _ obj: [String: Any]) -> Entry? {
        guard let id = obj["id"] as? String, !id.isEmpty,
              let kind = obj["kind"] as? String,
              let command = obj["command"] as? String,
              let line = obj["shell_line"] as? String, !line.isEmpty,
              let created = obj["created_at"] as? Double
        else { return nil }
        return Entry(path: path, id: id, kind: kind, command: command,
                     shellLine: line, createdAt: created)
    }

    /// Scan → launch fresh entries (oldest first) → delete. `launch` is the
    /// injection seam (the harness records instead of spawning osascript).
    /// Returns the entries that were handed to `launch`, for logging/tests.
    @discardableResult
    static func drain(now: TimeInterval = Date().timeIntervalSince1970,
                      launch: (Entry) -> Void = { TerminalLauncher.launch($0.shellLine) }) -> [Entry] {
        let fm = FileManager.default
        guard let names = try? fm.contentsOfDirectory(atPath: queueDir), !names.isEmpty else { return [] }
        var fresh: [Entry] = []
        // the server writes *.json.tmp then renames — the .json filter never
        // sees a half-written file
        for name in names where name.hasSuffix(".json") {
            let path = queueDir + "/" + name
            guard let data = fm.contents(atPath: path),
                  let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                  let entry = parse(path: path, obj)
            else {
                NSLog("terminal_queue: malformed entry dropped: \(name)")
                try? fm.removeItem(atPath: path)
                continue
            }
            if now - entry.createdAt > staleAfter {
                NSLog("terminal_queue: stale entry dropped: \(entry.id)")
                try? fm.removeItem(atPath: path)
                continue
            }
            fresh.append(entry)
        }
        fresh.sort { $0.createdAt < $1.createdAt }
        for entry in fresh {
            // consume first: even if the launch fails, a request is tried once
            try? fm.removeItem(atPath: entry.path)
            launch(entry)
        }
        return fresh
    }
}

/// state/shell.heartbeat — "the shell is running" for the server (§68.7).
/// Touched on the 5 s engine tick, removed on quit; content is informational
/// (pid), the mtime is the truth.
enum ShellHeartbeat {
    static var path: String { AppPaths.stateRoot + "/state/shell.heartbeat" }

    static func beat(now: Date = Date()) {
        let fm = FileManager.default
        let dir = (path as NSString).deletingLastPathComponent
        try? fm.createDirectory(atPath: dir, withIntermediateDirectories: true)
        if !fm.fileExists(atPath: path) {
            let body = "pid=\(ProcessInfo.processInfo.processIdentifier)\n"
            try? body.write(toFile: path, atomically: true, encoding: .utf8)
        }
        try? fm.setAttributes([.modificationDate: now], ofItemAtPath: path)
    }

    /// Quit = no consumer any more: remove the file so the server flips to
    /// 503 immediately instead of after the 15 s freshness window.
    static func stop() {
        try? FileManager.default.removeItem(atPath: path)
    }
}

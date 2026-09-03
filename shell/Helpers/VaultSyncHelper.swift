// VaultSyncHelper.swift — vault ⇄ repo mirror courier (standalone CLI, no GUI).
//
// WHY THIS EXISTS (claude TCC identity drift, 2026-07-14): the claude CLI now
// installs per-version binaries (~/.local/share/claude/versions/X.Y.Z) and
// macOS TCC keys grants to the REAL binary path — so every CLI update is a new
// TCC identity: the GUI re-prompts for Documents and cron (no UI to prompt)
// dies with `error: An internal error occurred (EPERM)`. 38 consecutive ingest
// failures 07-09→07-13 had this root cause.
//
// Fix: no pipeline process touches ~/Documents anymore. This helper — compiled
// into the app bundle (Contents/MacOS/), SAME bundle id + SAME stable signing
// identity as the menu-bar app ("Zelin AI Engineer Dev", the TCC-safe cert) —
// is the single courier between the Obsidian vault and a repo-local mirror
// (state/vault-mirror/). The user grants Documents access ONCE via the app
// (a normal GUI prompt, no System Settings digging), the grant lands on the
// bundle identity, and this helper reuses it forever — across app updates
// (stable cert) and across every claude/python/node update (they never see
// the vault). claude runs against the mirror only.
//
// Usage (invoked by ingest/process-screenpipe.sh):
//   vault-sync-helper pull --vault <vaultDir> --mirror <mirrorDir>
//   vault-sync-helper push --vault <vaultDir> --mirror <mirrorDir>
//
// pull: mirror := exact copy of vault (rsync -a --delete, minus excludes) and
//       records the inbox manifest (state file next to the mirror).
// push: publishes the processing results back:
//       - every dir except the inbox: rsync -a --update (never deletes vault
//         files — the skill only ADDS/EDITS raw/wiki/change-summary/log)
//       - inbox ("1 - unprocessed/"): deletions are propagated MANIFEST-BASED,
//         not with rsync --delete: a file the user dropped into the vault
//         inbox WHILE claude was processing is not in the mirror, and a blind
//         --delete would destroy it. Only files that (a) were seen at pull
//         time, (b) are gone from the mirror now, and (c) are unmodified in
//         the vault since pull, are removed.
//
// Exit codes: 0 ok · 2 usage · 3 vault unreadable (likely no TCC grant yet —
// callers fall back to legacy direct-vault mode and surface the checkup hint).
//
// Standalone-compilable (Foundation only) — mac/build.sh compiles it like
// framegrab, separate from the app module.

import Foundation

let inboxName = "1 - unprocessed"
// Never mirrored: Obsidian's own workspace/cache (big, volatile, irrelevant
// to ingest) and macOS noise. NOTE .claude/ IS mirrored — the skill reads the
// vault CLAUDE.md privacy posture and skill assets from inside the tree.
let excludes = [".obsidian/", ".DS_Store", ".Trash/"]

func fail(_ msg: String, code: Int32) -> Never {
    FileHandle.standardError.write(("vault-sync-helper: " + msg + "\n").data(using: .utf8)!)
    exit(code)
}

func run(_ tool: String, _ args: [String]) -> Int32 {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: tool)
    p.arguments = args
    // rsync progress noise is useless in the ingest log; keep stderr (errors).
    p.standardOutput = FileHandle.nullDevice
    do { try p.run() } catch { fail("cannot exec \(tool): \(error.localizedDescription)", code: 1) }
    p.waitUntilExit()
    return p.terminationStatus
}

func rsync(_ src: String, _ dst: String, extra: [String]) -> Int32 {
    var args = ["-a"] + extra
    for e in excludes { args += ["--exclude", e] }
    // trailing slashes: copy CONTENTS of src into dst
    args += [src.hasSuffix("/") ? src : src + "/", dst.hasSuffix("/") ? dst : dst + "/"]
    return run("/usr/bin/rsync", args)
}

// MARK: - argv

var vault: String?
var mirror: String?
var mode: String?
var i = 1
let argv = CommandLine.arguments
while i < argv.count {
    switch argv[i] {
    case "pull", "push": mode = argv[i]
    case "--vault": i += 1; vault = i < argv.count ? argv[i] : nil
    case "--mirror": i += 1; mirror = i < argv.count ? argv[i] : nil
    default: fail("unknown argument: \(argv[i])", code: 2)
    }
    i += 1
}
guard let mode, let vault, let mirror else {
    fail("usage: vault-sync-helper pull|push --vault <dir> --mirror <dir>", code: 2)
}

let fm = FileManager.default
let manifestPath = (mirror as NSString).deletingLastPathComponent + "/vault-sync-manifest.txt"

// Readability probe — the FIRST thing that touches the vault. Without the
// bundle's Documents grant this is where TCC says no; exit 3 tells the caller
// "fall back + point the user at the permissions checkup", not "crash".
guard fm.isReadableFile(atPath: vault),
      (try? fm.contentsOfDirectory(atPath: vault)) != nil else {
    fail("vault unreadable at \(vault) — Documents access not granted to the app bundle yet?", code: 3)
}

func inboxListing(_ dir: String) -> [String: Date] {
    var out: [String: Date] = [:]
    for name in (try? fm.contentsOfDirectory(atPath: dir)) ?? [] {
        if name.hasPrefix(".") { continue }
        let p = dir + "/" + name
        var isDir: ObjCBool = false
        guard fm.fileExists(atPath: p, isDirectory: &isDir), !isDir.boolValue else { continue }
        let mtime = (try? fm.attributesOfItem(atPath: p)[.modificationDate] as? Date) ?? nil
        out[name] = mtime ?? .distantPast
    }
    return out
}

switch mode {
case "pull":
    try? fm.createDirectory(atPath: mirror, withIntermediateDirectories: true)
    // exact snapshot: a previous failed run's leftovers in the mirror are
    // wiped, so every processing round starts from vault truth.
    guard rsync(vault, mirror, extra: ["--delete"]) == 0 else {
        fail("rsync pull failed", code: 1)
    }
    // manifest = inbox files as of pull time ("name\tmtimeEpoch")
    let listing = inboxListing(vault + "/" + inboxName)
    let lines = listing.map { "\($0.key)\t\($0.value.timeIntervalSince1970)" }
    try? lines.joined(separator: "\n").write(toFile: manifestPath, atomically: true, encoding: .utf8)
    exit(0)

case "push":
    // 1) additive publish for everything (new raw/wiki/change-summary/log
    //    files and edits; --update never regresses a newer vault-side edit,
    //    and nothing is ever deleted outside the inbox)
    guard rsync(mirror, vault, extra: ["--update"]) == 0 else {
        fail("rsync push failed", code: 1)
    }
    // 2) manifest-based inbox deletions: the skill consumed these files
    let vaultInbox = vault + "/" + inboxName
    let mirrorInbox = mirror + "/" + inboxName
    let nowInVault = inboxListing(vaultInbox)
    let nowInMirror = Set(((try? fm.contentsOfDirectory(atPath: mirrorInbox)) ?? []).filter { !$0.hasPrefix(".") })
    var manifest: [String: TimeInterval] = [:]
    for line in (try? String(contentsOfFile: manifestPath, encoding: .utf8))?.split(separator: "\n") ?? [] {
        let parts = line.split(separator: "\t", maxSplits: 1)
        if parts.count == 2, let t = Double(parts[1]) { manifest[String(parts[0])] = t }
    }
    var removed = 0
    for (name, pulledMtime) in manifest {
        guard !nowInMirror.contains(name),               // skill consumed it
              let vaultMtime = nowInVault[name] else { continue }  // still in vault
        // untouched since pull? (a user re-save during processing wins — the
        // next round re-ingests the updated file instead of losing the edit)
        guard abs(vaultMtime.timeIntervalSince1970 - pulledMtime) < 1.0 else { continue }
        try? fm.removeItem(atPath: vaultInbox + "/" + name)
        removed += 1
    }
    print("vault-sync-helper: push ok (\(removed) consumed inbox file(s) removed)")
    exit(0)

default:
    fail("unknown mode \(mode)", code: 2)
}

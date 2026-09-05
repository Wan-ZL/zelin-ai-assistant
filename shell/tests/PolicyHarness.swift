// PolicyHarness.swift — behavior tests for the shell window policies in
// shell/Sources/ShellSupport.swift (CONTRACT §54 追记: 外链交系统浏览器 / Dock 重开只看
// 看板窗口 / 标题跟随页面). Compiled by run.sh together with every shell source
// except main.swift into a plain macOS CLI tool — no Xcode, no XCTest, no
// WKWebView, no NSWindow. Exits non-zero on any failure. Same harness style as
// BridgeHarness.swift (which stays the home of the `zaiShell` wire contract).
//
// Why pure policies: AppDelegate's WKUIDelegate / WKNavigationDelegate /
// applicationShouldHandleReopen callbacks cannot be driven without a running
// app, so main.swift only asks these policies and performs the side effect
// (webView.load / NSWorkspace.open / showWindow). Every cell of each policy is
// pinned here.

import Foundation

var allOK = true
func check(_ cond: Bool, _ label: String, _ detail: String = "") {
    if cond { print("  PASS \(label)") }
    else { print("  FAIL \(label) \(detail)"); allOK = false }
}

func run() {
    let port = 47821   // a non-default port so the tests cannot pass by accident

    // ---- 1. ExternalLinkPolicy.classify ----
    print("[1] ExternalLinkPolicy.classify:")
    func verdict(_ s: String) -> ExternalLinkPolicy.Verdict {
        ExternalLinkPolicy.classify(URL(string: s), port: port)
    }
    // board origin: loopback host + the configured port, any path / query / fragment
    check(verdict("http://127.0.0.1:47821/") == .board, "board root → board")
    check(verdict("http://127.0.0.1:47821/?page=settings&anchor=live_captions") == .board,
          "board deep link (?page=) → board")
    check(verdict("http://127.0.0.1:47821/api/board#x") == .board, "board API path → board")
    check(verdict("http://localhost:47821/") == .board, "localhost + port → board")
    check(verdict("http://[::1]:47821/") == .board, "IPv6 loopback + port → board",
          String(describing: verdict("http://[::1]:47821/")))
    check(verdict("HTTP://127.0.0.1:47821/") == .board, "scheme compared case-insensitively")
    // same host, other port = some other local service, not the board
    check(verdict("http://127.0.0.1:47820/") == .external, "loopback on another port → external")
    check(verdict("http://127.0.0.1/") == .external, "loopback on the default port 80 → external")
    check(verdict("https://127.0.0.1:47821/") == .external,
          "https on the board port → external (the board is plain http)")
    // the 16 web call sites: dependency download pages, GitHub PRs, release page, docs
    check(verdict("https://claude.com/claude-code") == .external, "claude.com → external")
    check(verdict("https://nodejs.org") == .external, "nodejs.org (no path) → external")
    check(verdict("https://ffmpeg.org/download.html") == .external, "ffmpeg.org → external")
    check(verdict("https://github.com/Wan-ZL/zelin-ai-assistant/pull/1") == .external,
          "GitHub PR link → external")
    check(verdict("http://example.com:47821/") == .external,
          "board port on a non-loopback host → external")
    // non-http schemes the system handler owns (markdown autolink allows mailto)
    check(verdict("mailto:someone@example.com") == .external, "mailto: → external (system handler)")
    check(verdict("file:///Users/x/Library/Logs/zelin-ai-assistant/board-shell.log") == .external,
          "file: → external (system handler)")
    // WebKit-internal / scripty URLs: never open a browser for these
    check(verdict("about:blank") == .ignore, "about:blank → ignore (splash / empty popup)")
    check(verdict("javascript:void(0)") == .ignore, "javascript: → ignore")
    check(verdict("data:text/plain,hi") == .ignore, "data: → ignore")
    check(verdict("blob:http://127.0.0.1:47821/abc") == .ignore, "blob: → ignore")
    check(ExternalLinkPolicy.classify(nil, port: port) == .ignore, "nil URL → ignore")
    check(ExternalLinkPolicy.classify(URL(string: "/relative/only"), port: port) == .ignore,
          "scheme-less URL → ignore")
    // the port argument is honoured (not hard-wired to 47820)
    check(ExternalLinkPolicy.classify(URL(string: "http://127.0.0.1:47820/"), port: 47820) == .board,
          "default port is board when configured so")

    // ---- 2. ReopenPolicy.shouldShow — the four cells; hasVisibleWindows is not an input ----
    print("[2] ReopenPolicy.shouldShow:")
    check(ReopenPolicy.shouldShow(boardVisible: false, boardMiniaturized: false),
          "board closed → show (even while the captions panel keeps hasVisibleWindows true)")
    check(!ReopenPolicy.shouldShow(boardVisible: true, boardMiniaturized: false),
          "board visible → no-op (show is idempotent anyway)")
    check(!ReopenPolicy.shouldShow(boardVisible: false, boardMiniaturized: true),
          "board minimized → no-op (AppKit's default reopen de-minimizes; mirrors isWindowOpen)")
    check(!ReopenPolicy.shouldShow(boardVisible: true, boardMiniaturized: true),
          "both flags → no-op")

    // ---- 3. WindowTitlePolicy.resolve — page title wins, blanks fall back ----
    print("[3] WindowTitlePolicy.resolve:")
    let fallback = "Zelin's AI Assistant"
    check(WindowTitlePolicy.resolve(pageTitle: "Zelin 的 AI 助理 · 看板", fallback: fallback)
          == "Zelin 的 AI 助理 · 看板", "document.title mirrors into the window title verbatim")
    check(WindowTitlePolicy.resolve(pageTitle: "Zelin's AI Assistant · Board", fallback: fallback)
          == "Zelin's AI Assistant · Board", "English page title mirrors too")
    check(WindowTitlePolicy.resolve(pageTitle: nil, fallback: fallback) == fallback,
          "nil title (no page yet) → product name")
    check(WindowTitlePolicy.resolve(pageTitle: "", fallback: fallback) == fallback,
          "empty title (embedded splash HTML) → product name")
    check(WindowTitlePolicy.resolve(pageTitle: " \n\t", fallback: fallback) == fallback,
          "whitespace-only title → product name")
    check(WindowTitlePolicy.resolve(pageTitle: "  Settings  ", fallback: fallback) == "Settings",
          "surrounding whitespace trimmed")
}

run()
print(allOK ? "ALL PASS" : "FAILURES")
exit(allOK ? 0 : 1)

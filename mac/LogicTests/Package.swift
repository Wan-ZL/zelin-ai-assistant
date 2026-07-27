// swift-tools-version:5.9
// LogicTests — mac/Sources 纯逻辑的可执行单测（本地第四道门，CONTRIBUTING.md）。
// The app itself still builds with plain swiftc as ONE module (mac/build.sh —
// no SPM, no Xcode project); this package exists solely to run XCTest over
// individual pure-logic files without dragging in the whole app.
//
// Sources/MacLogic/ contains SYMLINKS into ../Sources and ../../shared/Sources —
// the code under test is the real file, never a copy, so tests can't drift
// from the app. To put another file under test, add a symlink here; the file
// must compile standalone against its Foundation-only helpers (I18n.swift's
// L() rides along the same way). If a file ever needs a heavier dependency
// stubbed, add a clearly-labelled stub FILE in this package — never edit the
// real source to make it testable.
//
// Run: swift test --package-path mac/LogicTests
import PackageDescription

let package = Package(
    name: "LogicTests",
    // .v14: MainActor.assumeIsolated (PasteImageKeyMonitor) needs the
    // macOS 14 SDK floor; the CI macos runner and dev machines are newer.
    platforms: [.macOS(.v14)],
    targets: [
        .target(name: "MacLogic", path: "Sources/MacLogic"),
        .testTarget(name: "MacLogicTests",
                    dependencies: ["MacLogic"],
                    path: "Tests/MacLogicTests"),
    ]
)
